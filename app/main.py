import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import BASE_DIR, settings
from app.sample_data import sample_candidates, sample_market_snapshot
from app.services.ai import ai_status, generate_market_summary, generate_trade_explanation, openai_enabled
from app.services.backtest import backtest_snapshot
from app.services.scanner import CATEGORY_MAX, scan_market
from app.services.storage import (
    add_journal_entry,
    compute_risk_state,
    get_journal_entries,
    init_db,
    recent_scans,
    record_scan,
    update_journal_entry,
)
from app.services.telegram import preview_message, send_message, telegram_status

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

app = FastAPI(title=settings.app_name, version="0.2.0")
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


@app.on_event("startup")
def on_startup():
    init_db()

    # Start scheduler if enabled
    if settings.enable_scheduler:
        from app.services.scheduler import create_scheduler
        from app.services.telegram import send_message as tg_send

        sched = create_scheduler(
            scan_fn=lambda: build_scan(persist=False),
            send_fn=tg_send,
        )
        if sched:
            sched.start()
            logger.info("Scheduler started")


# ── scan helpers ──────────────────────────────────────────────────────────────

def _live_data():
    """Try NSE live data; return (candidates, market) or raise on failure."""
    from app.data_sources.nse import nse_data
    market     = nse_data.get_market_snapshot()
    candidates = nse_data.get_live_candidates()
    if not candidates:
        raise ValueError("No live candidates returned from NSE")
    return candidates, market


def build_scan(settings_payload: dict | None = None, persist: bool = True) -> dict:
    data_source = "live"
    try:
        candidates, market = _live_data()
    except Exception as exc:
        logger.info("Live data unavailable (%s) — using sample data", exc)
        candidates  = sample_candidates()
        market      = sample_market_snapshot()
        data_source = "sample"

    risk_pct = (settings_payload or {}).get("riskPercent", 2.0)
    scan = scan_market(candidates, market, compute_risk_state(risk_pct=risk_pct), settings_payload)

    # Enrich approved signals with AI trade explanations
    if openai_enabled():
        for item in scan["approved"]:
            item["explanation"] = generate_trade_explanation(
                item["candidate"], item["score"], market
            )

    if persist:
        record_scan(scan)

    return {"market": market, "categoryMax": CATEGORY_MAX, "dataSource": data_source, **scan}


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
    from datetime import datetime
    from zoneinfo import ZoneInfo

    available = nse_data.is_available()
    vix       = nse_data.get_india_vix() if available else None
    return {
        "liveDataAvailable": available,
        "indiaVix":          vix,
        "timestamp":         datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
    }


# ── API: scan ─────────────────────────────────────────────────────────────────

@app.post("/api/scan")
def scan(settings_payload: ScanSettings):
    return build_scan(settings_payload.model_dump())


@app.get("/api/scan")
def scan_default():
    return build_scan()


# ── API: backtest ─────────────────────────────────────────────────────────────

@app.get("/api/backtest")
def backtest():
    return backtest_snapshot()


# ── API: audit ────────────────────────────────────────────────────────────────

@app.get("/api/audit/recent")
def audit_recent(limit: int = 10):
    return {"items": recent_scans(limit)}


# ── API: AI summary ───────────────────────────────────────────────────────────

@app.get("/api/summary")
def summary():
    market = sample_market_snapshot()
    scan   = scan_market(sample_candidates(), market, compute_risk_state(), None)
    return generate_market_summary(scan, market)


# ── API: Telegram ─────────────────────────────────────────────────────────────

@app.post("/api/telegram/preview")
def telegram_preview(settings_payload: ScanSettings):
    response = build_scan(settings_payload.model_dump(), persist=False)
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
