#!/usr/bin/env python3
"""
Diagnostic tool – inspect why a ticker is or isn't generating signals.

Usage:
    python scripts/diagnose.py BBCA
    python scripts/diagnose.py BBCA --days 60
    python scripts/diagnose.py BBCA --date 2026-02-26   # inspect a specific date
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.data.market_data import fetch_daily_ohlcv
from src.screener.volume_spike import compute_rvol, compute_avg_txn_value, price_position
from src.screener.signal_generator import enrich

logging.basicConfig(level=logging.WARNING)

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "


def fmt(val, fmt_str=".2f"):
    try:
        return format(val, fmt_str)
    except (TypeError, ValueError):
        return str(val)


def check_spike_filters(row: pd.Series, prev_row: pd.Series, cfg) -> dict:
    """Return per-filter pass/fail for spike detection."""
    rvol = row.get("rvol", np.nan)
    avg_txn = row.get("avg_txn", np.nan)
    pp = row.get("price_pos", np.nan)
    close = row["Close"]
    open_ = row["Open"]
    prev_close = prev_row["Close"]

    results = {
        "RVOL":          (rvol >= cfg.RVOL_THRESHOLD,        f"{fmt(rvol)} (need ≥ {cfg.RVOL_THRESHOLD})"),
        "Min Price":     (close >= cfg.MIN_PRICE,             f"{fmt(close, ',.0f')} (need ≥ {cfg.MIN_PRICE:,.0f})"),
        "Avg Txn Value": (avg_txn >= cfg.MIN_AVG_TXN_VALUE,  f"{fmt(avg_txn, ',.0f')} (need ≥ {cfg.MIN_AVG_TXN_VALUE:,.0f})"),
        "Green Candle":  (close > open_,                     f"close {fmt(close,',.0f')} vs open {fmt(open_,',.0f')}"),
        "Price Position":(not np.isnan(pp) and pp >= cfg.PRICE_POSITION_MIN,
                                                              f"{fmt(pp)} (need ≥ {cfg.PRICE_POSITION_MIN})"),
        "Close > Prev":  (close > prev_close,                f"{fmt(close,',.0f')} vs prev {fmt(prev_close,',.0f')}"),
    }
    return results


def check_entry_filters(latest: pd.Series, prev: pd.Series, spike_row: pd.Series, cfg) -> dict:
    """Return per-filter pass/fail for entry signal."""
    close = latest["Close"]
    prev_close_val = prev["Close"]
    ema = latest.get("EMA", np.nan)
    prev_ema = prev.get("EMA", np.nan)
    mfi = latest.get("MFI", np.nan)
    pre_spike_close = spike_row["Close"]   # prev_close of spike day = close before spike

    dist_pct = abs(close - pre_spike_close) / pre_spike_close * 100 if pre_spike_close else np.nan
    ema_above = close > ema
    prev_ema_above = prev_close_val > prev_ema
    ema_sloping = ema > prev_ema

    results = {
        "Retrace to zone":    (dist_pct <= cfg.RETRACE_PCT,
                               f"{fmt(dist_pct)}% from pre-spike close {fmt(pre_spike_close,',.0f')} (need ≤ {cfg.RETRACE_PCT}%)"),
        "EMA reclaim (now)":  (ema_above,
                               f"close {fmt(close,',.0f')} vs EMA {fmt(ema,',.2f')}"),
        "EMA reclaim (prev)": (prev_ema_above,
                               f"prev close {fmt(prev_close_val,',.0f')} vs prev EMA {fmt(prev_ema,',.2f')}"),
        "EMA sloping up":     (ema_sloping,
                               f"EMA {fmt(ema,',.2f')} vs prev EMA {fmt(prev_ema,',.2f')}"),
        "MFI":                (not np.isnan(mfi) and mfi >= cfg.MFI_MIN,
                               f"{fmt(mfi)} (need ≥ {cfg.MFI_MIN})"),
    }
    return results


def print_filter_table(title: str, filters: dict) -> None:
    print(f"\n  {'─'*50}")
    print(f"  {title}")
    print(f"  {'─'*50}")
    all_pass = True
    for name, (passed, detail) in filters.items():
        icon = PASS if passed else FAIL
        print(f"  {icon}  {name:<22} {detail}")
        if not passed:
            all_pass = False
    if all_pass:
        print(f"\n  {PASS} ALL FILTERS PASSED")
    else:
        failed = [n for n, (p, _) in filters.items() if not p]
        print(f"\n  {FAIL} BLOCKING FILTERS: {', '.join(failed)}")


def diagnose(ticker: str, days: int = 90, target_date: str | None = None) -> None:
    print(f"\n{'='*54}")
    print(f"  DIAGNOSTIC: {ticker}  ({days} days history)")
    print(f"{'='*54}")

    df = fetch_daily_ohlcv(ticker, days=days)
    if df.empty:
        print(f"  {FAIL} No data returned for {ticker}")
        return

    # enrich with indicators
    df["rvol"]      = compute_rvol(df["Volume"])
    df["avg_txn"]   = compute_avg_txn_value(df)
    df["price_pos"] = price_position(df)
    df["prev_close"] = df["Close"].shift(1)
    df = enrich(df)

    # ── find all spike days ───────────────────────────────────────────────
    from src.screener.volume_spike import detect_spikes
    spikes = detect_spikes(df, ticker)

    print(f"\n  Total bars loaded : {len(df)}")
    print(f"  Date range        : {df.index[0].date()} → {df.index[-1].date()}")
    print(f"  Spike days found  : {len(spikes)}")

    if spikes:
        print("\n  Recent spikes:")
        for s in spikes[-5:]:
            print(f"    {s.date.date()}  RVOL={s.rvol}x  close={s.close:,.0f}  chg={s.pct_change:+.1f}%")

    # ── inspect a specific date or the latest bar ─────────────────────────
    if target_date:
        try:
            target_ts = pd.Timestamp(target_date)
            if target_ts not in df.index:
                print(f"\n  {WARN} Date {target_date} not in index. Nearest dates:")
                nearby = df.index[df.index.get_indexer([target_ts], method="nearest")]
                print(f"    {[str(d.date()) for d in nearby]}")
                return
            idx = df.index.get_loc(target_ts)
        except Exception as e:
            print(f"  {FAIL} Invalid date: {e}")
            return
    else:
        idx = len(df) - 1

    inspect_date = df.index[idx]
    row     = df.iloc[idx]
    prev    = df.iloc[idx - 1] if idx > 0 else row

    print(f"\n  Inspecting bar   : {inspect_date.date()}")
    print(f"  O={row['Open']:,.0f}  H={row['High']:,.0f}  L={row['Low']:,.0f}  C={row['Close']:,.0f}  V={int(row['Volume']):,}")
    print(f"  RVOL={fmt(row['rvol'])}x  EMA={fmt(row.get('EMA', np.nan),',.2f')}  MFI={fmt(row.get('MFI', np.nan))}")

    # ── spike filter check on this bar ───────────────────────────────────
    spike_filters = check_spike_filters(row, prev, config)
    print_filter_table(f"SPIKE FILTERS  ({inspect_date.date()})", spike_filters)

    # ── entry filter check using most recent spike ────────────────────────
    if spikes:
        latest_spike = spikes[-1]
        # find the pre-spike row (the bar before spike.date)
        try:
            spike_idx = df.index.get_loc(latest_spike.date)
            pre_spike_row = df.iloc[spike_idx - 1] if spike_idx > 0 else df.iloc[spike_idx]
        except KeyError:
            pre_spike_row = prev

        days_since = (inspect_date - latest_spike.date).days
        print(f"\n  Last spike       : {latest_spike.date.date()}  ({days_since} days ago)")
        print(f"  Spike high       : {latest_spike.high:,.0f}")
        print(f"  Pre-spike close  : {latest_spike.prev_close:,.0f}  (entry zone target)")

        entry_filters = check_entry_filters(row, prev, pre_spike_row, config)
        print_filter_table(f"ENTRY FILTERS  (vs spike on {latest_spike.date.date()})", entry_filters)

        # how far is price from entry zone
        dist = (row["Close"] - latest_spike.prev_close) / latest_spike.prev_close * 100
        if dist > config.RETRACE_PCT:
            print(f"\n  {WARN}  Price is {dist:.1f}% ABOVE entry zone — waiting for retrace")
        elif dist < -config.RETRACE_PCT:
            print(f"\n  {WARN}  Price is {abs(dist):.1f}% BELOW entry zone — may have broken down")
        else:
            print(f"\n  {PASS}  Price is within entry zone ({dist:+.1f}% from pre-spike close)")
    else:
        print(f"\n  {WARN}  No spikes detected — entry filter check skipped")
        print(f"       Try lowering RVOL_THRESHOLD (currently {config.RVOL_THRESHOLD})")
        print(f"       or increasing the --days window to catch older spikes")

    print()


def main():
    parser = argparse.ArgumentParser(description="Diagnose volume spike signals for a ticker")
    parser.add_argument("ticker", help="IDX ticker code (e.g. BBCA)")
    parser.add_argument("--days", type=int, default=90, help="History window in days")
    parser.add_argument("--date", type=str, default=None,
                        help="Inspect a specific date (YYYY-MM-DD), default = latest bar")
    args = parser.parse_args()

    diagnose(args.ticker.upper(), days=args.days, target_date=args.date)


if __name__ == "__main__":
    main()

"""
Sample output would look like:

======================================================
  DIAGNOSTIC: BBCA  (90 days history)
======================================================

  Total bars loaded : 63
  Date range        : 2024-09-01 → 2024-11-27
  Spike days found  : 2

  Recent spikes:
    2024-11-10  RVOL=6.2x  close=9450  chg=+3.8%

  Inspecting bar   : 2024-11-27
  O=9100  H=9200  L=9050  C=9150  V=45,231,000
  RVOL=1.2x  EMA=9080.50  MFI=44.2

  ──────────────────────────────────────────────────
  ENTRY FILTERS  (vs spike on 2024-11-10)
  ──────────────────────────────────────────────────
  ✅  Retrace to zone        1.8% from pre-spike close 9,100 (need ≤ 3%)
  ✅  EMA reclaim (now)      close 9,150 vs EMA 9,080.50
  ❌  EMA reclaim (prev)     prev close 9,020 vs prev EMA 9,060.30
  ✅  EMA sloping up         EMA 9,080.50 vs prev EMA 9,060.30
  ❌  MFI                    44.2 (need ≥ 50)

  ❌ BLOCKING FILTERS: EMA reclaim (prev), MFI
  """