import logging

import requests

from app.config import settings
from app.services.scanner import telegram_text

logger = logging.getLogger(__name__)


def telegram_status() -> dict:
    return {
        "configured":      bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "chatIdConfigured": bool(settings.telegram_chat_id),
    }


def send_message(message: str) -> dict:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return {"sent": False, "reason": "Telegram bot token or chat id is missing."}

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={"chat_id": settings.telegram_chat_id, "text": message},
            timeout=20,
        )
        response.raise_for_status()
        return {"sent": True, "telegramResponse": response.json()}
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return {"sent": False, "reason": str(exc)}


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
        lines += [
            f"  {c['direction']} {c['instrument']}",
            f"  Entry: {c['entry']}  SL: {c['stopLoss']}  T1/T2: {c['targets'][0]}/{c['targets'][1]}",
            f"  Score: {item['score']['total']}/100  RR: 1:{c['rr']}  Valid: {item['validUntil']}",
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
