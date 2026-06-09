"""FastAPI application entry point — wiring only.

Responsibilities:
  - Create the FastAPI app and attach middleware
  - Mount static files
  - Include API routers
  - Run startup lifecycle (logging, DB, health checks, background services)

All business logic lives in app/services/.
All HTTP concerns live in app/routers/.
All constants live in app/core/constants.py.
"""
import logging
import logging.handlers
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.core.constants import APP_NAME, APP_VERSION, LOG_MAX_BYTES, LOG_BACKUP_COUNT
from app.routers import scan, journal, signals, market, telegram, admin

logger = logging.getLogger(__name__)


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(title=APP_NAME, version=APP_VERSION)

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

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(scan.router,     prefix="/api")
app.include_router(journal.router,  prefix="/api")
app.include_router(signals.router,  prefix="/api")
app.include_router(market.router,   prefix="/api")
app.include_router(telegram.router, prefix="/api")
app.include_router(admin.router,    prefix="/api")


# ── Static file routes ────────────────────────────────────────────────────────

@app.get("/")
def dashboard():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/styles.css")
def styles():
    return FileResponse(BASE_DIR / "styles.css")


@app.get("/{path:path}")
def static_fallback(path: str):
    target = (BASE_DIR / path).resolve()
    if target.exists() and target.is_file() and BASE_DIR in target.parents:
        return FileResponse(target)
    return FileResponse(BASE_DIR / "index.html")


# ── Startup lifecycle ─────────────────────────────────────────────────────────

def _setup_logging() -> None:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
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
    from app.data_sources.angel import startup_login, ANGEL_CLIENT_ID, ANGEL_AVAILABLE
    if not ANGEL_AVAILABLE:
        logger.warning("STARTUP WARN — Angel One not configured (env vars missing)")
    elif startup_login():
        logger.info("STARTUP OK  — Angel One SmartAPI connected (%s) + keepalive started",
                    ANGEL_CLIENT_ID)
    else:
        logger.error("STARTUP FAIL — Angel One login failed after retries — check credentials")

    from app.data_sources.nse import nse_data
    try:
        vix = nse_data.get_india_vix()
        if vix and vix > 0:
            logger.info("STARTUP OK  — NSE reachable, VIX %.2f", vix)
        else:
            logger.warning("STARTUP WARN — NSE reachable but VIX returned None")
    except Exception as exc:
        logger.error("STARTUP FAIL — NSE unreachable: %s", exc)

    try:
        from app.services.storage import get_journal_entries
        get_journal_entries(limit=1)
        logger.info("STARTUP OK  — Database accessible")
    except Exception as exc:
        logger.error("STARTUP FAIL — Database error: %s", exc)

    if settings.telegram_bot_token and settings.telegram_chat_id:
        logger.info("STARTUP OK  — Telegram configured")
    else:
        logger.warning("STARTUP WARN — Telegram not configured (no alerts will be sent)")

    if getattr(settings, "azure_openai_api_key", None):
        logger.info("STARTUP OK  — Azure OpenAI configured")
    else:
        logger.warning("STARTUP WARN — Azure OpenAI not configured (AI explanations disabled)")


@app.on_event("startup")
def on_startup():
    _setup_logging()

    from app.services.storage import init_db
    init_db()

    _validate_startup()

    from app.data_sources.nse     import nse_data as _nse
    from app.services.telegram    import send_message as tg_send, start_retry_drain
    from app.services.monitor     import start_price_monitor

    start_price_monitor(_nse, tg_send)
    logger.info("Price monitor + NSE watchdog started")

    start_retry_drain()

    if settings.enable_scheduler:
        from app.services.scan_service import build_scan
        from app.services.scheduler    import create_scheduler
        sched = create_scheduler(
            scan_fn=lambda: build_scan(persist=False),
            send_fn=tg_send,
            nse_data=_nse,
        )
        if sched:
            sched.start()
            logger.info("Scheduler started")
