"""FastAPI application entry point — wiring only.

Responsibilities:
  - Create the FastAPI app and attach middleware
  - Mount static files
  - Include API routers
  - Run startup lifecycle (logging, DB, background health checks, services)

All business logic lives in app/services/.
All HTTP concerns live in app/routers/.
All constants live in app/core/constants.py.

STARTUP DESIGN
--------------
Slow network calls (Angel One login, NSE VIX probe) are pushed to a background
asyncio.Task so the HTTP server begins accepting connections immediately.
  • /api/health  → always 200 (process alive)
  • /api/ready   → 200 when background init finished, 503 while still in progress
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.core.constants import APP_NAME, APP_VERSION, LOG_MAX_BYTES, LOG_BACKUP_COUNT
from app.core.logging import configure_logging
from app.core.app_state import state as _state
from app.routers import scan, journal, signals, market, telegram, admin, kite_auth

logger = logging.getLogger(__name__)


# ── Startup helpers ───────────────────────────────────────────────────────────

def _setup_logging() -> None:
    configure_logging(level=settings.log_level, json_logs=settings.json_logs)

    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.getLogger().addHandler(file_handler)


def _init_db() -> None:
    try:
        from app.services.storage import init_db
        init_db()
        _state["checks"]["db"] = True
        logger.info("STARTUP OK  — Database initialised")
    except Exception as exc:
        _state["checks"]["db"] = False
        logger.error("STARTUP FAIL — Database error: %s", exc)


def _check_kite() -> bool:
    """Blocking — run in executor. Returns True if saved session is valid."""
    try:
        from app.data_sources.kite import startup_check
        return startup_check()
    except Exception as exc:
        logger.error("STARTUP FAIL — Kite error: %s", exc)
        return False


def _check_nse() -> bool:
    """Blocking — run in executor. Returns True on success."""
    try:
        from app.data_sources.nse import nse_data
        vix = nse_data.get_india_vix()
        if vix and vix > 0:
            logger.info("STARTUP OK  — NSE reachable, VIX %.2f", vix)
            return True
        logger.warning("STARTUP WARN — NSE reachable but VIX returned None")
        return False
    except Exception as exc:
        logger.error("STARTUP FAIL — NSE unreachable: %s", exc)
        return False


async def _background_init() -> None:
    """Run all slow network checks in thread-pool so the server stays responsive."""
    loop = asyncio.get_event_loop()
    try:
        # Run Kite and NSE probes concurrently in the thread pool
        kite_ok, nse_ok = await asyncio.gather(
            loop.run_in_executor(None, _check_kite),
            loop.run_in_executor(None, _check_nse),
            return_exceptions=True,
        )

        _state["checks"]["kite"] = kite_ok if isinstance(kite_ok, bool) else False
        _state["checks"]["nse"]  = nse_ok  if isinstance(nse_ok,  bool) else False

        # Wire new-architecture DI container (fast, no network)
        try:
            from app.api.dependencies import init_dependencies
            init_dependencies(settings.to_dependency_settings())
            logger.info("New architecture DI container initialised")
        except Exception as exc:
            logger.warning("New architecture DI init failed (non-fatal): %s", exc)

        # Start background services
        from app.data_sources.nse  import nse_data as _nse
        from app.services.telegram import send_message as tg_send, start_retry_drain
        from app.services.monitor  import start_price_monitor

        start_price_monitor(_nse, tg_send)
        start_retry_drain()
        logger.info("Price monitor + retry drain started")

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

        _log_config_summary()

    except Exception as exc:
        logger.error("Background init error (non-fatal): %s", exc)
    finally:
        _state["ready"] = True
        elapsed = time.monotonic() - _state["started_at"]
        logger.info("Background init finished in %.1fs", elapsed)


def _log_config_summary() -> None:
    if settings.telegram_bot_token and settings.telegram_chat_id:
        logger.info("STARTUP OK  — Telegram configured")
    else:
        logger.warning("STARTUP WARN — Telegram not configured")

    if getattr(settings, "azure_openai_api_key", None):
        logger.info("STARTUP OK  — Azure OpenAI configured")
    else:
        logger.warning("STARTUP WARN — Azure OpenAI not configured")


# ── Lifespan (replaces deprecated @app.on_event) ──────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Phase 1: fast synchronous setup (milliseconds) ───────────────────────
    _setup_logging()
    _init_db()
    _state["started_at"] = time.monotonic()

    # ── Phase 2: schedule slow network calls as a background task ─────────────
    # The server starts accepting connections as soon as we yield — the background
    # task runs concurrently with real requests. /api/health returns 200 immediately;
    # /api/ready returns 503 until the task sets _state["ready"] = True.
    bg = asyncio.create_task(_background_init())

    logger.info(
        "Server ready — background init running. "
        "Check /api/ready for full readiness."
    )

    yield  # ← HTTP server is live from this point

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if not bg.done():
        bg.cancel()
        try:
            await bg
        except asyncio.CancelledError:
            pass
    logger.info("Server shutdown complete")


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    from app.api.middleware.timing import (
        TimingMiddleware, CorrelationMiddleware, SecurityHeadersMiddleware
    )
    app.add_middleware(TimingMiddleware)
    app.add_middleware(CorrelationMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
except Exception:
    pass

static_src = BASE_DIR / "src"
if static_src.exists():
    app.mount("/src", StaticFiles(directory=static_src), name="src")


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(scan.router,       prefix="/api")
app.include_router(journal.router,    prefix="/api")
app.include_router(signals.router,    prefix="/api")
app.include_router(market.router,     prefix="/api")
app.include_router(telegram.router,   prefix="/api")
app.include_router(admin.router,      prefix="/api")
app.include_router(kite_auth.router,  prefix="/api")


# ── Readiness endpoint ────────────────────────────────────────────────────────
# /api/health is handled by market.router (already returns 200 immediately).
# /api/ready is the explicit "fully initialised?" probe used by start.ps1 / CI.

@app.get("/api/ready", tags=["ops"])
def readiness_check():
    """Returns 200 once background init finishes, 503 while still loading."""
    # Read live Kite status rather than the startup-probe boolean — avoids a
    # race condition where _check_kite ran before the session file was loaded.
    from app.data_sources.kite import KITE_AVAILABLE, kite_session
    kite_live = bool(KITE_AVAILABLE and kite_session and kite_session.status().get("connected"))
    checks = {**_state["checks"], "kite": kite_live}
    if _state["ready"]:
        return {"status": "ready", "checks": checks}
    return JSONResponse(
        status_code=503,
        content={
            "status": "initializing",
            "message": "Background init in progress — retry in a few seconds",
            "checks":  checks,
        },
    )


# ── Static file routes ────────────────────────────────────────────────────────

@app.get("/")
def dashboard(request_token: str = "", status: str = "", action: str = "", type: str = ""):
    # Zerodha redirects to root when redirect_url is set to http://127.0.0.1:8000/
    # Intercept and forward to the real callback handler.
    if request_token and (status == "success" or action == "login"):
        from fastapi.responses import RedirectResponse as _Redir
        return _Redir(f"/api/kite/callback?request_token={request_token}&status=success")
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
