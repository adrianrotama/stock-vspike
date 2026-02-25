"""
Fetch and cache the list of IDX (Indonesian Stock Exchange) tickers.

Primary source: IDX website API.
Fallback: a local CSV file shipped with the repo.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import List

import requests

from src.config import TICKER_CSV_PATH

logger = logging.getLogger(__name__)

_IDX_STOCK_LIST_URL = (
    "https://www.idx.co.id/primary/StockData/GetSecuritiesStock"
    "?length=9999&start=0&code=&sector=&board="
)

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.idx.co.id/en/market-data/stocks-data/list-of-stocks/",
}


def fetch_idx_tickers_online() -> List[str]:
    """Fetch ticker codes from the IDX website API."""
    try:
        resp = requests.get(_IDX_STOCK_LIST_URL, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        records = payload.get("data", payload.get("reply", []))
        tickers = sorted({r["Code"] for r in records if r.get("Code")})
        if tickers:
            _save_to_csv(tickers)
            logger.info("Fetched %d tickers from IDX website", len(tickers))
        return tickers
    except Exception:
        logger.warning("Failed to fetch tickers from IDX website", exc_info=True)
        return []


def load_tickers_from_csv() -> List[str]:
    """Load tickers from the local CSV cache."""
    path = Path(TICKER_CSV_PATH)
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # skip header
        return sorted({row[0].strip() for row in reader if row})


def get_idx_tickers(force_refresh: bool = False) -> List[str]:
    """
    Return a list of IDX ticker codes (e.g. ``["AAAA", "BBCA", ...]``).

    Tries the online API first; falls back to the local CSV cache.
    """
    if not force_refresh:
        cached = load_tickers_from_csv()
        if cached:
            return cached

    tickers = fetch_idx_tickers_online()
    if tickers:
        return tickers

    cached = load_tickers_from_csv()
    if cached:
        logger.info("Using cached CSV (%d tickers)", len(cached))
        return cached

    raise RuntimeError(
        "Cannot obtain IDX ticker list. "
        "Place a CSV with a 'ticker' column at: " + TICKER_CSV_PATH
    )


def _save_to_csv(tickers: List[str]) -> None:
    path = Path(TICKER_CSV_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ticker"])
    for t in tickers:
        writer.writerow([t])
    path.write_text(buf.getvalue())
