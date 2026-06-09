"""Lightweight domain helpers shared across scoring, gates, and strategies.

No imports from app.services or app.data_sources — this stays pure.
"""


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def is_bullish_trend(candidate: dict) -> bool:
    """EMA20 > EMA50 > EMA200 — daily macro uptrend."""
    return candidate["ema20"] > candidate["ema50"] > candidate["ema200"]


def is_bearish_trend(candidate: dict) -> bool:
    """EMA20 < EMA50 < EMA200 — daily macro downtrend."""
    return candidate["ema20"] < candidate["ema50"] < candidate["ema200"]


def trend_aligned(candidate: dict) -> bool:
    """True when the candidate's declared direction matches the EMA stack."""
    if candidate["direction"] == "BUY":
        return is_bullish_trend(candidate)
    return is_bearish_trend(candidate)
