#!/usr/bin/env python3
"""
Daily scan entry point.

Intended to run once per trading day after market close (19:00 WIB).

1. Refresh the IDX ticker list.
2. Download daily OHLCV for all tickers.
3. Detect volume spikes.
4. Identify stocks near entry level.
5. Send the daily Telegram report.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.ticker_list import get_idx_tickers
from src.data.market_data import fetch_bulk_daily
from src.screener.volume_spike import latest_spikes
from src.screener.signal_generator import enrich, find_near_entry_stocks
from src.notify.telegram import send_daily_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("=== Daily IDX Scan ===")

    # 1. ticker list
    tickers = get_idx_tickers()
    logger.info("Loaded %d IDX tickers", len(tickers))

    # 2. fetch market data
    data = fetch_bulk_daily(tickers)
    if not data:
        logger.error("No market data returned – aborting")
        return

    # 3. detect recent spikes
    spikes = latest_spikes(data, lookback_days=5)
    logger.info("Found %d spike events in last 5 days", len(spikes))

    # 4. enrich data and find near-entry stocks
    enriched = {t: enrich(df) for t, df in data.items()}
    near_entry = find_near_entry_stocks(enriched, spikes)
    logger.info("Found %d stocks near entry level", len(near_entry))

    # 5. send telegram report
    ok = send_daily_report(spikes, near_entry)
    if ok:
        logger.info("Daily report sent to Telegram")
    else:
        logger.warning("Failed to send daily report (check credentials)")


if __name__ == "__main__":
    main()
