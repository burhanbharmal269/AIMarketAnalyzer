"""Market data routes — /api/health, /api/data-status, /api/summary, /api/option-ltp."""
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.ai import ai_status, generate_market_summary
from app.services.scan_service import build_scan, get_cached_scan, get_cache_timestamp
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


@router.get("/data-status")
def data_status():
    from app.data_sources.nse import nse_data
    available = nse_data.is_available()
    vix       = nse_data.get_india_vix() if available else None
    return {
        "liveDataAvailable": available,
        "indiaVix":          vix,
        "lastScanAt":        get_cache_timestamp(),
        "timestamp":         datetime.now(_IST).isoformat(),
    }


@router.get("/summary")
def summary():
    cached = get_cached_scan()
    if cached:
        return generate_market_summary(cached, cached["market"])
    try:
        result = build_scan(persist=False)
        return generate_market_summary(result, result["market"])
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Live data unavailable for summary: {exc}",
        ) from exc
