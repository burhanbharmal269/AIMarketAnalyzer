"""RSI extremes gate — blocks entries that chase overbought/oversold conditions.

Research basis (deep-research-report): Bull call spread entry requires RSI < 60
(momentum building, not exhausted). At RSI >78, a stock/index has already made
its move — IV spikes, option premiums are inflated, and mean-reversion risk is
high. Buying calls at RSI 80+ is the most common retail mistake: you pay peak
premium for a move that is almost certainly over.

Symmetric rule applies for puts: RSI <22 = extreme oversold = bounce risk.
"""
from app.core.constants import RSI_OVERBOUGHT_GATE, RSI_OVERSOLD_GATE
from app.services.gates.base import BaseGate


class RsiExtremeGate(BaseGate):
    def check(self, candidate, market, risk_state, settings) -> str | None:
        rsi       = candidate.get("rsi", 50)
        direction = candidate["direction"]

        if direction == "BUY" and rsi > RSI_OVERBOUGHT_GATE:
            return (
                f"RSI {rsi:.0f} is overbought (>{RSI_OVERBOUGHT_GATE}). "
                "Buying calls at RSI extremes means paying inflated premium for "
                "a move already priced in — IV crush risk is very high."
            )
        if direction == "SELL" and rsi < RSI_OVERSOLD_GATE:
            return (
                f"RSI {rsi:.0f} is oversold (<{RSI_OVERSOLD_GATE}). "
                "Buying puts at extreme oversold levels carries high bounce-reversion "
                "risk — wait for a confirmed breakdown before entering puts."
            )
        return None
