"""Scoring engine — aggregates all IScorer instances into a single result dict."""
from __future__ import annotations

from app.core.constants import SCORE_MAX_RAW
from app.core.interfaces import IScorer


class ScoringEngine:
    """Runs every registered scorer and normalises the raw total to 0–100."""

    def __init__(self, scorers: list[IScorer]) -> None:
        self._scorers = scorers

    def score(self, candidate: dict, market: dict) -> dict:
        scores = {s.category: s.score(candidate, market) for s in self._scorers}
        raw    = sum(scores.values())
        total  = round(raw / SCORE_MAX_RAW * 100)
        return {"scores": scores, "total": total, "rawTotal": raw}

    @property
    def scorers(self) -> list[IScorer]:
        return list(self._scorers)
