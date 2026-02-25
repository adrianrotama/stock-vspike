"""
Volume‑spike detection with RVOL, price, and confirmation filters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src import config

logger = logging.getLogger(__name__)


@dataclass
class SpikeEvent:
    """A detected volume‑spike day for a single ticker."""

    ticker: str
    date: pd.Timestamp
    rvol: float
    close: float
    prev_close: float
    pct_change: float
    high: float
    low: float
    volume: int
    avg_txn_value: float  # 20‑day avg (volume * close)


def compute_rvol(volume: pd.Series, window: int = config.VOLUME_SMA_WINDOW) -> pd.Series:
    """Relative Volume = volume / SMA(volume, window)."""
    sma = volume.rolling(window).mean()
    return (volume / sma).replace([np.inf, -np.inf], np.nan)


def compute_avg_txn_value(
    df: pd.DataFrame, window: int = config.VOLUME_SMA_WINDOW
) -> pd.Series:
    """Rolling average daily transaction value (volume * close)."""
    return (df["Volume"] * df["Close"]).rolling(window).mean()


def price_position(df: pd.DataFrame) -> pd.Series:
    """Where the close sits within the day's range (0 = low, 1 = high)."""
    rng = df["High"] - df["Low"]
    return ((df["Close"] - df["Low"]) / rng).replace([np.inf, -np.inf], np.nan)


def detect_spikes(
    df: pd.DataFrame,
    ticker: str,
    rvol_threshold: float = config.RVOL_THRESHOLD,
    min_price: float = config.MIN_PRICE,
    min_avg_txn: float = config.MIN_AVG_TXN_VALUE,
    price_pos_min: float = config.PRICE_POSITION_MIN,
) -> List[SpikeEvent]:
    """
    Scan a single ticker's daily OHLCV for volume‑spike days.

    Filters applied (all must be true):
      1. Close >= *min_price*
      2. 20‑day avg transaction value >= *min_avg_txn*
      3. RVOL >= *rvol_threshold*
      4. Green candle (close > open)
      5. Close in upper portion of day range (>= *price_pos_min*)
      6. Close > previous close
    """
    if df.empty or len(df) < config.VOLUME_SMA_WINDOW + 1:
        return []

    df = df.copy()
    df["rvol"] = compute_rvol(df["Volume"])
    df["avg_txn"] = compute_avg_txn_value(df)
    df["price_pos"] = price_position(df)
    df["prev_close"] = df["Close"].shift(1)

    mask = (
        (df["Close"] >= min_price)
        & (df["avg_txn"] >= min_avg_txn)
        & (df["rvol"] >= rvol_threshold)
        & (df["Close"] > df["Open"])         # green candle
        & (df["price_pos"] >= price_pos_min) # close in upper range
        & (df["Close"] > df["prev_close"])   # price rising
    )

    events: List[SpikeEvent] = []
    for idx_label in df.index[mask]:
        row = df.loc[idx_label]
        events.append(
            SpikeEvent(
                ticker=ticker,
                date=pd.Timestamp(idx_label),
                rvol=round(float(row["rvol"]), 2),
                close=float(row["Close"]),
                prev_close=float(row["prev_close"]),
                pct_change=round(
                    (float(row["Close"]) - float(row["prev_close"]))
                    / float(row["prev_close"])
                    * 100,
                    2,
                ),
                high=float(row["High"]),
                low=float(row["Low"]),
                volume=int(row["Volume"]),
                avg_txn_value=float(row["avg_txn"]),
            )
        )

    return events


def scan_all(
    data: Dict[str, pd.DataFrame],
    rvol_threshold: float = config.RVOL_THRESHOLD,
) -> List[SpikeEvent]:
    """
    Run spike detection across all tickers.

    Returns events sorted by RVOL descending.
    """
    all_events: List[SpikeEvent] = []
    for ticker, df in data.items():
        events = detect_spikes(df, ticker, rvol_threshold=rvol_threshold)
        all_events.extend(events)

    all_events.sort(key=lambda e: e.rvol, reverse=True)
    return all_events


def latest_spikes(
    data: Dict[str, pd.DataFrame],
    rvol_threshold: float = config.RVOL_THRESHOLD,
    lookback_days: int = 10,
) -> List[SpikeEvent]:
    """
    Return only recent spike events (within *lookback_days*
    of the latest date in each ticker's data).
    """
    results: List[SpikeEvent] = []
    for ticker, df in data.items():
        if df.empty:
            continue
        cutoff = df.index.max() - pd.Timedelta(days=lookback_days)
        events = detect_spikes(df, ticker, rvol_threshold=rvol_threshold)
        results.extend(e for e in events if e.date >= cutoff)

    results.sort(key=lambda e: e.rvol, reverse=True)
    return results
