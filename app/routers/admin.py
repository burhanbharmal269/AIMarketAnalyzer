"""Admin and audit routes — /api/admin/*, /api/audit/*, /api/backtest."""
import logging

from fastapi import APIRouter, HTTPException

from app.services.backtest import backtest_snapshot
from app.services.storage  import invalidate_ohlcv_today, recent_scans

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/audit/recent")
def audit_recent(limit: int = 10):
    return {"items": recent_scans(limit)}


@router.get("/backtest")
def backtest():
    try:
        return backtest_snapshot()
    except Exception as exc:
        logger.warning("Backtest unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/admin/ohlcv-refresh")
def admin_ohlcv_refresh():
    """Invalidate today's daily_ohlcv cache so the next scan re-fetches the EOD candle."""
    deleted = invalidate_ohlcv_today()
    return {"deleted": deleted, "message": f"Cleared {deleted} today-dated OHLCV rows"}
