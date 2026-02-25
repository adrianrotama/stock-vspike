"""
backtesting.py Strategy class for the volume‑spike retracement system.

This module is imported by the optimizer and can also be used standalone
with ``backtesting.Backtest``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from backtesting import Strategy
from backtesting.lib import crossover


# ── indicator helper functions (must accept numpy‑like arrays) ───────────────

def _sma(arr, window):
    s = pd.Series(arr)
    return s.rolling(window, min_periods=window).mean().values


def _ema(arr, window):
    s = pd.Series(arr)
    return s.ewm(span=window, adjust=False).mean().values


def _atr(high, low, close, window=14):
    h = pd.Series(high)
    l = pd.Series(low)
    c = pd.Series(close)
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(window, min_periods=window).mean().values


def _mfi(high, low, close, volume, window=14):
    tp = (pd.Series(high) + pd.Series(low) + pd.Series(close)) / 3
    mf = tp * pd.Series(volume)
    delta = tp.diff()
    pos_mf = mf.where(delta > 0, 0).rolling(window).sum()
    neg_mf = mf.where(delta <= 0, 0).rolling(window).sum()
    ratio = pos_mf / neg_mf.replace(0, np.nan)
    return (100 - 100 / (1 + ratio)).values


def _rvol(volume, window):
    s = pd.Series(volume)
    avg = s.rolling(window, min_periods=window).mean()
    return (s / avg).replace([np.inf, -np.inf], np.nan).values


def _price_position(high, low, close):
    rng = pd.Series(high) - pd.Series(low)
    return ((pd.Series(close) - pd.Series(low)) / rng).replace(
        [np.inf, -np.inf], np.nan
    ).values


class VolumeSpikeRetracement(Strategy):
    """
    Parameters (all tuneable via ``Backtest.optimize``):

    * ``rvol_threshold``  – minimum RVOL to qualify as a spike  (3‑10)
    * ``retrace_pct``     – max % distance from pre‑spike close for entry  (1‑8)
    * ``ema_period``      – EMA look‑back for MA‑reclaim check  (5, 10, 20)
    * ``sl_pct``          – base stop‑loss % below pre‑spike close  (2‑5)
    * ``tp_mode``         – 1=breakout, 2=MA breakdown, 3=trailing stop
    * ``trailing_pct``    – trailing‑stop distance when tp_mode=3  (1‑5)
    * ``mfi_min``         – minimum MFI at entry  (20‑60)
    * ``vol_window``      – SMA window for RVOL baseline  (10‑30)
    """

    # tuneable parameters with defaults
    rvol_threshold: int = 5
    retrace_pct: int = 3
    ema_period: int = 10
    sl_pct: int = 3
    tp_mode: int = 3
    trailing_pct: int = 3
    mfi_min: int = 20
    vol_window: int = 20

    def init(self):
        c = self.data.Close
        h = self.data.High
        l = self.data.Low
        v = self.data.Volume

        self.rvol = self.I(_rvol, v, self.vol_window, name="RVOL")
        self.ema_line = self.I(_ema, c, self.ema_period, name="EMA")
        self.atr_line = self.I(_atr, h, l, c, 14, name="ATR")
        self.mfi_line = self.I(_mfi, h, l, c, v, 14, name="MFI")
        self.pp = self.I(_price_position, h, l, c, name="PricePos")

        # state
        self._spike_close = np.nan       # close on the spike day
        self._pre_spike_close = np.nan   # close the day before the spike
        self._spike_high = np.nan        # high on the spike day
        self._highest = 0.0              # for trailing TP

    def next(self):
        close = self.data.Close[-1]
        prev_close = self.data.Close[-2] if len(self.data.Close) > 1 else close
        is_green = close > self.data.Open[-1]
        rvol = self.rvol[-1]
        pp = self.pp[-1]
        ema_val = self.ema_line[-1]
        prev_ema = self.ema_line[-2] if len(self.ema_line) > 1 else ema_val
        mfi_val = self.mfi_line[-1]
        atr_val = self.atr_line[-1]

        # ── detect new spike (only when flat) ────────────────────────────
        if not self.position:
            if (
                not np.isnan(rvol)
                and rvol >= self.rvol_threshold
                and is_green
                and close > prev_close
                and (not np.isnan(pp) and pp >= 0.5)
            ):
                self._spike_close = close
                self._pre_spike_close = prev_close
                self._spike_high = self.data.High[-1]

            # ── check entry ──────────────────────────────────────────────
            if not np.isnan(self._pre_spike_close) and self._pre_spike_close > 0:
                dist_pct = abs(close - self._pre_spike_close) / self._pre_spike_close * 100
                ema_reclaim = close > ema_val and prev_close <= prev_ema

                if (
                    dist_pct <= self.retrace_pct
                    and ema_reclaim
                    and (not np.isnan(mfi_val) and mfi_val >= self.mfi_min)
                ):
                    pct_dist = self._pre_spike_close * self.sl_pct / 100
                    atr_safe = atr_val if not np.isnan(atr_val) else 0
                    sl_dist = max(pct_dist, atr_safe)
                    sl_price = self._pre_spike_close - sl_dist

                    # SL must be below the entry price for a long order;
                    # when retrace is deep the pre-spike-based SL can end
                    # up above the current price.
                    if sl_price >= close:
                        sl_price = close * (1 - self.sl_pct / 100)

                    self.buy(sl=sl_price)
                    self._highest = close
            return

        # ── manage open position ─────────────────────────────────────────
        self._highest = max(self._highest, close)
        entry_price = self.trades[-1].entry_price

        if self.tp_mode == 1:  # breakout
            if close > self._spike_high:
                self.position.close()
                self._reset()

        elif self.tp_mode == 2:  # MA breakdown while in profit
            if close > entry_price and close < ema_val:
                self.position.close()
                self._reset()

        elif self.tp_mode == 3:  # trailing stop
            trail = self._highest * (1 - self.trailing_pct / 100)
            if close > entry_price and close < trail:
                self.position.close()
                self._reset()

    def _reset(self):
        self._spike_close = np.nan
        self._pre_spike_close = np.nan
        self._spike_high = np.nan
        self._highest = 0.0
