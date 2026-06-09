"""Gate engine — runs all registered IGate instances and collects failures."""
from __future__ import annotations

from app.core.interfaces import IGate


class GateEngine:
    """Evaluates every gate and returns all failure reasons (not just the first)."""

    def __init__(self, gates: list[IGate]) -> None:
        self._gates = gates

    def check(
        self,
        candidate: dict,
        market: dict,
        risk_state: dict,
        settings: dict,
    ) -> list[str]:
        """Return a list of failure reasons. Empty list = all gates passed."""
        failures = []
        for gate in self._gates:
            reason = gate.check(candidate, market, risk_state, settings)
            if reason:
                failures.append(reason)
        return failures

    @property
    def gates(self) -> list[IGate]:
        return list(self._gates)
