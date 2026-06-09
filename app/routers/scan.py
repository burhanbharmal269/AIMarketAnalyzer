"""Scan routes — /api/scan (POST and GET).

POST /api/scan  — starts a background scan and returns immediately.
GET  /api/scan  — returns the latest cached scan result.

The scan can take 40-60s (41 symbols × Angel One rate limit + NSE scraping).
Running it in a background thread avoids HTTP timeouts and lets the frontend
poll GET /api/scan until the result is ready.
"""
import logging
import threading

from fastapi import APIRouter, HTTPException

from app.routers.schemas import ScanSettings
from app.services.scan_service import (
    build_scan, notify_scan_failure,
    get_cached_scan, get_cache_timestamp,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Track whether a scan is currently running so we don't stack concurrent scans.
_scan_running = threading.Event()


def _run_scan_background(settings_payload: dict | None = None) -> None:
    """Run build_scan() in a background thread. Clears the running flag when done."""
    try:
        build_scan(settings_payload)
        logger.info("Background scan completed")
    except Exception as exc:
        notify_scan_failure(str(exc))
        logger.error("Background scan failed: %s", exc)
    finally:
        _scan_running.clear()


@router.post("/scan")
def scan_post(settings_payload: ScanSettings):
    """Start a market scan in the background. Returns immediately.

    If a scan is already running, returns the current cached result (or 202 if
    no cache yet). Poll GET /api/scan to get the completed result.
    """
    cached = get_cached_scan()

    if _scan_running.is_set():
        # Already in progress — return stale cache or let client know to poll
        if cached:
            return {**cached, "scanStatus": "running", "message": "Scan in progress — showing previous result"}
        return {"scanStatus": "running", "message": "Scan started — no previous result yet, poll GET /api/scan"}

    # Start background scan
    _scan_running.set()
    t = threading.Thread(
        target=_run_scan_background,
        args=(settings_payload.model_dump(),),
        daemon=True,
        name="scan-worker",
    )
    t.start()

    if cached:
        return {**cached, "scanStatus": "running", "message": "New scan started — showing previous result while scanning"}

    return {
        "scanStatus": "running",
        "message":    "Scan started — poll GET /api/scan in ~60s for results",
        "approved":   [],
        "rejected":   [],
        "noTrade":    True,
    }


@router.get("/scan")
def scan_get():
    """Return the latest cached scan result.

    If no scan has completed yet, triggers a background scan and asks the client to poll.
    """
    cached = get_cached_scan()
    if cached:
        return {**cached, "scanStatus": "ready", "cachedAt": get_cache_timestamp()}

    # No cache — kick off a scan if one isn't already running
    if not _scan_running.is_set():
        _scan_running.set()
        t = threading.Thread(
            target=_run_scan_background,
            daemon=True,
            name="scan-worker",
        )
        t.start()

    return {
        "scanStatus": "running",
        "message":    "Scan in progress — poll again in ~60s",
        "approved":   [],
        "rejected":   [],
        "noTrade":    True,
    }
