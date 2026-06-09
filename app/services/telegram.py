import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from app.config import settings
from app.core.constants import TELEGRAM_RETRY_DELAYS, TELEGRAM_DRAIN_SECS
from app.services.scanner import telegram_text

logger = logging.getLogger(__name__)

_RETRY_DELAYS   = list(TELEGRAM_RETRY_DELAYS)
_DRAIN_INTERVAL = TELEGRAM_DRAIN_SECS
_drain_started  = False


def telegram_status() -> dict:
    return {
        "configured":      bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "chatIdConfigured": bool(settings.telegram_chat_id),
    }


def _post(message: str) -> bool:
    """Low-level send. Returns True on success, False on any failure."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": settings.telegram_chat_id, "text": message},
            timeout=20,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def send_message(message: str) -> dict:
    """Try immediate send; if it fails, write to retry queue."""
    if _post(message):
        return {"sent": True}
    # Queue for retry (up to 3 total attempts; this counts as attempt 1)
    from app.services.storage import enqueue_alert
    try:
        enqueue_alert(message)
        logger.info("Telegram send failed — queued for retry")
    except Exception as exc:
        logger.error("Failed to queue alert: %s", exc)
    return {"sent": False, "reason": "queued for retry"}


def _drain_queue() -> None:
    """Background thread: sends pending alerts with exponential backoff."""
    from app.services.storage import pop_due_alerts, update_alert_status
    while True:
        time.sleep(_DRAIN_INTERVAL)
        try:
            due = pop_due_alerts(limit=10)
            for alert in due:
                aid      = alert["id"]
                message  = alert["message"]
                attempts = alert["attempts"] + 1   # this is the next attempt number

                if _post(message):
                    update_alert_status(aid, "sent", attempts)
                    logger.info("Queued alert %d sent on attempt %d", aid, attempts)
                else:
                    if attempts >= 3:
                        update_alert_status(aid, "failed", attempts)
                        logger.warning("Queued alert %d abandoned after 3 attempts", aid)
                    else:
                        delay     = _RETRY_DELAYS[attempts - 1]
                        next_time = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                        update_alert_status(aid, "pending", attempts, next_retry=next_time)
                        logger.debug("Queued alert %d will retry in %ds (attempt %d)", aid, delay, attempts + 1)
        except Exception as exc:
            logger.warning("Alert drain cycle error: %s", exc)


def start_retry_drain() -> None:
    """Start the background alert-drain thread. Safe to call multiple times."""
    global _drain_started
    if _drain_started:
        return
    _drain_started = True
    threading.Thread(target=_drain_queue, daemon=True, name="telegram-retry").start()
    logger.info("Telegram retry drain started (interval=%ds, max_attempts=3)", _DRAIN_INTERVAL)


def preview_message(scan: dict, market: dict) -> str:
    return telegram_text(scan, market)


# ── alert formatters ──────────────────────────────────────────────────────────

def market_open_alert(market: dict, scan: dict) -> str:
    vix    = market.get("indiaVix", "N/A")
    regime = market.get("regime", "")
    lines  = [
        "MARKET OPEN SCAN",
        f"Regime: {regime}",
        f"India VIX: {vix}",
        f"Approved signals: {len(scan.get('approved', []))}",
        "",
    ]
    for item in scan.get("approved", []):
        c = item["candidate"]
        t = c['targets']
        lines += [
            f"  {c['direction']} {c['instrument']}",
            f"  Entry: {c['entry']}  SL: {c['stopLoss']}",
            f"  T1: {t[0]}  T2: {t[1]}  T3: {t[2]}  RR: 1:{c['rr']}",
            f"  Score: {item['score']['total']}/100  Valid: {item['validUntil']}",
            "",
        ]
    if scan.get("noTrade"):
        lines.append("NO TRADE MODE — No setup cleared all risk gates. Preserve capital.")
    return "\n".join(lines)


def eod_alert(market: dict, scan: dict) -> str:
    vix     = market.get("indiaVix", "N/A")
    breadth = market.get("breadth", "N/A")
    lines   = [
        "END OF DAY SUMMARY",
        f"Market: {market.get('regime', '')}",
        f"India VIX: {vix}  |  Breadth A/D: {breadth}",
        f"Signals today: {len(scan.get('approved', []))} approved",
        "",
        "Action: Review open positions. Respect stop-loss levels.",
        "Next scan: Market open tomorrow at 09:20 IST.",
    ]
    return "\n".join(lines)


def target_hit_alert(instrument: str, target_num: int, target_price: float) -> str:
    return "\n".join([
        "TARGET HIT",
        f"{instrument}",
        f"T{target_num} reached at {target_price}",
        "Action: Book partial profits. Trail stop on remaining position.",
    ])


def stop_loss_alert(instrument: str, stop_price: float) -> str:
    return "\n".join([
        "STOP LOSS HIT",
        f"{instrument}",
        f"SL triggered at {stop_price}",
        "Action: Exit trade completely. Do not average down.",
    ])


def signal_update_alert(instrument: str, update_text: str) -> str:
    return "\n".join([
        "SIGNAL UPDATE",
        instrument,
        update_text,
    ])
