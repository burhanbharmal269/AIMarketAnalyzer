"""Market data routes — /api/health, /api/data-status, /api/summary, /api/option-ltp."""
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.ai import ai_status, generate_market_summary
from app.services.scan_service import get_cached_scan, get_cache_timestamp
from app.services.telegram import telegram_status

logger = logging.getLogger(__name__)
router = APIRouter()

_IST = ZoneInfo("Asia/Kolkata")


@router.get("/health")
def health():
    from app.data_sources.angel import ANGEL_AVAILABLE, angel_session
    return {
        "status":        "ok",
        "pythonBackend": True,
        "database":      str(settings.database_path),
        "ai":            ai_status(),
        "telegram":      telegram_status(),
        "angelOne":      angel_session.status(),
    }


@router.get("/option-ltp")
def option_ltp(
    underlying: str,
    strike: float,
    opt_type: str,
    expiry: Optional[str] = None,
):
    """Real-time Angel One option quote.

    Examples:
      /api/option-ltp?underlying=NIFTY&strike=23200&opt_type=PE
      /api/option-ltp?underlying=BANKNIFTY&strike=52000&opt_type=CE&expiry=12Jun2026
    """
    from app.data_sources.angel import get_option_ltp
    opt_type = opt_type.upper()
    if opt_type not in ("CE", "PE"):
        return {"error": "opt_type must be CE or PE"}
    result = get_option_ltp(underlying.upper(), strike, opt_type, expiry_hint=expiry)
    if result is None:
        return {
            "error":      "No data — market may be closed, option expired, or Angel One disconnected",
            "underlying": underlying,
            "strike":     strike,
            "optType":    opt_type,
        }
    return result


def _is_market_open() -> bool:
    """True only during NSE trading hours: Mon–Fri, 09:15–15:30 IST."""
    from datetime import time as dtime
    now = datetime.now(_IST)
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    t = now.time()
    return dtime(9, 15) <= t <= dtime(15, 30)


@router.get("/data-status")
def data_status():
    from app.data_sources.nse import nse_data
    available   = nse_data.is_available()
    vix         = nse_data.get_india_vix() if available else None
    market_open = _is_market_open()
    return {
        "liveDataAvailable": available,
        "marketOpen":        market_open,
        "indiaVix":          vix,
        "lastScanAt":        get_cache_timestamp(),
        "timestamp":         datetime.now(_IST).isoformat(),
    }


@router.get("/integration-status")
def integration_status():
    """Latest API probe result. Probe runs every 5 min via the scheduler.
    Call POST /api/integration-status/run to trigger an immediate probe."""
    from app.services.api_probe import get_last_result, secs_since_last_probe
    result = get_last_result()
    if not result:
        return {"detail": "No probe has run yet — trigger one via POST /api/integration-status/run"}
    return {**result, "secondsSinceProbe": secs_since_last_probe()}


@router.post("/integration-status/run")
def run_integration_probe():
    """Trigger an immediate API probe outside the 5-min schedule."""
    from app.services.api_probe import probe_all
    return probe_all()


@router.get("/summary")
def summary():
    """Return AI market briefing from the latest scan cache.

    Never triggers a live scan — always responds immediately.
    Run POST /api/scan first to populate the cache.
    """
    from app.services.scan_service import _scan_cache  # raw cache, bypass TTL for summary
    if _scan_cache:
        result = _scan_cache
        return {**generate_market_summary(result, result["market"]), "stale": get_cache_timestamp()}
    raise HTTPException(
        status_code=503,
        detail="No scan data yet — run POST /api/scan first, then retry.",
    )
