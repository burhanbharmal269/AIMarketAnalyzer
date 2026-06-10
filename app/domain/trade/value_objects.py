"""Trade domain value objects."""
from enum import Enum


class TradeStatus(str, Enum):
    PAPER    = "paper"
    OPEN     = "open"
    WIN      = "win"
    LOSS     = "loss"
    BREAKEVEN = "breakeven"
    EXPIRED  = "expired"


class ExitReason(str, Enum):
    SL_HIT    = "sl_hit"
    T1_HIT    = "t1_hit"
    T2_HIT    = "t2_hit"
    T3_HIT    = "t3_hit"
    MANUAL    = "manual"
    EXPIRED   = "expired"
    TIME_STOP = "time_stop"


class OrderStatus(str, Enum):
    PENDING  = "PENDING"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
