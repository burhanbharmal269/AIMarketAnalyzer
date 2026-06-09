"""Telegram routes — /api/telegram/preview and /api/telegram/send."""
import logging

from fastapi import APIRouter, HTTPException

from app.routers.schemas import ScanSettings, TelegramSendRequest
from app.services.scan_service import build_scan
from app.services.telegram import preview_message, send_message

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/telegram/preview")
def telegram_preview(settings_payload: ScanSettings):
    try:
        response = build_scan(settings_payload.model_dump(), persist=False)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"message": preview_message(response, response["market"])}


@router.post("/telegram/send")
def telegram_send(payload: TelegramSendRequest):
    try:
        return send_message(payload.message)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
