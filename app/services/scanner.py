"""Public scanning API — thin delegator.

All business logic lives in:
  app/services/strategies/  — scan orchestration + position sizing
  app/services/scoring/     — per-category scoring classes
  app/services/gates/       — per-rule hard gates

This file preserves the original public surface so every existing import
(main.py, tests, scripts) works without any changes.
"""
from __future__ import annotations

from app.core.constants import SCORE_CATEGORIES, SCORE_MAX_RAW, DEFAULT_SCAN_SETTINGS
from app.services.strategies.options import OptionsTradingStrategy

# ── Backward-compatible aliases ───────────────────────────────────────────────
CATEGORY_MAX   = SCORE_CATEGORIES
_SCORE_MAX_RAW = SCORE_MAX_RAW
DEFAULT_SETTINGS = DEFAULT_SCAN_SETTINGS

# ── Singleton strategy ────────────────────────────────────────────────────────
_options_strategy = OptionsTradingStrategy()


def scan_market(
    candidates: list[dict],
    market: dict,
    risk_state: dict,
    settings: dict | None = None,
) -> dict:
    """Run the full options scan.  settings overrides DEFAULT_SETTINGS keys."""
    merged = DEFAULT_SCAN_SETTINGS.copy()
    if settings:
        merged.update({k: v for k, v in settings.items() if v is not None})
    return _options_strategy.run_scan(candidates, market, risk_state, merged)


# ── Utility functions (called by main.py / telegram.py) ──────────────────────

def build_risks(candidate: dict, market: dict) -> list[str]:
    return OptionsTradingStrategy._build_risks(candidate, market)


def telegram_text(scan: dict, market: dict) -> str:
    if scan["noTrade"]:
        return "\n".join([
            "NO TRADE MODE",
            f"Market: {market['regime']}",
            "Reason: No setup passed hard risk gates and score threshold.",
            "Action: Preserve capital. Wait for clean alignment.",
        ])

    messages = []
    for item in scan["approved"]:
        c = item["candidate"]
        messages.append("\n".join([
            f"{c['direction']} {c['instrument']}",
            f"Entry: {c['entry']}",
            f"SL: {c['stopLoss']}",
            f"T1/T2/T3: {' / '.join(str(t) for t in c['targets'])}",
            f"RR: 1:{c['rr']}",
            f"Confidence Score: {item['score']['total']}/100",
            f"Valid Until: {item['validUntil']}",
            f"Size: {item['sizing']['lots']} lot(s), max risk Rs {item['sizing']['rupeeRisk']}",
            f"Why: {item['explanation']}",
            f"Risks: {'; '.join(item['risks'])}",
        ]))
    return "\n\n".join(messages)


def position_sizing(candidate: dict, settings: dict) -> dict:
    """Exposed for any callers that compute sizing independently."""
    return _options_strategy.compute_position_size(candidate, settings)
