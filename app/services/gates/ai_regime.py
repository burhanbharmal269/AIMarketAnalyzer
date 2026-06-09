"""AI regime gate — blocks trading when the AI classifier flags untradeable conditions."""
from app.services.gates.base import BaseGate


class AiRegimeGate(BaseGate):
    """Only fires when AI is configured and explicitly returns action='avoid'.
    When AI is absent (aiAction is None), this gate is a no-op.
    """

    def check(self, candidate, market, risk_state, settings) -> str | None:
        if market.get("aiAction") == "avoid":
            reason = market.get("aiReason", "AI classified current conditions as untradeable.")
            return f"AI regime gate: {reason}"
        return None
