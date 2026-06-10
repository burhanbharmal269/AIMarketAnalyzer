"""Periodic API health probe.

Runs at a configurable interval while the app is running and tests each
external integration: Kite, NSE, AI (Azure OpenAI), and News.
Results are stored in memory and exposed at GET /api/integration-status.
"""
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

PROBE_INTERVAL_SECS = 300   # 5 min default

_last_result:    dict  = {}
_last_probed_at: float = 0.0


def _now_ist() -> datetime:
    return datetime.now(ZoneInfo("Asia/Kolkata"))


def _probe_kite() -> dict:
    """Test Kite: session validity + a single LTP call (NIFTY)."""
    try:
        from app.data_sources.kite import kite_session, KITE_AVAILABLE, get_ltp
        if not KITE_AVAILABLE:
            return {"ok": False, "detail": "Kite credentials not configured"}
        status = kite_session.status()
        if not status["connected"]:
            return {
                "ok":     False,
                "detail": f"Session not connected — {status.get('message', '')}",
                "login_url": status.get("login_url", ""),
                "session": status,
            }
        t0  = time.time()
        ltp = get_ltp("NIFTY")
        ms  = round((time.time() - t0) * 1000)
        if ltp and ltp > 0:
            return {
                "ok":         True,
                "detail":     f"Kite OK — NIFTY LTP ₹{ltp:,.1f} in {ms}ms",
                "nifty_ltp":  ltp,
                "latency_ms": ms,
                "session":    status,
            }
        return {
            "ok":         True,
            "detail":     f"Session OK — LTP unavailable (market closed) [{ms}ms]",
            "session":    status,
            "latency_ms": ms,
        }
    except Exception as exc:
        logger.warning("api_probe kite: %s", exc)
        return {"ok": False, "detail": str(exc)}


def _probe_nse() -> dict:
    """Test NSE: reachability + VIX fetch."""
    try:
        from app.data_sources.nse import nse_data
        t0        = time.time()
        available = nse_data.is_available()
        vix       = nse_data.get_india_vix() if available else None
        ms        = round((time.time() - t0) * 1000)
        if available:
            return {"ok": True, "detail": f"NSE reachable — VIX {vix} in {ms}ms",
                    "vix": vix, "latency_ms": ms}
        return {"ok": False, "detail": f"NSE unreachable [{ms}ms]", "latency_ms": ms}
    except Exception as exc:
        logger.warning("api_probe nse: %s", exc)
        return {"ok": False, "detail": str(exc)}


def _probe_ai() -> dict:
    """Test Azure OpenAI: minimal regime classification."""
    try:
        from app.services.ai import openai_enabled, get_market_regime_ai, ai_status
        if not openai_enabled():
            return {"ok": False, "detail": "AI client not configured", "status": ai_status()}
        t0     = time.time()
        result = get_market_regime_ai({
            "indiaVix": 15.0,
            "breadth":  {"advancing": 30, "declining": 20},
            "eventCalendar": [],
        })
        ms = round((time.time() - t0) * 1000)
        return {
            "ok":         True,
            "detail":     f"Azure OpenAI OK — regime={result.get('aiRegime')} in {ms}ms",
            "regime":     result.get("aiRegime"),
            "latency_ms": ms,
            "status":     ai_status(),
        }
    except Exception as exc:
        logger.warning("api_probe ai: %s", exc)
        return {"ok": False, "detail": str(exc)}


def _probe_news() -> dict:
    """Test News: yfinance headline fetch."""
    try:
        import yfinance as yf
        t0   = time.time()
        news = yf.Ticker("^NSEI").news or []
        ms   = round((time.time() - t0) * 1000)
        return {
            "ok":         True,
            "detail":     f"yfinance OK — {len(news)} headlines in {ms}ms",
            "count":      len(news),
            "latency_ms": ms,
        }
    except Exception as exc:
        logger.warning("api_probe news: %s", exc)
        return {"ok": False, "detail": str(exc)}


def probe_all() -> dict:
    """Run all probes and store the result. Safe to call from a background thread."""
    global _last_result, _last_probed_at
    now   = _now_ist()
    logger.info("api_probe: running all probes at %s", now.strftime("%H:%M:%S IST"))

    kite  = _probe_kite()
    nse   = _probe_nse()
    ai    = _probe_ai()
    news  = _probe_news()
    all_ok = all([kite["ok"], nse["ok"], ai["ok"], news["ok"]])

    result = {
        "probedAt": now.isoformat(),
        "allOk":    all_ok,
        "kite":     kite,
        "nse":      nse,
        "ai":       ai,
        "news":     news,
    }
    _last_result    = result
    _last_probed_at = time.time()

    logger.info(
        "api_probe: %s — kite=%s nse=%s ai=%s news=%s",
        "OK" if all_ok else "DEGRADED",
        "OK" if kite["ok"] else "FAIL",
        "OK" if nse["ok"]  else "FAIL",
        "OK" if ai["ok"]   else "FAIL",
        "OK" if news["ok"] else "FAIL",
    )
    return result


def get_last_result() -> dict:
    return _last_result


def secs_since_last_probe() -> float | None:
    return None if _last_probed_at == 0.0 else round(time.time() - _last_probed_at)
