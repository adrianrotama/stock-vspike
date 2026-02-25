#!/usr/bin/env python3
"""
Intraday scan entry point.

Intended to run every 15 minutes during IDX market hours (09:15‑15:00 WIB).

1. Load active spike events from recent daily data.
2. Fetch intraday OHLCV for monitored tickers.
3. Check entry / TP / SL criteria.
4. Send Telegram alerts for triggered signals.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.data.ticker_list import get_idx_tickers
from src.data.market_data import fetch_daily_ohlcv, fetch_intraday_ohlcv
from src.screener.volume_spike import SpikeEvent, latest_spikes, detect_spikes
from src.screener.signal_generator import (
    ActivePosition,
    Signal,
    SignalType,
    check_entry,
    check_exit,
    enrich,
)
from src.notify.telegram import send_signal_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH = config.SIGNALS_DB_PATH


# ── SQLite helpers ───────────────────────────────────────────────────────────

def _init_db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS active_positions (
            ticker TEXT PRIMARY KEY,
            entry_date TEXT,
            entry_price REAL,
            sl_price REAL,
            tp_price REAL,
            spike_json TEXT,
            highest REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            signal_type TEXT,
            date TEXT,
            price REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def _load_positions(conn: sqlite3.Connection) -> Dict[str, ActivePosition]:
    rows = conn.execute("SELECT * FROM active_positions").fetchall()
    positions: Dict[str, ActivePosition] = {}
    for r in rows:
        spike_data = json.loads(r[5])
        spike = SpikeEvent(**{
            **spike_data,
            "date": pd.Timestamp(spike_data["date"]),
        })
        positions[r[0]] = ActivePosition(
            ticker=r[0],
            entry_date=pd.Timestamp(r[1]),
            entry_price=r[2],
            spike_event=spike,
            sl_price=r[3],
            tp_price=r[4],
            highest_since_entry=r[6],
        )
    return positions


def _save_position(conn: sqlite3.Connection, pos: ActivePosition) -> None:
    spike_dict = {
        "ticker": pos.spike_event.ticker,
        "date": str(pos.spike_event.date),
        "rvol": pos.spike_event.rvol,
        "close": pos.spike_event.close,
        "prev_close": pos.spike_event.prev_close,
        "pct_change": pos.spike_event.pct_change,
        "high": pos.spike_event.high,
        "low": pos.spike_event.low,
        "volume": pos.spike_event.volume,
        "avg_txn_value": pos.spike_event.avg_txn_value,
    }
    conn.execute(
        """INSERT OR REPLACE INTO active_positions
           (ticker, entry_date, entry_price, sl_price, tp_price, spike_json, highest)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            pos.ticker, str(pos.entry_date), pos.entry_price,
            pos.sl_price, pos.tp_price, json.dumps(spike_dict),
            pos.highest_since_entry,
        ),
    )
    conn.commit()


def _remove_position(conn: sqlite3.Connection, ticker: str) -> None:
    conn.execute("DELETE FROM active_positions WHERE ticker = ?", (ticker,))
    conn.commit()


def _already_sent(conn: sqlite3.Connection, ticker: str, signal_type: str, date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sent_signals WHERE ticker=? AND signal_type=? AND date=?",
        (ticker, signal_type, date),
    ).fetchone()
    return row is not None


def _mark_sent(conn: sqlite3.Connection, sig: Signal) -> None:
    conn.execute(
        "INSERT INTO sent_signals (ticker, signal_type, date, price) VALUES (?, ?, ?, ?)",
        (sig.ticker, sig.signal_type.name, str(sig.date), sig.price),
    )
    conn.commit()


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=== Intraday IDX Scan ===")

    conn = _init_db()
    positions = _load_positions(conn)

    tickers = get_idx_tickers()

    # gather tickers to monitor: those with recent spikes + open positions
    monitored: Dict[str, pd.DataFrame] = {}
    spikes_by_ticker: Dict[str, SpikeEvent] = {}

    # fetch daily data for spike detection (use cached / quick)
    for ticker in tickers:
        df = fetch_daily_ohlcv(ticker, days=30)
        if df.empty:
            continue
        events = detect_spikes(df, ticker)
        if events:
            latest = max(events, key=lambda e: e.date)
            cutoff = df.index.max() - pd.Timedelta(days=10)
            if latest.date >= cutoff:
                spikes_by_ticker[ticker] = latest
                monitored[ticker] = df

    # also include tickers with open positions
    for ticker in positions:
        if ticker not in monitored:
            df = fetch_daily_ohlcv(ticker, days=30)
            if not df.empty:
                monitored[ticker] = df

    logger.info("Monitoring %d tickers", len(monitored))

    # check signals
    for ticker, df in monitored.items():
        edf = enrich(df)

        # check entry for spike tickers without open position
        if ticker in spikes_by_ticker and ticker not in positions:
            spike = spikes_by_ticker[ticker]
            sig = check_entry(edf, spike)
            if sig and not _already_sent(conn, ticker, "ENTRY", str(sig.date)):
                logger.info("ENTRY signal: %s @ %s", ticker, sig.price)
                send_signal_alert(sig)
                _mark_sent(conn, sig)
                pos = ActivePosition(
                    ticker=ticker,
                    entry_date=sig.date,
                    entry_price=sig.price,
                    spike_event=spike,
                    sl_price=sig.sl_price or 0,
                    tp_price=sig.tp_price,
                    highest_since_entry=sig.price,
                )
                _save_position(conn, pos)
                positions[ticker] = pos

        # check exit for open positions
        if ticker in positions:
            pos = positions[ticker]
            sig = check_exit(edf, pos)
            if sig and not _already_sent(conn, ticker, sig.signal_type.name, str(sig.date)):
                logger.info("%s signal: %s @ %s", sig.signal_type.name, ticker, sig.price)
                send_signal_alert(sig)
                _mark_sent(conn, sig)
                _remove_position(conn, ticker)

    conn.close()
    logger.info("Intraday scan complete")


if __name__ == "__main__":
    main()
