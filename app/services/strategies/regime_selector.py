"""Strategy regime selector.

Picks the most appropriate trading strategy for the current market conditions
based on proven NSE F&O research (ORB 8yr backtest, VWAP momentum, EMA continuation,
mean reversion). The selected strategy shapes:
  - Which candidates the AI shortlists (via get_candidate_shortlist)
  - Which entry signals are weighted most heavily in scoring
  - What the AI summary tells traders to focus on

Strategy hierarchy (checked in order, first match wins):
  1. ORB      — session open window + some trend
  2. Momentum — strong trend (ADX > 25)
  3. VWAP     — mild trend (ADX 18-25) + VWAP data available
  4. MeanRev  — sideways/choppy (ADX < 18), option buying is lowest confidence
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ── Strategy definitions ──────────────────────────────────────────────────────

STRATEGIES: dict[str, dict] = {
    "orb": {
        "name":        "Opening Range Breakout",
        "shortName":   "ORB",
        "description": (
            "Buy ATM CE/PE on confirmed breakout above/below the 9:15–9:30 opening range. "
            "Volume on breakout candle must be ≥ 1.5x average. "
            "Highest win rate on expiry days (Tuesday for weekly, last Thursday for monthly). "
            "Typical target: 1.5–2.5R. Stop: below/above ORB boundary."
        ),
        "entryRSI":    (38, 80),   # wide — breakout energy can push RSI high quickly
        "minADX":      18,
        "preferIndex": True,       # index options have cleanest ORB breakouts
        "ivRankMax":   50,
        "typicalWinRate": "48-65%",
        "typicalRR":   "1:2",
        "aiHint": (
            "Prioritise NIFTY and BANKNIFTY — they have the most reliable ORB breakouts. "
            "For stocks, require: ORB breakout (pdBreakout=True), volume spike (volumeSpike=True), "
            "and relVol >= 1.5. RSI can be 38-80 — breakout momentum often pushes RSI above 70 quickly. "
            "MACD confirmation preferred but not required if volume is strong."
        ),
    },

    "momentum": {
        "name":        "Momentum Continuation",
        "shortName":   "Momentum",
        "description": (
            "EMA stack fully aligned (9 > 21 > 50 on 15-min, or 20 > 50 > 200 daily). "
            "Enter on first pullback candle that touches EMA 9/20 and closes above/below it. "
            "ADX > 25 confirms trend strength. "
            "Strongest post-budget, RBI policy days, and sustained FII flow sessions."
        ),
        "entryRSI":    (45, 76),   # trend continuation zone — avoid overbought extremes
        "minADX":      25,
        "preferIndex": False,      # stocks can show stronger momentum continuation
        "ivRankMax":   45,
        "typicalWinRate": "52-68%",
        "typicalRR":   "1:2.5",
        "aiHint": (
            "Require full EMA alignment (ema=aligned). ADX must be >= 25 — below this is a weak trend. "
            "RSI 45-76 is the sweet spot for continuation entries. "
            "MACD must confirm (macd=confirms) — diverging MACD means momentum is stalling. "
            "Prefer adxRising=True (accelerating trend). One symbol per sector max."
        ),
    },

    "vwap": {
        "name":        "VWAP Momentum",
        "shortName":   "VWAP",
        "description": (
            "Price holds above/below VWAP on a mildly trending day. "
            "Enter on pullback to VWAP where RSI cools to 45–58 zone (not oversold, not overbought). "
            "Best when India VIX < 18 — cleaner directional moves. "
            "Win rate degrades sharply in choppy sessions — require VWAP confirmed as filter."
        ),
        "entryRSI":    (42, 68),   # RSI should cool to mid-zone on pullback
        "minADX":      16,
        "preferIndex": False,
        "ivRankMax":   45,
        "typicalWinRate": "55-68%",
        "typicalRR":   "1:2",
        "aiHint": (
            "Require vwap=yes (price confirmed above/below VWAP). "
            "RSI should be in the 42-68 cooldown zone — a pullback, not a reversal. "
            "EMA alignment preferred but partial alignment acceptable if VWAP holds. "
            "relVol >= 0.9 shows enough participation. Skip if MACD shows strong divergence."
        ),
    },

    "mean_reversion": {
        "name":        "Mean Reversion",
        "shortName":   "MeanRev",
        "description": (
            "Sideways/range-bound market. "
            "Fade extremes at Bollinger Band touch + RSI < 30 (buy call) or RSI > 70 (buy put). "
            "PCR > 1.3 = oversold bounce signal; PCR < 0.7 = overbought fade. "
            "Option buying win rate is lowest here — consider smaller size or skip buying entirely. "
            "IV Rank < 30 required (cheap premium only). Hard stop: 30% option premium loss."
        ),
        "entryRSI":    (22, 36),   # look for oversold extremes only
        "minADX":      0,
        "preferIndex": True,       # index options more liquid for mean reversion
        "ivRankMax":   35,         # strict — never buy expensive options in choppy markets
        "typicalWinRate": "45-60%",
        "typicalRR":   "1:1.5",
        "aiHint": (
            "Look for RSI extremes: < 30 for BUY, > 70 for SELL. "
            "Require IV Rank < 35 (cheap options only — paying full premium in range market is a losing edge). "
            "PCR signal from option chain preferred. "
            "EMA alignment is less important — range-bound stocks often have mixed EMA. "
            "Skip any symbol with ADX > 22 (those are trending, wrong strategy)."
        ),
    },
}


def select_strategy(market: dict, candidates: list[dict]) -> dict:
    """Choose the best strategy for current market conditions.

    Returns the full strategy dict with an added 'selectedBy' key explaining the reason.
    """
    vix       = market.get("indiaVix", 15)
    regime    = market.get("regime", "neutral")
    ai_action = market.get("aiAction", "selective")

    # Average ADX across candidates — proxy for overall market trend strength
    avg_adx = (
        sum(c.get("adx") or 0 for c in candidates) / len(candidates)
        if candidates else 0
    )

    # Check if we're in the ORB entry window (9:15–10:30 IST)
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    in_orb  = dtime(9, 15) <= now_ist.time() <= dtime(10, 30)

    # ai_action=avoid → mean reversion only (smallest position, index hedges)
    if ai_action == "avoid":
        key = "mean_reversion"
        reason = f"AI action=avoid — only defensive mean-reversion setups (avg ADX {avg_adx:.0f})"

    elif in_orb and avg_adx >= 18:
        key    = "orb"
        reason = f"Inside ORB window (9:15–10:30 IST), avg ADX {avg_adx:.0f} >= 18"

    elif avg_adx >= 25:
        key    = "momentum"
        reason = f"Strong trend: avg ADX {avg_adx:.0f} >= 25"

    elif avg_adx >= 16:
        # VWAP strategy needs live VWAP data — check if any candidate has it confirmed
        vwap_available = any(c.get("vwapConfirmed") for c in candidates)
        if vwap_available:
            key    = "vwap"
            reason = f"Mild trend: avg ADX {avg_adx:.0f} (16-25) with VWAP data available"
        else:
            key    = "momentum"
            reason = f"Mild trend: avg ADX {avg_adx:.0f}, VWAP unavailable (market closed?) — using momentum"

    else:
        key    = "mean_reversion"
        reason = f"Sideways/choppy: avg ADX {avg_adx:.0f} < 16 — option buying low confidence"

    strategy = {**STRATEGIES[key], "key": key, "selectedBy": reason}
    logger.info("Strategy selected: %s — %s", strategy["shortName"], reason)
    return strategy


def strategy_summary(strategy: dict) -> str:
    """One-line summary for logging and API responses."""
    return f"{strategy['name']} ({strategy['shortName']}) | WinRate: {strategy['typicalWinRate']} | RR: {strategy['typicalRR']}"
