"""
Signal generator – entry, take‑profit, and stop‑loss logic.

Works on daily OHLCV data enriched with technical indicators.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import ta

from src import config
from src.screener.volume_spike import SpikeEvent

logger = logging.getLogger(__name__)


class SignalType(IntEnum):
    ENTRY = 1
    TAKE_PROFIT = 2
    STOP_LOSS = 3


class TPMode(IntEnum):
    BREAKOUT = 1       # exit when price > spike‑day high
    MA_BREAKDOWN = 2   # exit when close < EMA after being in profit
    TRAILING = 3       # trailing stop after reaching profit threshold


@dataclass
class Signal:
    ticker: str
    signal_type: SignalType
    date: pd.Timestamp
    price: float
    spike_event: SpikeEvent
    entry_price: Optional[float] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    note: str = ""


@dataclass
class ActivePosition:
    """Tracks a position opened via an ENTRY signal."""
    ticker: str
    entry_date: pd.Timestamp
    entry_price: float
    spike_event: SpikeEvent
    sl_price: float
    tp_price: Optional[float]
    highest_since_entry: float = 0.0


def enrich(df: pd.DataFrame, ema_period: int = config.EMA_PERIOD) -> pd.DataFrame:
    """Add EMA, ATR, and MFI columns to a daily OHLCV DataFrame."""
    out = df.copy()
    out["EMA"] = ta.trend.ema_indicator(out["Close"], window=ema_period)
    out["ATR"] = ta.volatility.average_true_range(
        out["High"], out["Low"], out["Close"], window=config.ATR_PERIOD
    )
    out["MFI"] = ta.volume.money_flow_index(
        out["High"], out["Low"], out["Close"], out["Volume"],
        window=config.MFI_PERIOD,
    )
    out["prev_EMA"] = out["EMA"].shift(1)
    return out


# ── helpers ──────────────────────────────────────────────────────────────────

def _adaptive_sl(
    pre_spike_close: float,
    atr: float,
    sl_pct: float = config.SL_PCT,
    entry_price: float | None = None,
) -> float:
    """
    SL = pre_spike_close - max(sl_pct%, 1 x ATR).

    If the result ends up >= *entry_price* (deep retracement), fall back
    to sl_pct% below the entry price so the SL is always valid.
    """
    pct_dist = pre_spike_close * sl_pct / 100
    sl = pre_spike_close - max(pct_dist, atr)
    if entry_price is not None and sl >= entry_price:
        sl = entry_price * (1 - sl_pct / 100)
    return sl


def _is_near_entry(
    current_close: float,
    pre_spike_close: float,
    retrace_pct: float = config.RETRACE_PCT,
) -> bool:
    """True when price has retraced to within *retrace_pct* of the pre‑spike close."""
    if pre_spike_close <= 0:
        return False
    distance_pct = abs(current_close - pre_spike_close) / pre_spike_close * 100
    return distance_pct <= retrace_pct


def _ema_reclaim(row: pd.Series) -> bool:
    """True when close crosses above EMA from below."""
    return (
        not np.isnan(row.get("prev_EMA", np.nan))
        and row["Close"] > row["EMA"]
        and row.get("prev_close", row["Close"]) <= row.get("prev_EMA", row["EMA"])
    )


# ── public API ───────────────────────────────────────────────────────────────

def check_entry(
    df: pd.DataFrame,
    spike: SpikeEvent,
    retrace_pct: float = config.RETRACE_PCT,
    mfi_min: float = config.MFI_MIN,
) -> Optional[Signal]:
    """
    Check the latest bar for an entry signal following a spike event.

    Criteria:
      1. Price retraced to within *retrace_pct* of the pre‑spike close.
      2. Close crosses above EMA (MA reclaim).
      3. MFI >= *mfi_min* (money still flowing in).
    """
    if df.empty:
        return None

    latest = df.iloc[-1]
    date = df.index[-1]

    if date <= spike.date:
        return None

    near = _is_near_entry(latest["Close"], spike.prev_close, retrace_pct)
    reclaim = _ema_reclaim(latest)
    mfi_ok = latest.get("MFI", 0) >= mfi_min

    if near and reclaim and mfi_ok:
        atr = latest.get("ATR", 0)
        sl = _adaptive_sl(spike.prev_close, atr, entry_price=float(latest["Close"]))
        tp = spike.high  # default: breakout TP

        return Signal(
            ticker=spike.ticker,
            signal_type=SignalType.ENTRY,
            date=pd.Timestamp(date),
            price=float(latest["Close"]),
            spike_event=spike,
            entry_price=float(latest["Close"]),
            sl_price=round(sl, 2),
            tp_price=round(tp, 2),
            note=(
                f"Retrace {abs(latest['Close'] - spike.prev_close) / spike.prev_close * 100:.1f}% "
                f"| EMA reclaim | MFI {latest.get('MFI', 0):.0f}"
            ),
        )
    return None


def check_exit(
    df: pd.DataFrame,
    pos: ActivePosition,
    tp_mode: int = config.TP_MODE,
    trailing_pct: float = config.TRAILING_STOP_PCT,
) -> Optional[Signal]:
    """
    Check the latest bar for exit (TP or SL).
    """
    if df.empty:
        return None

    latest = df.iloc[-1]
    date = df.index[-1]
    close = float(latest["Close"])

    # ── stop loss ────────────────────────────────────────────────────────
    if close <= pos.sl_price:
        return Signal(
            ticker=pos.ticker,
            signal_type=SignalType.STOP_LOSS,
            date=pd.Timestamp(date),
            price=close,
            spike_event=pos.spike_event,
            entry_price=pos.entry_price,
            sl_price=pos.sl_price,
            note=f"SL hit @ {close:,.0f}",
        )

    # ── take profit ──────────────────────────────────────────────────────
    if tp_mode == TPMode.BREAKOUT:
        if close > pos.spike_event.high:
            return Signal(
                ticker=pos.ticker,
                signal_type=SignalType.TAKE_PROFIT,
                date=pd.Timestamp(date),
                price=close,
                spike_event=pos.spike_event,
                entry_price=pos.entry_price,
                tp_price=pos.spike_event.high,
                note=f"Breakout above spike high {pos.spike_event.high:,.0f}",
            )

    elif tp_mode == TPMode.MA_BREAKDOWN:
        in_profit = close > pos.entry_price
        below_ema = close < latest.get("EMA", close + 1)
        if in_profit and below_ema:
            return Signal(
                ticker=pos.ticker,
                signal_type=SignalType.TAKE_PROFIT,
                date=pd.Timestamp(date),
                price=close,
                spike_event=pos.spike_event,
                entry_price=pos.entry_price,
                note=f"MA breakdown (close < EMA) while in profit",
            )

    elif tp_mode == TPMode.TRAILING:
        pos.highest_since_entry = max(pos.highest_since_entry, close)
        trail_price = pos.highest_since_entry * (1 - trailing_pct / 100)
        in_profit = close > pos.entry_price
        if in_profit and close < trail_price:
            return Signal(
                ticker=pos.ticker,
                signal_type=SignalType.TAKE_PROFIT,
                date=pd.Timestamp(date),
                price=close,
                spike_event=pos.spike_event,
                entry_price=pos.entry_price,
                note=f"Trailing stop @ {trail_price:,.0f} (peak {pos.highest_since_entry:,.0f})",
            )

    return None


def find_near_entry_stocks(
    data: Dict[str, pd.DataFrame],
    spikes: List[SpikeEvent],
    retrace_pct: float = config.RETRACE_PCT,
) -> List[dict]:
    """
    For the daily report: list stocks whose current price is near
    the entry zone but haven't triggered full entry yet.
    """
    results = []
    for spike in spikes:
        df = data.get(spike.ticker)
        if df is None or df.empty:
            continue

        edf = enrich(df)
        latest = edf.iloc[-1]

        if latest.name <= spike.date:
            continue

        near = _is_near_entry(latest["Close"], spike.prev_close, retrace_pct)
        if not near:
            continue

        atr = latest.get("ATR", 0)
        sl = _adaptive_sl(spike.prev_close, atr, entry_price=float(latest["Close"]))

        results.append({
            "ticker": spike.ticker,
            "current_close": float(latest["Close"]),
            "pre_spike_close": spike.prev_close,
            "retrace_pct": round(
                abs(latest["Close"] - spike.prev_close) / spike.prev_close * 100, 1
            ),
            "ema_reclaiming": latest["Close"] > latest["EMA"],
            "mfi": round(latest.get("MFI", 0), 1),
            "entry_zone_low": round(spike.prev_close * (1 - retrace_pct / 100), 0),
            "entry_zone_high": round(spike.prev_close, 0),
            "sl": round(sl, 0),
            "tp": round(spike.high, 0),
        })

    return results
