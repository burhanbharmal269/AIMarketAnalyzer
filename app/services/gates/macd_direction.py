"""MACD direction gate — MACD line must confirm trade direction.

Research basis (deep-research-report): MACD bullish is an explicit ENTRY FILTER
for bull call spreads, not just a scoring factor. A directional option trade
against MACD momentum has structurally lower win rates because momentum is the
primary edge in intraday options — it must be confirmed, not just hoped for.
"""
from app.services.gates.base import BaseGate


class MacdDirectionGate(BaseGate):
    def check(self, candidate, market, risk_state, settings) -> str | None:
        macd   = candidate.get("macd", 0.0)
        signal = candidate.get("macdSignal", 0.0)
        direction = candidate["direction"]

        if direction == "BUY" and macd <= signal:
            return (
                "MACD is bearish (MACD line below signal) — momentum does not "
                "support a BUY entry. Wait for MACD crossover before entering calls."
            )
        if direction == "SELL" and macd >= signal:
            return (
                "MACD is bullish (MACD line above signal) — momentum does not "
                "support a SELL entry. Wait for MACD crossover before entering puts."
            )
        return None
