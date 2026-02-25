"""
Telegram Bot API wrapper – send formatted signal messages.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

import requests

from src import config
from src.screener.signal_generator import Signal, SignalType
from src.screener.volume_spike import SpikeEvent

logger = logging.getLogger(__name__)


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a single message via the Telegram Bot API."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured – skipping send")
        return False

    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }
    try:
        resp = requests.post(config.TELEGRAM_API_URL, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception:
        logger.error("Failed to send Telegram message", exc_info=True)
        return False


# ── formatters ───────────────────────────────────────────────────────────────

def _idr(value: float) -> str:
    """Format a number as IDR with thousand separators."""
    return f"{value:,.0f}"


def _trillion(value: float) -> str:
    if value >= 1e12:
        return f"{value / 1e12:.1f}T"
    if value >= 1e9:
        return f"{value / 1e9:.1f}B"
    return _idr(value)


def format_daily_report(
    spikes: List[SpikeEvent],
    near_entry: List[dict],
    date: Optional[datetime] = None,
) -> str:
    """Build the daily night report message."""
    date = date or datetime.now()
    header = f"📊 <b>IDX Volume Spike Report – {date.strftime('%d %b %Y')}</b>\n"

    # ── spikes section ───────────────────────────────────────────────────
    if spikes:
        lines = ["", "🔥 <b>VOLUME SPIKE DETECTED:</b>"]
        for i, s in enumerate(spikes[:15], 1):
            lines.append(
                f"{i}. <b>{s.ticker}</b> – RVOL: {s.rvol}x | "
                f"Close: {_idr(s.close)} ({s.pct_change:+.1f}%) | "
                f"Txn: {_trillion(s.avg_txn_value)}"
            )
    else:
        lines = ["", "No volume spikes detected today."]

    # ── near‑entry section ───────────────────────────────────────────────
    if near_entry:
        lines.append("")
        lines.append("📍 <b>NEAR ENTRY LEVEL:</b>")
        for i, ne in enumerate(near_entry[:10], 1):
            ema_status = "reclaiming ✓" if ne["ema_reclaiming"] else "below ✗"
            lines.append(
                f"{i}. <b>{ne['ticker']}</b> – "
                f"Retrace: {ne['retrace_pct']}% from spike | EMA: {ema_status}\n"
                f"   Entry zone: {_idr(ne['entry_zone_low'])}–{_idr(ne['entry_zone_high'])} | "
                f"SL: {_idr(ne['sl'])} | TP: {_idr(ne['tp'])}"
            )

    return header + "\n".join(lines)


def format_intraday_signal(signal: Signal) -> str:
    """Build an intraday alert message for a single signal."""
    if signal.signal_type == SignalType.ENTRY:
        icon = "🟢"
        label = "ENTRY SIGNAL"
    elif signal.signal_type == SignalType.TAKE_PROFIT:
        icon = "🎯"
        label = "TAKE PROFIT"
    else:
        icon = "🔴"
        label = "STOP LOSS"

    header = f"{icon} <b>{label} – {signal.ticker} @ {_idr(signal.price)}</b>\n"
    detail = signal.note

    parts = [header, detail]

    if signal.entry_price:
        pnl_pct = (signal.price - signal.entry_price) / signal.entry_price * 100
        parts.append(f"Entry: {_idr(signal.entry_price)} | P&L: {pnl_pct:+.1f}%")

    if signal.sl_price:
        sl_dist = (signal.sl_price - signal.price) / signal.price * 100
        parts.append(f"SL: {_idr(signal.sl_price)} ({sl_dist:+.1f}%)")

    if signal.tp_price:
        tp_dist = (signal.tp_price - signal.price) / signal.price * 100
        parts.append(f"TP: {_idr(signal.tp_price)} ({tp_dist:+.1f}%)")

    return "\n".join(parts)


def send_daily_report(
    spikes: List[SpikeEvent],
    near_entry: List[dict],
) -> bool:
    """Format and send the daily night report."""
    msg = format_daily_report(spikes, near_entry)
    return send_message(msg)


def send_signal_alert(signal: Signal) -> bool:
    """Format and send an intraday signal alert."""
    msg = format_intraday_signal(signal)
    return send_message(msg)
