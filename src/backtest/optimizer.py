"""
Parameter optimizer for the volume‑spike retracement strategy.

Uses ``backtesting.py``'s built‑in grid / random‑sampling optimizer
with optional heatmap output.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import pandas as pd
from backtesting import Backtest

from src.backtest.strategy import VolumeSpikeRetracement

logger = logging.getLogger(__name__)

DEFAULT_CASH = 100_000_000  # IDR 100 M


def run_single(
    df: pd.DataFrame,
    cash: int = DEFAULT_CASH,
    **strategy_kwargs: Any,
) -> pd.Series:
    """
    Run a single backtest with explicit parameters.
    Returns the stats Series produced by ``backtesting.py``.
    """
    bt = Backtest(
        df,
        VolumeSpikeRetracement,
        cash=cash,
        commission=0.0015,   # typical IDX brokerage ~0.15 %
        exclusive_orders=True,
    )
    return bt.run(**strategy_kwargs)


def optimize(
    df: pd.DataFrame,
    cash: int = DEFAULT_CASH,
    maximize: str = "Equity Final [$]",
    max_tries: Optional[int] = 300,
    return_heatmap: bool = True,
    param_ranges: Optional[Dict] = None,
) -> Tuple[pd.Series, Optional[pd.Series]]:
    """
    Optimize strategy parameters over a grid / random sample.

    Parameters
    ----------
    df : OHLCV DataFrame (must have Open, High, Low, Close, Volume).
    cash : starting capital in IDR.
    maximize : metric to maximise (e.g. ``"Equity Final [$]"``,
               ``"Sharpe Ratio"``, ``"Win Rate [%]"``).
    max_tries : max random combinations to test (``None`` = full grid).
    return_heatmap : whether to compute the 2‑D heatmap.
    param_ranges : override default parameter ranges.

    Returns
    -------
    (best_stats, heatmap_or_None)
    """
    ranges = param_ranges or {
        "rvol_threshold": range(3, 11),
        "retrace_pct": range(1, 9),
        "ema_period": [5, 10, 20],
        "sl_pct": range(2, 6),
        "tp_mode": [1, 2, 3],
        "trailing_pct": range(1, 6),
        "mfi_min": [20, 30, 40, 50, 60],
        "vol_window": [10, 15, 20, 30],
    }

    bt = Backtest(
        df,
        VolumeSpikeRetracement,
        cash=cash,
        commission=0.0015,
        exclusive_orders=True,
    )

    kwargs: Dict[str, Any] = {
        "maximize": maximize,
        "return_heatmap": return_heatmap,
    }
    if max_tries is not None:
        kwargs["max_tries"] = max_tries

    result = bt.optimize(**ranges, **kwargs)

    if return_heatmap:
        stats, heatmap = result
    else:
        stats = result
        heatmap = None

    _log_stats(stats)
    return stats, heatmap


def compare_tp_modes(
    df: pd.DataFrame,
    cash: int = DEFAULT_CASH,
) -> pd.DataFrame:
    """
    Run three separate optimisations (one per TP mode) and return
    a summary comparison DataFrame.
    """
    rows = []
    for mode in (1, 2, 3):
        mode_label = {1: "Breakout", 2: "MA Breakdown", 3: "Trailing Stop"}[mode]
        bt = Backtest(
            df,
            VolumeSpikeRetracement,
            cash=cash,
            commission=0.0015,
            exclusive_orders=True,
        )
        stats = bt.optimize(
            rvol_threshold=range(3, 11),
            retrace_pct=range(1, 9),
            ema_period=[5, 10, 20],
            sl_pct=range(2, 6),
            tp_mode=[mode],
            trailing_pct=range(1, 6),
            mfi_min=[20, 30, 40, 50, 60],
            maximize="Equity Final [$]",
            max_tries=200,
            return_heatmap=False,
        )
        rows.append({
            "TP Mode": mode_label,
            "Final Equity": stats["Equity Final [$]"],
            "Return %": stats["Return [%]"],
            "Win Rate %": stats["Win Rate [%]"],
            "Max Drawdown %": stats["Max. Drawdown [%]"],
            "Sharpe": stats.get("Sharpe Ratio", None),
            "# Trades": stats["# Trades"],
            "Expectancy %": stats.get("Expectancy [%]", None),
        })

    return pd.DataFrame(rows)


def print_trades(stats: pd.Series) -> None:
    """
    Print each trade's buy date (entry) and sell date (exit) from backtest stats.
    Stats must be the result of Backtest.run() or Backtest.optimize() (which
    includes a _trades DataFrame).
    """
    trades = getattr(stats, "_trades", None)
    if trades is None:
        trades = stats.get("_trades") if hasattr(stats, "get") else None
    if trades is None or (hasattr(trades, "empty") and trades.empty):
        print("No trades to display.")
        return
    # backtesting.py _trades columns: EntryTime, ExitTime, EntryPrice, ExitPrice, etc.
    has_time = "EntryTime" in trades.columns and "ExitTime" in trades.columns
    has_bar = "EntryBar" in trades.columns and "ExitBar" in trades.columns
    for i, row in trades.iterrows():
        if has_time:
            entry_dt = row["EntryTime"]
            exit_dt = row["ExitTime"]
            entry_str = str(entry_dt) if pd.notna(entry_dt) else "N/A"
            exit_str = str(exit_dt) if pd.notna(exit_dt) else "N/A"
        elif has_bar:
            entry_str = f"Bar {row['EntryBar']}"
            exit_str = f"Bar {row['ExitBar']}"
        else:
            entry_str = "N/A"
            exit_str = "N/A"
        entry_price = row.get("EntryPrice", "")
        exit_price = row.get("ExitPrice", "")
        ret = row.get("ReturnPct", "")
        extra = f"  EntryPrice={entry_price}  ExitPrice={exit_price}  Return%={ret}"
        print(f"  Trade {i + 1}:  Buy date: {entry_str}  |  Sell date: {exit_str}{extra}")


def _log_stats(stats: pd.Series) -> None:
    keys = [
        "Start", "End", "Equity Final [$]", "Return [%]",
        "Win Rate [%]", "Max. Drawdown [%]", "Sharpe Ratio",
        "# Trades", "Expectancy [%]",
    ]
    for k in keys:
        if k in stats.index:
            logger.info("  %-25s %s", k, stats[k])
