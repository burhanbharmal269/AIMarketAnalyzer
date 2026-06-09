"""Signal log routes — /api/signals (list + analytics + CSV export)."""
import csv
import io
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.services.storage import get_recent_signals, get_signal_analytics

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/signals")
def signals_list(limit: int = 50, outcome: Optional[str] = None):
    """Recent signal_log rows. outcome filter: win / loss / None (all)."""
    return {"items": get_recent_signals(limit=limit, outcome_filter=outcome)}


@router.get("/signals/analytics")
def signals_analytics():
    """Accuracy analytics sliced by score bucket, VIX, data source, flags etc."""
    return get_signal_analytics()


@router.get("/signals/export")
def signals_export():
    rows = get_recent_signals(limit=100_000)
    if not rows:
        raise HTTPException(status_code=404, detail="No signal data yet.")
    fields = list(rows[0].keys())
    buf    = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    filename = f"signals_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
