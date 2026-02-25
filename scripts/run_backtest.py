#!/usr/bin/env python3
"""
Backtest runner – optimise parameters and compare TP modes.

Usage:
    python scripts/run_backtest.py BBCA          # single ticker
    python scripts/run_backtest.py BBCA TLKM     # multiple tickers (concatenated)
    python scripts/run_backtest.py --compare BBCA # compare TP modes
    python scripts/run_backtest.py --trades BBCA # print each trade's buy date and sell date
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

pd.set_option('display.max_colwidth', None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.market_data import fetch_daily_ohlcv
from src.backtest.optimizer import compare_tp_modes, optimize, print_trades, run_single

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _fetch_and_concat(tickers: list[str], days: int = 365) -> pd.DataFrame:
    frames = []
    for t in tickers:
        df = fetch_daily_ohlcv(t, days=days)
        if not df.empty:
            frames.append(df)
            logger.info("Loaded %d bars for %s", len(df), t)
    if not frames:
        logger.error("No data for any ticker – aborting")
        sys.exit(1)
    return pd.concat(frames).sort_index()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the volume‑spike strategy")
    parser.add_argument("tickers", nargs="+", help="IDX ticker codes (e.g. BBCA TLKM)")
    parser.add_argument("--days", type=int, default=365, help="History length in days")
    parser.add_argument("--compare", action="store_true", help="Compare all 3 TP modes")
    parser.add_argument(
        "--maximize", default="Equity Final [$]",
        help="Metric to maximise (default: 'Equity Final [$]')",
    )
    parser.add_argument(
        "--trades", action="store_true",
        help="Print each trade's buy date and sell date",
    )
    args = parser.parse_args()

    df = _fetch_and_concat(args.tickers, args.days)
    logger.info("Total bars: %d  |  Date range: %s → %s", len(df), df.index[0], df.index[-1])

    if args.compare:
        print("\n=== TP Mode Comparison ===\n")
        cmp = compare_tp_modes(df)
        print(cmp.to_string(index=False))
        print()
    else:
        print("\n=== Optimising parameters ===\n")
        stats, heatmap = optimize(df, maximize=args.maximize)
        print("\nBest parameters:")
        print(stats.filter(like="_").to_string())
        print("\nPerformance:")
        for key in [
            "Equity Final [$]", "Return [%]", "Win Rate [%]",
            "Max. Drawdown [%]", "Sharpe Ratio", "# Trades", "Expectancy [%]",
        ]:
            if key in stats.index:
                print(f"  {key:30s} {stats[key]}")
        if args.trades:
            print("\n=== Trades (buy date / sell date) ===\n")
            print_trades(stats)


if __name__ == "__main__":
    main()
