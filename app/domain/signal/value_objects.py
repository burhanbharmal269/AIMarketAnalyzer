"""Signal domain value objects."""
from __future__ import annotations
from enum import Enum


class Direction(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


class SetupType(str, Enum):
    ORB         = "ORB"
    ORB_CONT    = "ORB Cont"
    SR_BREAK    = "S/R Break"
    PDH_L_BREAK = "PDH/L Break"
    MOMENTUM    = "Momentum"
    TREND       = "Trend"
    GAP_PLAY    = "Gap Play"
    REVERSAL    = "Reversal"


class SignalGrade(str, Enum):
    A_PLUS = "A+"
    A      = "A"
    B      = "B"
    C      = "C"
    D      = "D"

    @classmethod
    def from_score(cls, score: float) -> "SignalGrade":
        if score >= 85: return cls.A_PLUS
        if score >= 75: return cls.A
        if score >= 65: return cls.B
        if score >= 55: return cls.C
        return cls.D
