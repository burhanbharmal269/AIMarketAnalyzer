"""Journal routes — /api/journal (CRUD + analytics + CSV export)."""
import csv
import io
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.routers.schemas import JournalEntry, JournalUpdate
from app.services.storage import (
    add_journal_entry,
    get_journal_analytics,
    get_journal_entries,
    update_journal_entry,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_JOURNAL_EXPORT_FIELDS = [
    "id", "created_at", "instrument", "direction", "entry", "stop_loss",
    "target_1", "target_2", "target_3", "confidence_score",
    "status", "outcome", "exit_price", "pnl_r", "notes",
]


@router.post("/journal")
def journal_add(entry: JournalEntry):
    entry_id = add_journal_entry(entry.model_dump())
    return {"id": entry_id, "status": "saved"}


@router.get("/journal")
def journal_list(limit: int = 50, status: Optional[str] = None):
    return {"items": get_journal_entries(limit, status)}


@router.get("/journal/analytics")
def journal_analytics():
    return get_journal_analytics()


@router.get("/journal/export")
def journal_export():
    entries = get_journal_entries(limit=10_000)
    if not entries:
        raise HTTPException(status_code=404, detail="No journal entries to export.")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_JOURNAL_EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(entries)
    buf.seek(0)
    filename = f"journal_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.patch("/journal/{entry_id}")
def journal_update(entry_id: int, update: JournalUpdate):
    success = update_journal_entry(entry_id, update.model_dump(exclude_none=True))
    if not success:
        raise HTTPException(status_code=400, detail="No valid fields to update.")
    return {"updated": True}
