"""Scan routes — /api/scan (POST and GET)."""
import logging

from fastapi import APIRouter, HTTPException

from app.routers.schemas import ScanSettings
from app.services.scan_service import build_scan, notify_scan_failure

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/scan")
def scan_post(settings_payload: ScanSettings):
    try:
        return build_scan(settings_payload.model_dump())
    except Exception as exc:
        notify_scan_failure(str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/scan")
def scan_get():
    try:
        return build_scan()
    except Exception as exc:
        notify_scan_failure(str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
