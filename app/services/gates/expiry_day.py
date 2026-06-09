"""Expiry day gate — blocks new weekly long-option entries on Tuesday after 11:00 IST.

NSE moved weekly F&O expiry to Tuesday (effective 2025-09-01). Gamma accelerates
and time decay is punishing for long options after 11:00 on expiry morning.
"""
from datetime import time as dtime
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.constants import EXPIRY_GATE_HOUR
from app.services.gates.base import BaseGate

_TUESDAY = 1   # datetime.weekday() → 0=Mon, 1=Tue


class ExpiryDayGate(BaseGate):
    def check(self, candidate, market, risk_state, settings) -> str | None:
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        if now.weekday() == _TUESDAY and now.time() >= dtime(EXPIRY_GATE_HOUR, 0):
            if candidate.get("expiry") == "Weekly":
                return (
                    "Weekly expiry day (Tuesday) after 11:00 IST — accelerated gamma and "
                    "time decay make new long-option entries unfavourable."
                )
        return None
