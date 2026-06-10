"""Scan orchestration service.

Owns: build_scan(), scan cache, auto-paper-trade journaling, and failure alerting.
Previously embedded in main.py — extracted here so the HTTP layer stays thin.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from app.core.constants import SCAN_CACHE_TTL_SECS, SCAN_AUDIT_KEEP_DAYS, SCORE_CATEGORIES, SCORE_MAX_RAW, SP500_GATE_PCT

logger = logging.getLogger(__name__)

# Category maxima normalised to 100 for frontend bar widths.
CATEGORY_MAX_NORM: dict[str, int] = {
    k: round(v / SCORE_MAX_RAW * 100) for k, v in SCORE_CATEGORIES.items()
}

# ── Scan cache (module-level, process lifetime) ───────────────────────────────
_scan_cache:    dict | None     = None
_scan_cache_at: datetime | None = None


def cache_scan(result: dict) -> None:
    global _scan_cache, _scan_cache_at
    _scan_cache    = result
    _scan_cache_at = datetime.now(timezone.utc)


def get_cached_scan() -> dict | None:
    if _scan_cache is None or _scan_cache_at is None:
        return None
    age = (datetime.now(timezone.utc) - _scan_cache_at).total_seconds()
    return _scan_cache if age < SCAN_CACHE_TTL_SECS else None


def get_cache_timestamp() -> str | None:
    return _scan_cache_at.isoformat() if _scan_cache_at else None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _live_data() -> tuple[list[dict], dict]:
    """Fetch live candidates and market snapshot from NSE. Raises on hard failures."""
    from app.data_sources.nse import nse_data
    market     = nse_data.get_market_snapshot()
    candidates = nse_data.get_live_candidates()
    if nse_data.last_nifty_direction:
        market["niftyDirection"] = nse_data.last_nifty_direction
    return candidates, market


def notify_scan_failure(reason: str) -> None:
    """Queue a Telegram alert so the trader knows the scan failed."""
    logger.error("Scan failed: %s", reason)
    try:
        from app.services.storage import enqueue_alert
        enqueue_alert(
            f"SCAN FAILED\n{reason}\nCheck NSE connectivity. Do NOT trade on stale data."
        )
    except Exception as exc:
        logger.error("Could not queue scan failure alert: %s", exc)


def _auto_paper_trade(item: dict, signal_id: int | None = None) -> None:
    """Log an approved signal as a paper trade if no open position for that underlying.

    Deduplicates by underlying — we only trade one direction per underlying at a time.
    Links trade_journal row ↔ signal_log row via signal_id.
    """
    from app.services.storage import get_journal_entries, add_journal_entry, link_signal_to_journal

    c          = item["candidate"]
    instrument = c.get("instrument", "")
    underlying = c.get("underlying", instrument.split()[0] if instrument else "")

    existing = [
        e for e in get_journal_entries(limit=50)
        if (e["instrument"].split()[0] == underlying or e["instrument"] == instrument)
        and e["status"] in ("open", "paper")
    ]
    if existing:
        if signal_id and not existing[0].get("signal_id"):
            try:
                link_signal_to_journal(signal_id, existing[0]["id"])
            except Exception:
                pass
        return

    journal_id = add_journal_entry({
        "instrument":      instrument,
        "direction":       c.get("direction", "BUY"),
        "entry":           c.get("entry", 0),
        "stopLoss":        c.get("stopLoss", 0),
        "targets":         c.get("targets", [0, 0, 0]),
        "confidenceScore": item.get("score", {}).get("total", 0),
        "status":          "paper",
        "notes":           f"Auto-logged. Score: {item.get('score', {}).get('total', 0)}/100",
    })
    if signal_id and journal_id:
        try:
            link_signal_to_journal(signal_id, journal_id)
        except Exception:
            pass
    logger.info("Auto-paper-trade logged: %s (signal_id=%s)", instrument, signal_id)


# ── Main entry point ──────────────────────────────────────────────────────────

def build_scan(
    settings_payload: dict | None = None,
    persist: bool = True,
) -> dict:
    """Run a full live market scan.

    Args:
        settings_payload: Overrides for DEFAULT_SCAN_SETTINGS.
        persist:          Write scan/signals to DB and update the cache.
                          Pass False for preview/summary calls that should not
                          create journal entries or audit rows.

    Raises:
        RuntimeError / Exception: when NSE data is unavailable (caller must handle).
    """
    from app.services.ai      import openai_enabled, get_market_regime_ai, get_batch_news_sentiment, generate_trade_explanation
    from app.services.scanner import scan_market
    from app.services.storage import (
        compute_risk_state, record_scan, record_approved_signals,
        prune_scan_audit,
    )

    candidates, market = _live_data()

    # ── Market-level PCR from NIFTY option chain (no extra API call) ─────────
    # NIFTY's total PE OI / CE OI = the market's institutional PCR signal.
    # PCR >1.2 = put writers dominant (hedging) → contrarian bullish.
    # PCR <0.8 = call writers dominant → contrarian bearish / bullish caution.
    nifty_cand = next((c for c in candidates if c.get("underlying") == "NIFTY"), None)
    if nifty_cand and nifty_cand.get("pcr"):
        market["marketPcr"] = nifty_cand["pcr"]

    # ── Global cues gate: block BUY/CE when S&P 500 prev session < -1% ───────
    # A US drop of >1% overnight correlates with NSE gap-down opens and elevated
    # CE risk. On such days only SELL/PE setups are permitted.
    try:
        from app.services.ai import get_sp500_change
        sp_chg = get_sp500_change()
        if sp_chg is not None and sp_chg < SP500_GATE_PCT:
            before_gate = len(candidates)
            candidates  = [c for c in candidates if c.get("direction") != "BUY"]
            blocked     = before_gate - len(candidates)
            market["globalCuesGate"] = {
                "triggered": True,
                "sp500Change": sp_chg,
                "threshold": SP500_GATE_PCT,
                "blockedBuyCandidates": blocked,
            }
            logger.info(
                "Global cues gate: S&P500 %+.2f%% (threshold %.1f%%) — blocked %d BUY/CE candidates",
                sp_chg, SP500_GATE_PCT, blocked,
            )
        else:
            market["globalCuesGate"] = {"triggered": False, "sp500Change": sp_chg}
    except Exception as exc:
        logger.warning("Global cues gate skipped (S&P 500 data unavailable): %s", exc)
        market["globalCuesGate"] = {"triggered": False, "sp500Change": None, "error": str(exc)}

    # ── AI: regime + shortlist + news sentiment — run in parallel ────────────
    # Previously sequential (regime → shortlist → news = ~15-25s total).
    # Now: regime and news headlines fetch run concurrently; shortlist uses
    # regime result which is ready by the time shortlist needs it.
    shortlist_result = {"shortlist": None, "skipped": {}, "regimeNote": "", "source": "fallback"}
    if openai_enabled():
        from app.services.ai import get_candidate_shortlist
        from app.data_sources.news import get_headlines

        underlyings = list({c.get("underlying", "") for c in candidates if c.get("underlying")})

        def _fetch_regime():
            try:
                r = get_market_regime_ai(market)
                logger.info("AI regime: action=%s regime=%s", r.get("aiAction"), r.get("aiRegime"))
                return ("regime", r)
            except Exception as exc:
                logger.warning("AI regime skipped: %s", exc)
                return ("regime", {})

        def _fetch_headlines():
            try:
                result = {sym: get_headlines(sym) for sym in underlyings}
                return ("headlines", result)
            except Exception as exc:
                logger.warning("News headlines skipped: %s", exc)
                return ("headlines", {})

        # Run regime + headline fetches concurrently
        symbol_headlines: dict = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_fetch_regime), pool.submit(_fetch_headlines)]
            for f in as_completed(futures):
                key, val = f.result()
                if key == "regime":
                    market.update(val)
                else:
                    symbol_headlines = val

        # Shortlist uses regime result (now ready), news sentiment batched together
        try:
            shortlist_result = get_candidate_shortlist(candidates, market)
            shortlist_syms   = set(shortlist_result["shortlist"])
            before           = len(candidates)
            candidates       = [c for c in candidates if c["underlying"] in shortlist_syms]
            logger.info(
                "AI shortlist (%s): %d/%d candidates — skipped %d. RegimeNote: %s",
                shortlist_result["source"], len(candidates), before,
                len(shortlist_result["skipped"]),
                shortlist_result.get("regimeNote", "")[:80],
            )
        except Exception as exc:
            logger.warning("AI shortlist skipped: %s", exc)

        # Batch news sentiment (one AI call for all underlyings)
        try:
            filtered_headlines = {
                sym: symbol_headlines[sym]
                for sym in {c.get("underlying", "") for c in candidates}
                if sym in symbol_headlines
            }
            sentiments = get_batch_news_sentiment(filtered_headlines)
            for c in candidates:
                c["newsSentiment"] = sentiments.get(c.get("underlying", ""), 0)
            logger.info("News sentiment injected for %d underlyings", len(sentiments))
        except Exception as exc:
            logger.warning("News sentiment skipped: %s", exc)

    risk_pct   = (settings_payload or {}).get("riskPercent", 2.0)
    risk_state = compute_risk_state(risk_pct=risk_pct)

    # Journal-computed loss streak overrides manual input if higher (safer)
    journal_streak = risk_state.get("lossStreak", 0)
    if settings_payload:
        manual_streak             = settings_payload.get("lossStreak", 0)
        settings_payload["lossStreak"] = max(journal_streak, manual_streak)
    else:
        settings_payload = {"lossStreak": journal_streak}

    scan = scan_market(candidates, market, risk_state, settings_payload)

    # ── AI: per-signal trade explanations — parallel across all signals ──────
    # Previously serial (5 signals × ~3s = 15s). Now parallel: all signals
    # sent concurrently, total time = slowest single explanation (~3-5s).
    if openai_enabled() and scan["approved"]:
        def _explain(item):
            try:
                item["explanation"] = generate_trade_explanation(
                    item["candidate"], item["score"], market
                )
            except Exception as exc:
                logger.warning("Explanation failed for %s: %s",
                               item.get("candidate", {}).get("instrument"), exc)

        with ThreadPoolExecutor(max_workers=min(5, len(scan["approved"]))) as pool:
            list(pool.map(_explain, scan["approved"]))

    # ── Data source diagnostics — visible in UI so nothing is hidden ─────────
    from app.data_sources.kite import KITE_AVAILABLE
    diagnostics = {
        "kiteConnected":  KITE_AVAILABLE,
        "kiteOcScope":    "full F&O universe via Kite Connect",
        "aiEnabled":      openai_enabled(),
        "globalCuesGate": market.get("globalCuesGate", {}),
    }

    funnel_log = scan.pop("funnelLog", {})
    result = {
        "market":      market,
        "categoryMax": CATEGORY_MAX_NORM,
        "dataSource":  "live",
        "lossStreak":  journal_streak,
        "aiShortlist": shortlist_result,
        "diagnostics": diagnostics,
        "funnelLog":   funnel_log,
        **scan,
    }

    if persist:
        scan_id: int | None = None
        try:
            scan_id = record_scan(scan)
        except Exception as exc:
            logger.warning("record_scan failed: %s", exc)
        try:
            prune_scan_audit(keep_days=SCAN_AUDIT_KEEP_DAYS)
        except Exception as exc:
            logger.debug("scan_audit prune skipped: %s", exc)

        cache_scan(result)

        signal_ids: list[int] = []
        try:
            signal_ids = record_approved_signals(scan_id, scan["approved"], market)
        except Exception as exc:
            logger.warning("record_approved_signals failed: %s", exc)

        for item, sig_id in zip(
            scan["approved"],
            signal_ids or [None] * len(scan["approved"]),
        ):
            try:
                _auto_paper_trade(item, signal_id=sig_id)
            except Exception as exc:
                logger.warning(
                    "Auto-paper-trade failed for %s: %s",
                    item.get("candidate", {}).get("instrument"),
                    exc,
                )

    return result
