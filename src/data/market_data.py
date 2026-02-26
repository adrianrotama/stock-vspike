"""
yfinance wrapper for fetching IDX OHLCV data.

All IDX tickers use the ``.JK`` suffix on Yahoo Finance.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from src.config import HISTORY_DAYS

logger = logging.getLogger(__name__)


def _yf_ticker(code: str) -> str:
    """Append ``.JK`` if not already present."""
    return code if code.endswith(".JK") else f"{code}.JK"


def fetch_daily_ohlcv(
    ticker: str,
    days: int = HISTORY_DAYS,
    end: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Download daily OHLCV for a single IDX ticker.

    Returns a DataFrame with columns:
    ``Open, High, Low, Close, Volume``  (DatetimeIndex).
    """
    end = end or datetime.now() + timedelta(days=1)
    start = end - timedelta(days=days)
    symbol = _yf_ticker(ticker)

    try:
        df = yf.download(
            symbol,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
    except Exception:
        logger.warning("Failed to download %s", symbol, exc_info=True)
        return pd.DataFrame()

    if df.empty:
        return df

    # yfinance may return multi-level columns when downloading single ticker
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(df.columns):
        logger.warning("%s missing columns: %s", symbol, required - set(df.columns))
        return pd.DataFrame()

    return df[list(required)].copy()


def fetch_bulk_daily(
    tickers: List[str],
    days: int = HISTORY_DAYS,
    end: Optional[datetime] = None,
) -> Dict[str, pd.DataFrame]:
    """
    Download daily OHLCV for many tickers in one batch call.

    Returns ``{ticker_code: DataFrame}`` (only tickers with data).
    """
    end = end or datetime.now() + timedelta(days=1)
    start = end - timedelta(days=days)

    symbols = [_yf_ticker(t) for t in tickers]
    symbol_str = " ".join(symbols)

    try:
        raw = yf.download(
            symbol_str,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            group_by="ticker",
            progress=False,
            auto_adjust=True,
            threads=True,
        )
    except Exception:
        logger.error("Bulk download failed", exc_info=True)
        return {}

    result: Dict[str, pd.DataFrame] = {}
    for code, symbol in zip(tickers, symbols):
        try:
            if len(symbols) == 1:
                df = raw.copy()
            else:
                df = raw[symbol].copy()

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.dropna(how="all")
            if df.empty:
                continue

            required = {"Open", "High", "Low", "Close", "Volume"}
            if required.issubset(df.columns):
                result[code] = df[list(required)].copy()
        except (KeyError, TypeError):
            continue

    logger.info("Fetched data for %d / %d tickers", len(result), len(tickers))
    return result


def fetch_intraday_ohlcv(
    ticker: str,
    interval: str = "15m",
    period: str = "5d",
) -> pd.DataFrame:
    """
    Download intraday OHLCV for a single IDX ticker.

    ``interval`` can be ``1m, 2m, 5m, 15m, 30m, 60m, 90m``.
    ``period`` can be ``1d .. 1mo`` (max 60d for 15m).
    """
    symbol = _yf_ticker(ticker)
    try:
        df = yf.download(
            symbol,
            interval=interval,
            period=period,
            progress=False,
            auto_adjust=True,
        )
    except Exception:
        logger.warning("Intraday download failed for %s", symbol, exc_info=True)
        return pd.DataFrame()

    if df.empty:
        return df

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df
