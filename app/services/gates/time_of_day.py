"""Time-of-day gates — avoid opening chop and close volatility."""
from datetime import time as dtime
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.constants import (
    MARKET_OPEN_H, MARKET_OPEN_M,
    OPENING_VOL_END_H, OPENING_VOL_END_M,
    CLOSING_VOL_START_H, CLOSING_VOL_START_M,
    MARKET_CLOSE_H, MARKET_CLOSE_M,
)
from app.services.gates.base import BaseGate

_OPENING_START = dtime(MARKET_OPEN_H,       MARKET_OPEN_M)
_OPENING_END   = dtime(OPENING_VOL_END_H,   OPENING_VOL_END_M)
_CLOSING_START = dtime(CLOSING_VOL_START_H, CLOSING_VOL_START_M)
_CLOSING_END   = dtime(MARKET_CLOSE_H,      MARKET_CLOSE_M)


class OpeningVolatilityGate(BaseGate):
    """Block new entries during the first 15 minutes of the session."""

    def check(self, candidate, market, risk_state, settings) -> str | None:
        now = datetime.now(ZoneInfo("Asia/Kolkata")).time()
        if _OPENING_START <= now <= _OPENING_END:
            return "Opening volatility window (9:15–9:30 IST) — wait for price discovery."
        return None


class ClosingVolatilityGate(BaseGate):
    """Block new entries in the last 45 minutes to avoid close-driven spikes."""

    def check(self, candidate, market, risk_state, settings) -> str | None:
        now = datetime.now(ZoneInfo("Asia/Kolkata")).time()
        if _CLOSING_START <= now <= _CLOSING_END:
            return "Closing volatility window (14:45–15:30 IST) — avoid new entries near close."
        return None
