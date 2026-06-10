"""Domain event handlers — side effects decoupled from the scan pipeline.

Each handler is an async function registered with AsyncEventBus at startup.
Handlers must be idempotent: the bus may retry on transient failures.
"""
from __future__ import annotations
import asyncio
import logging

from app.domain.signal.events import SignalApproved, SignalRejected, ScanCompleted
from app.domain.trade.events import TradeOpened, SLHit, TargetHit, TradeClosed

logger = logging.getLogger(__name__)


# ── Telegram handlers ─────────────────────────────────────────────────────────

async def on_signal_approved_telegram(event: SignalApproved) -> None:
    """Send Telegram alert when a signal is approved."""
    try:
        loop = asyncio.get_running_loop()
        from app.services.telegram import send_message
        msg = (
            f"<b>SIGNAL: {event.instrument}</b>\n"
            f"Direction: {event.direction} | Setup: {event.setup_type}\n"
            f"Score: {event.score:.1f}/100 | Lots: {event.lots}"
        )
        await loop.run_in_executor(None, send_message, msg)
    except Exception as exc:
        logger.warning("Telegram signal alert failed: %s", exc)


async def on_sl_hit_telegram(event: SLHit) -> None:
    try:
        loop = asyncio.get_running_loop()
        from app.services.telegram import send_message
        msg = (
            f"🛑 <b>SL HIT: {event.instrument}</b>\n"
            f"Exit: ₹{event.exit_price:.2f} | P&L: {event.pnl_r:+.2f}R"
        )
        await loop.run_in_executor(None, send_message, msg)
    except Exception as exc:
        logger.warning("Telegram SL alert failed: %s", exc)


async def on_target_hit_telegram(event: TargetHit) -> None:
    try:
        loop = asyncio.get_running_loop()
        from app.services.telegram import send_message
        msg = (
            f"🎯 <b>T{event.target_num} HIT: {event.instrument}</b>\n"
            f"Exit: ₹{event.exit_price:.2f} | P&L: {event.pnl_r:+.2f}R"
        )
        await loop.run_in_executor(None, send_message, msg)
    except Exception as exc:
        logger.warning("Telegram target alert failed: %s", exc)


async def on_scan_completed_telegram(event: ScanCompleted) -> None:
    try:
        loop = asyncio.get_running_loop()
        from app.services.telegram import send_message
        if event.approved_count == 0:
            msg = f"Scan complete — no signals ({event.duration_ms}ms)"
        else:
            msg = (
                f"Scan #{event.scan_id}: "
                f"{event.approved_count} signals approved, "
                f"{event.rejected_count} rejected ({event.duration_ms}ms)"
            )
        await loop.run_in_executor(None, send_message, msg)
    except Exception as exc:
        logger.warning("Telegram scan summary failed: %s", exc)


# ── Journal / monitor handlers ────────────────────────────────────────────────

async def on_signal_approved_journal(event: SignalApproved) -> None:
    """Log to signal_log table when approved."""
    logger.info(
        "Signal approved: %s %s score=%.1f lots=%d",
        event.direction, event.instrument, event.score, event.lots,
    )


async def on_sl_hit_monitor(event: SLHit) -> None:
    """Notify trade monitor to stop watching this position."""
    logger.info(
        "SL hit: %s at ₹%.2f pnl=%.2fR",
        event.instrument, event.exit_price, event.pnl_r,
    )
    try:
        loop = asyncio.get_running_loop()
        from app.services.trade_monitor import unwatch_trade
        await loop.run_in_executor(None, unwatch_trade, event.trade_id)
    except (ImportError, AttributeError, Exception) as exc:
        logger.debug("unwatch_trade not available: %s", exc)


async def on_trade_opened_monitor(event: TradeOpened) -> None:
    """Register new trade with trade monitor."""
    logger.info(
        "Trade opened: %s %s entry=₹%.2f sl=₹%.2f lots=%d",
        event.direction, event.instrument, event.entry, event.stop_loss, event.lots,
    )


def register_all_handlers(bus, *, telegram: bool = True) -> None:
    """Wire all handlers to the event bus. Call at startup."""
    from app.application.events.bus import AsyncEventBus
    assert isinstance(bus, AsyncEventBus)

    bus.subscribe(SignalApproved,  on_signal_approved_journal)
    bus.subscribe(TradeOpened,     on_trade_opened_monitor)
    bus.subscribe(SLHit,           on_sl_hit_monitor)

    if telegram:
        bus.subscribe(SignalApproved,  on_signal_approved_telegram)
        bus.subscribe(SLHit,           on_sl_hit_telegram)
        bus.subscribe(TargetHit,       on_target_hit_telegram)
        bus.subscribe(ScanCompleted,   on_scan_completed_telegram)

    logger.info("Event handlers registered (telegram=%s)", telegram)
