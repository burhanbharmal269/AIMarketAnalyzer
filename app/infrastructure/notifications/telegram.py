"""TelegramNotificationAdapter — wraps existing telegram.py behind INotificationProvider."""
from __future__ import annotations
import asyncio
import logging
from app.application.ports.notification import INotificationProvider

logger = logging.getLogger(__name__)


class TelegramNotificationAdapter(INotificationProvider):
    """Delegates to existing app.services.telegram.send_message."""

    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        try:
            from app.services.telegram import send_message
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, send_message, message)
            return True
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)
            return False

    async def send_signal_alert(self, signal_dict: dict) -> bool:
        candidate = signal_dict.get("candidate", {})
        score     = signal_dict.get("score", {})
        instrument = candidate.get("instrument", "Unknown")
        direction  = candidate.get("direction", "")
        entry      = candidate.get("entry", 0)
        sl         = candidate.get("stopLoss", 0)
        targets    = candidate.get("targets", [])
        t1 = targets[0] if targets else 0
        score_total = score.get("total", signal_dict.get("score", {}).get("total", 0))
        lots  = signal_dict.get("lots", 0)

        msg = (
            f"<b>SIGNAL: {instrument}</b>\n"
            f"Direction: {direction}\n"
            f"Entry: ₹{entry:.2f} | SL: ₹{sl:.2f} | T1: ₹{t1:.2f}\n"
            f"Score: {score_total:.0f}/100 | Lots: {lots}\n"
        )
        if signal_dict.get("explanation"):
            msg += f"\n{signal_dict['explanation'][:200]}"

        return await self.send(msg)

    async def health_check(self) -> bool:
        try:
            from app.services.telegram import send_message
            return True
        except Exception:
            return False
