"""IV Rank gate — blocks option buying when IV is at historical extremes."""
from app.core.constants import MIN_IV_RANK_GATE
from app.services.gates.base import BaseGate


class IvRankGate(BaseGate):
    """Requires 20+ daily IV readings to fire; skipped when history is insufficient."""

    def check(self, candidate, market, risk_state, settings) -> str | None:
        iv_rank = candidate.get("ivRank")
        if iv_rank is not None and iv_rank > MIN_IV_RANK_GATE:
            return (
                f"IV Rank {iv_rank:.0f}th percentile — options are historically expensive. "
                "IV crush risk too high."
            )
        return None
