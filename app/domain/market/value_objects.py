"""Market domain value objects — immutable, no external deps."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import NewType

# Semantic type aliases — help IDEs and type-checkers catch mix-ups
Symbol    = NewType("Symbol",    str)
Price     = NewType("Price",     float)
Volume    = NewType("Volume",    int)
OI        = NewType("OI",        float)
IVPercent = NewType("IVPercent", float)


class OptionType(str, Enum):
    CALL = "CE"
    PUT  = "PE"


class CandleInterval(str, Enum):
    ONE_MIN     = "1m"
    THREE_MIN   = "3m"
    FIVE_MIN    = "5m"
    TEN_MIN     = "10m"
    FIFTEEN_MIN = "15m"
    ONE_HOUR    = "1h"
    ONE_DAY     = "1d"

    def to_angel_fmt(self) -> str:
        mapping = {
            "1m":  "ONE_MINUTE",   "3m":  "THREE_MINUTE",
            "5m":  "FIVE_MINUTE",  "10m": "TEN_MINUTE",
            "15m": "FIFTEEN_MINUTE", "1h": "ONE_HOUR",
            "1d":  "ONE_DAY",
        }
        return mapping[self.value]


@dataclass(frozen=True)
class Expiry:
    """NSE option expiry — knows how to format itself for each broker."""
    date: date

    def to_angel_fmt(self) -> str:
        """'30Jun2026' — Angel One ScripMaster / optionGreek format."""
        return self.date.strftime("%d%b%Y")

    def to_nse_fmt(self) -> str:
        """'30-Jun-2026' — NSE option chain API format."""
        return self.date.strftime("%d-%b-%Y")

    def to_display(self) -> str:
        """'30 Jun 2026' — human-readable."""
        return self.date.strftime("%d %b %Y")

    def to_short(self) -> str:
        """'30Jun26' — compact display."""
        return self.date.strftime("%d%b%y")

    @classmethod
    def from_angel(cls, s: str) -> "Expiry":
        """Parse '30Jun2026' → Expiry."""
        from datetime import datetime
        return cls(datetime.strptime(s.upper(), "%d%b%Y").date())

    @classmethod
    def from_nse(cls, s: str) -> "Expiry":
        """Parse '30-Jun-2026' → Expiry."""
        from datetime import datetime
        return cls(datetime.strptime(s, "%d-%b-%Y").date())

    def __lt__(self, other: "Expiry") -> bool:
        return self.date < other.date
