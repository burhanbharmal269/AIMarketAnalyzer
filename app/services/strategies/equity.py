"""Equity signal strategy — stub for future short-term and long-term equity signals.

When you are ready to build equity signals:
  1. Replace NotImplementedError bodies with real logic.
  2. Create equity-specific scorers in app/services/scoring/ (e.g. FundamentalScorer).
  3. Create equity-specific gates (e.g. EarningsWindowGate with a longer horizon).
  4. Register this strategy in the scan router or scan service alongside OptionsTradingStrategy.

The interface is identical to OptionsTradingStrategy — zero changes needed in the
HTTP layer or orchestration code to add this strategy.
"""
from __future__ import annotations

from app.services.strategies.base import BaseSignalStrategy


class EquitySwingStrategy(BaseSignalStrategy):
    """Short-term equity directional signals (2–10 day hold)."""
    signal_type = "equity_swing"

    def score_candidate(self, candidate: dict, market: dict) -> dict:
        raise NotImplementedError("EquitySwingStrategy scoring not yet implemented.")

    def check_gates(self, candidate, market, risk_state, settings) -> list[str]:
        raise NotImplementedError("EquitySwingStrategy gates not yet implemented.")

    def compute_position_size(self, candidate: dict, settings: dict) -> dict:
        raise NotImplementedError("EquitySwingStrategy position sizing not yet implemented.")


class EquityLongTermStrategy(BaseSignalStrategy):
    """Long-term equity position signals (weeks to months hold)."""
    signal_type = "equity_longterm"

    def score_candidate(self, candidate: dict, market: dict) -> dict:
        raise NotImplementedError("EquityLongTermStrategy scoring not yet implemented.")

    def check_gates(self, candidate, market, risk_state, settings) -> list[str]:
        raise NotImplementedError("EquityLongTermStrategy gates not yet implemented.")

    def compute_position_size(self, candidate: dict, settings: dict) -> dict:
        raise NotImplementedError("EquityLongTermStrategy position sizing not yet implemented.")
