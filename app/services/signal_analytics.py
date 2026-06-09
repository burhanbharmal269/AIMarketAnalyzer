"""Signal analytics — extract performance patterns from trade history.

Layer 2 of the AI shortlisting system. Currently returns empty data when no
completed trades exist. As trades accumulate in trade_journal + signal_log,
this module auto-populates the AI prompt with proven win/loss patterns.

Plug-in contract: get_performance_context() always returns a dict. The AI
prompt uses it when non-empty and silently skips it when empty.
"""
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def get_performance_context(lookback_days: int = 60) -> dict:
    """Return performance patterns from completed trades for AI grounding.

    Returns empty dict when insufficient trade history exists (<10 completed trades).
    The caller (ai.py) treats empty dict as "no historical context available".
    """
    try:
        from app.services.storage import get_journal_entries
        entries = get_journal_entries(limit=200)

        # Only use completed (win/loss) trades, not open/paper
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        completed = [
            e for e in entries
            if e.get("status") in ("win", "loss")
            and e.get("created_at") and _parse_dt(e["created_at"]) >= cutoff
        ]

        if len(completed) < 10:
            return {}   # not enough history to be statistically meaningful

        wins   = [e for e in completed if e["status"] == "win"]
        losses = [e for e in completed if e["status"] == "loss"]

        # ── Per-symbol win rates ──────────────────────────────────────────────
        sym_stats: dict[str, dict] = {}
        for e in completed:
            sym = e.get("instrument", "").split()[0]
            if not sym:
                continue
            s = sym_stats.setdefault(sym, {"w": 0, "l": 0})
            s["w" if e["status"] == "win" else "l"] += 1

        symbol_summary = {
            sym: f"{d['w']}W/{d['l']}L ({round(d['w']/(d['w']+d['l'])*100)}%)"
            for sym, d in sym_stats.items()
            if d["w"] + d["l"] >= 3   # only symbols with ≥3 trades
        }

        # ── Score bucket win rates ────────────────────────────────────────────
        buckets: dict[str, dict] = {"55-64": {"w":0,"l":0}, "65-74": {"w":0,"l":0}, "75+": {"w":0,"l":0}}
        for e in completed:
            score = e.get("confidence_score", 0) or 0
            bucket = "75+" if score >= 75 else "65-74" if score >= 65 else "55-64"
            buckets[bucket]["w" if e["status"] == "win" else "l"] += 1

        score_summary = {
            b: f"{d['w']}W/{d['l']}L ({round(d['w']/(d['w']+d['l'])*100)}%)" if d["w"]+d["l"] > 0 else "no data"
            for b, d in buckets.items()
        }

        return {
            "totalTrades":   len(completed),
            "overallWinRate": round(len(wins) / len(completed) * 100),
            "bySymbol":      symbol_summary,
            "byScoreBucket": score_summary,
            "dataWindow":    f"last {lookback_days} days",
        }

    except Exception as exc:
        logger.debug("signal_analytics failed: %s", exc)
        return {}


def _parse_dt(val) -> datetime:
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
    try:
        dt = datetime.fromisoformat(str(val))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)
