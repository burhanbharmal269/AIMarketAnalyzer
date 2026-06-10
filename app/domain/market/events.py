"""Market domain events."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4


@dataclass(frozen=True)
class DomainEvent:
    event_id:    UUID     = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class OptionChainFetched(DomainEvent):
    symbol:       str   = ""
    source:       str   = ""
    strike_count: int   = 0
    ce_oi:        float = 0.0
    pe_oi:        float = 0.0
    pcr:          float = 0.0


@dataclass(frozen=True)
class MarketDataFetchFailed(DomainEvent):
    symbol:    str = ""
    provider:  str = ""
    reason:    str = ""


@dataclass(frozen=True)
class VixUpdated(DomainEvent):
    vix: float = 0.0
