import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import BASE_DIR, settings
from app.services.ai import ai_status, generate_market_summary, generate_trade_explanation, openai_enabled
from app.services.backtest import backtest_snapshot
from app.services.scanner import CATEGORY_MAX, scan_market
from app.services.storage import (
    add_journal_entry,
    compute_risk_state,
    get_journal_analytics,
    get_journal_entries,
    init_db,
    recent_scans,
    record_scan,
    update_journal_entry,
)
from app.services.telegram import preview_message, send_message, start_retry_drain, telegram_status

logger = logging.getLogger(__name__)


# ── Pydantic models ───────────────────────────────────────────────────────────

class ScanSettings(BaseModel):
    accountCapital: float = Field(default=30000, ge=10000)
    riskPercent:    float = Field(default=2, ge=0.1, le=10)
    maxSpread:      float = Field(default=1.5, ge=0.5, le=10)
    minVolume:      int   = Field(default=50000, ge=0)
    eventWindow:    int   = Field(default=60, ge=0)
    lossStreak:     int   = Field(default=0, ge=0)


class TelegramSendRequest(BaseModel):
    message: str


class JournalEntry(BaseModel):
    instrument:      str
    direction:       str
    entry:           float
    stopLoss:        float
    targets:         list[float] = Field(default=[0.0, 0.0, 0.0])
    confidenceScore: int         = Field(default=0)
    status:          str         = Field(default="paper")
    notes:           str         = Field(default="")


class JournalUpdate(BaseModel):
    exit_price: Optional[float] = None
    outcome:    Optional[str]   = None
    pnl_r:      Optional[float] = None
    status:     Optional[str]   = None
    notes:      Optional[str]   = None


# ── app setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title=settings.app_name, version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_src = BASE_DIR / "src"
if static_src.exists():
    app.mount("/src", StaticFiles(directory=static_src), name="src")


def _setup_logging() -> None:
    """Rotating file log: 10 MB per file, 5 backups kept."""
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


def _validate_startup() -> None:
    """Check all critical dependencies at boot and log their status clearly."""
    from app.data_sources.nse import nse_data

    # NSE connectivity
    try:
        vix = nse_data.get_india_vix()
        if vix and vix > 0:
            logger.info("STARTUP OK  — NSE reachable, VIX %.2f", vix)
        else:
            logger.warning("STARTUP WARN — NSE reachable but VIX returned None")
    except Exception as exc:
        logger.error("STARTUP FAIL — NSE unreachable: %s", exc)

    # Database
    try:
        from app.services.storage import get_journal_entries
        get_journal_entries(limit=1)
        logger.info("STARTUP OK  — Database accessible")
    except Exception as exc:
        logger.error("STARTUP FAIL — Database error: %s", exc)

    # Telegram
    if settings.telegram_bot_token and settings.telegram_chat_id:
        logger.info("STARTUP OK  — Telegram configured")
    else:
        logger.warning("STARTUP WARN — Telegram not configured (no alerts will be sent)")

    # Azure OpenAI
    if getattr(settings, "azure_openai_api_key", None):
        logger.info("STARTUP OK  — Azure OpenAI configured")
    else:
        logger.warning("STARTUP WARN — Azure OpenAI not configured (AI explanations disabled)")


@app.on_event("startup")
def on_startup():
    _setup_logging()
    init_db()
    _validate_startup()

    from app.data_sources.nse import nse_data as _nse
    from app.services.telegram import send_message as tg_send

    from app.services.monitor import start_price_monitor
    start_price_monitor(_nse, tg_send)
    logger.info("Price monitor + NSE watchdog started")

    start_retry_drain()

    if settings.enable_scheduler:
        from app.services.scheduler import create_scheduler
        sched = create_scheduler(
            scan_fn=lambda: build_scan(persist=False),
            send_fn=tg_send,
            nse_data=_nse,
        )
        if sched:
            sched.start()
            logger.info("Scheduler started")


# ── live scan cache (15-minute TTL) ──────────────────────────────────────────

_scan_cache: dict | None = None
_scan_cache_at: datetime | None = None
_SCAN_CACHE_TTL = 900  # seconds


def _cache_scan(result: dict) -> None:
    global _scan_cache, _scan_cache_at
    _scan_cache = result
    _scan_cache_at = datetime.now(timezone.utc)


def _get_cached_scan() -> dict | None:
    if _scan_cache is None or _scan_cache_at is None:
        return None
    age = (datetime.now(timezone.utc) - _scan_cache_at).total_seconds()
    return _scan_cache if age < _SCAN_CACHE_TTL else None


# ── scan helpers ──────────────────────────────────────────────────────────────

def _live_data():
    """Fetch live candidates and market snapshot from NSE. Raises on any failure."""
    from app.data_sources.nse import nse_data
    market     = nse_data.get_market_snapshot()
    candidates = nse_data.get_live_candidates()
    if not candidates:
        raise RuntimeError("NSE returned zero candidates — market may be closed or session expired")
    return candidates, market


def _notify_scan_failure(reason: str) -> None:
    """Queue a Telegram alert so the trader knows the scan failed."""
    logger.error("Scan failed: %s", reason)
    try:
        from app.services.storage import enqueue_alert
        enqueue_alert(f"SCAN FAILED\n{reason}\nCheck NSE connectivity. Do NOT trade on stale data.")
    except Exception as exc:
        logger.error("Could not queue scan failure alert: %s", exc)


def _auto_paper_trade(item: dict) -> None:
    """Log an approved signal as a paper trade if not already open for that instrument."""
    from app.services.storage import get_journal_entries
    c = item["candidate"]
    instrument = c.get("instrument", "")
    # Skip if already have an open/paper entry for this instrument
    existing = [e for e in get_journal_entries(limit=50)
                if e["instrument"] == instrument and e["status"] in ("open", "paper")]
    if existing:
        return
    add_journal_entry({
        "instrument":      instrument,
        "direction":       c.get("direction", "BUY"),
        "entry":           c.get("entry", 0),
        "stopLoss":        c.get("stopLoss", 0),
        "targets":         c.get("targets", [0, 0, 0]),
        "confidenceScore": item.get("score", {}).get("total", 0),
        "status":          "paper",
        "notes":           f"Auto-logged by scanner. Score: {item.get('score', {}).get('total', 0)}/100",
    })
    logger.info("Auto-paper-trade logged: %s", instrument)


def build_scan(settings_payload: dict | None = None, persist: bool = True) -> dict:
    """Run a live market scan. Raises RuntimeError if NSE data is unavailable."""
    candidates, market = _live_data()   # raises — no silent sample fallback

    risk_pct   = (settings_payload or {}).get("riskPercent", 2.0)
    risk_state = compute_risk_state(risk_pct=risk_pct)

    # Journal-computed loss streak overrides manual input if higher (safer)
    journal_streak = risk_state.get("lossStreak", 0)
    if settings_payload:
        manual_streak = settings_payload.get("lossStreak", 0)
        settings_payload["lossStreak"] = max(journal_streak, manual_streak)
    else:
        settings_payload = {"lossStreak": journal_streak}

    scan = scan_market(candidates, market, risk_state, settings_payload)

    if openai_enabled():
        for item in scan["approved"]:
            item["explanation"] = generate_trade_explanation(
                item["candidate"], item["score"], market
            )

    result = {"market": market, "categoryMax": CATEGORY_MAX, "dataSource": "live",
              "lossStreak": journal_streak, **scan}

    if persist:
        record_scan(scan)
        _cache_scan(result)
        # Auto-log every approved signal as a paper trade
        for item in scan["approved"]:
            try:
                _auto_paper_trade(item)
            except Exception as exc:
                logger.warning("Auto-paper-trade failed for %s: %s",
                               item.get("candidate", {}).get("instrument"), exc)

    return result


# ── static routes ─────────────────────────────────────────────────────────────

@app.get("/")
def dashboard():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/styles.css")
def styles():
    return FileResponse(BASE_DIR / "styles.css")


# ── API: health & status ──────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {
        "status":        "ok",
        "pythonBackend": True,
        "database":      str(settings.database_path),
        "ai":            ai_status(),
        "telegram":      telegram_status(),
    }


@app.get("/api/data-status")
def data_status():
    from app.data_sources.nse import nse_data
    from zoneinfo import ZoneInfo

    available = nse_data.is_available()
    vix       = nse_data.get_india_vix() if available else None
    last_scan = _scan_cache_at.isoformat() if _scan_cache_at else None
    return {
        "liveDataAvailable": available,
        "indiaVix":          vix,
        "lastScanAt":        last_scan,
        "timestamp":         datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
    }


# ── API: scan ─────────────────────────────────────────────────────────────────

@app.post("/api/scan")
def scan(settings_payload: ScanSettings):
    try:
        return build_scan(settings_payload.model_dump())
    except Exception as exc:
        _notify_scan_failure(str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/scan")
def scan_default():
    try:
        return build_scan()
    except Exception as exc:
        _notify_scan_failure(str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ── API: backtest ─────────────────────────────────────────────────────────────

@app.get("/api/backtest")
def backtest():
    try:
        return backtest_snapshot()
    except Exception as exc:
        logger.warning("Backtest unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ── API: audit ────────────────────────────────────────────────────────────────

@app.get("/api/audit/recent")
def audit_recent(limit: int = 10):
    return {"items": recent_scans(limit)}


# ── API: AI summary ───────────────────────────────────────────────────────────

@app.get("/api/summary")
def summary():
    # Use cached live scan first (avoids re-running the full scan for a summary)
    cached = _get_cached_scan()
    if cached:
        return generate_market_summary(cached, cached["market"])
    # No cache — run a fresh scan (not persisted)
    try:
        result = build_scan(persist=False)
        return generate_market_summary(result, result["market"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Live data unavailable for summary: {exc}") from exc


# ── API: Telegram ─────────────────────────────────────────────────────────────

@app.post("/api/telegram/preview")
def telegram_preview(settings_payload: ScanSettings):
    try:
        response = build_scan(settings_payload.model_dump(), persist=False)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"message": preview_message(response, response["market"])}


@app.post("/api/telegram/send")
def telegram_send(payload: TelegramSendRequest):
    try:
        return send_message(payload.message)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── API: trade journal ────────────────────────────────────────────────────────

@app.post("/api/journal")
def journal_add(entry: JournalEntry):
    entry_id = add_journal_entry(entry.model_dump())
    return {"id": entry_id, "status": "saved"}


@app.get("/api/journal")
def journal_list(limit: int = 50, status: Optional[str] = None):
    return {"items": get_journal_entries(limit, status)}


@app.get("/api/journal/analytics")
def journal_analytics():
    return get_journal_analytics()


@app.get("/api/journal/export")
def journal_export():
    """Download all journal entries as a CSV file."""
    import csv, io
    from fastapi.responses import StreamingResponse
    entries = get_journal_entries(limit=10000)
    if not entries:
        raise HTTPException(status_code=404, detail="No journal entries to export.")
    fields = ["id", "created_at", "instrument", "direction", "entry", "stop_loss",
              "target_1", "target_2", "target_3", "confidence_score",
              "status", "outcome", "exit_price", "pnl_r", "notes"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(entries)
    buf.seek(0)
    filename = f"journal_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.patch("/api/journal/{entry_id}")
def journal_update(entry_id: int, update: JournalUpdate):
    success = update_journal_entry(entry_id, update.model_dump(exclude_none=True))
    if not success:
        raise HTTPException(status_code=400, detail="No valid fields to update.")
    return {"updated": True}


# ── static fallback ───────────────────────────────────────────────────────────

@app.get("/{path:path}")
def static_fallback(path: str):
    target = (BASE_DIR / path).resolve()
    if target.exists() and target.is_file() and BASE_DIR in target.parents:
        return FileResponse(target)
    return FileResponse(BASE_DIR / "index.html")
