"""
Periodic API health probe.

Runs at a configurable interval while the app is running and tests each
external integration: Angel One, NSE, AI (Azure OpenAI), and News (yfinance).
Results are stored in memory and exposed at GET /api/integration-status.
"""

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Interval between probes in seconds (default: 5 min; 15 min outside market hours)
PROBE_INTERVAL_SECS = 300

# Latest probe result — updated by probe_all(), read by the status endpoint
_last_result: dict = {}
_last_probed_at: float = 0.0


def _now_ist() -> datetime:
    return datetime.now(ZoneInfo("Asia/Kolkata"))


def _probe_angel_one() -> dict:
    """Test Angel One: session validity + a single option chain call (NIFTY)."""
    try:
        from app.data_sources.angel import angel_session, get_option_chain
        status = angel_session.status()
        if not status["connected"]:
            return {"ok": False, "detail": "Session not connected", "session": status}

        # Try fetching NIFTY option chain — will return None after hours (expected)
        t0  = time.time()
        oc  = get_option_chain("NIFTY")
        ms  = round((time.time() - t0) * 1000)

        if oc is not None:
            strikes = len(oc.get("records", {}).get("data", []))
            return {
                "ok":          True,
                "detail":      f"Option chain OK — {strikes} strikes in {ms}ms",
                "session":     status,
                "latency_ms":  ms,
            }
        else:
            # After hours or expiry mismatch — session is fine, data is not available
            return {
                "ok":         True,
                "detail":     f"Session OK — option chain unavailable (market closed or after hours) [{ms}ms]",
                "session":    status,
                "latency_ms": ms,
            }
    except Exception as exc:
        logger.warning("api_probe angel_one: %s", exc)
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
            return {
                "ok":         True,
                "detail":     f"NSE reachable — VIX {vix} in {ms}ms",
                "vix":        vix,
                "latency_ms": ms,
            }
        return {"ok": False, "detail": f"NSE unreachable [{ms}ms]", "latency_ms": ms}
    except Exception as exc:
        logger.warning("api_probe nse: %s", exc)
        return {"ok": False, "detail": str(exc)}


def _probe_ai() -> dict:
    """Test Azure OpenAI: send a minimal regime classification request."""
    try:
        from app.services.ai import openai_enabled, get_market_regime_ai, ai_status
        if not openai_enabled():
            return {"ok": False, "detail": "AI client not configured", "status": ai_status()}

        t0     = time.time()
        result = get_market_regime_ai({
            "indiaVix":      15.0,
            "breadth":       {"advancing": 30, "declining": 20},
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
    """Test News data: yfinance headline fetch for NIFTY proxy (^NSEI)."""
    try:
        import yfinance as yf
        t0     = time.time()
        ticker = yf.Ticker("^NSEI")
        news   = ticker.news or []
        ms     = round((time.time() - t0) * 1000)

        if news:
            return {
                "ok":         True,
                "detail":     f"yfinance news OK — {len(news)} headlines in {ms}ms",
                "count":      len(news),
                "latency_ms": ms,
            }
        return {
            "ok":         True,
            "detail":     f"yfinance reachable — 0 headlines returned (may be normal) [{ms}ms]",
            "count":      0,
            "latency_ms": ms,
        }
    except Exception as exc:
        logger.warning("api_probe news: %s", exc)
        return {"ok": False, "detail": str(exc)}


def probe_all() -> dict:
    """Run all four probes and store the result. Safe to call from a background thread."""
    global _last_result, _last_probed_at

    now = _now_ist()
    logger.info("api_probe: running all probes at %s", now.strftime("%H:%M:%S IST"))

    angel = _probe_angel_one()
    nse   = _probe_nse()
    ai    = _probe_ai()
    news  = _probe_news()

    all_ok = all([angel["ok"], nse["ok"], ai["ok"], news["ok"]])

    result = {
        "probedAt":  now.isoformat(),
        "allOk":     all_ok,
        "angelOne":  angel,
        "nse":       nse,
        "ai":        ai,
        "news":      news,
    }

    _last_result    = result
    _last_probed_at = time.time()

    status = "OK" if all_ok else "DEGRADED"
    logger.info(
        "api_probe: %s — angel=%s nse=%s ai=%s news=%s",
        status,
        "OK" if angel["ok"] else "FAIL",
        "OK" if nse["ok"]   else "FAIL",
        "OK" if ai["ok"]    else "FAIL",
        "OK" if news["ok"]  else "FAIL",
    )
    return result


def get_last_result() -> dict:
    """Return the most recent probe result (empty dict if never run)."""
    return _last_result


def secs_since_last_probe() -> float | None:
    if _last_probed_at == 0.0:
        return None
    return round(time.time() - _last_probed_at)
