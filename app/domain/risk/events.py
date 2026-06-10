"""Risk domain events."""
from __future__ import annotations
from dataclasses import dataclass
from app.domain.market.events import DomainEvent


@dataclass(frozen=True)
class DrawdownLimitReached(DomainEvent):
    limit_type: str   = ""   # "daily" | "weekly" | "monthly"
    current:    float = 0.0
    limit:      float = 0.0


@dataclass(frozen=True)
class LossStreakAlert(DomainEvent):
    streak:     int   = 0
    threshold:  int   = 3


@dataclass(frozen=True)
class PositionSizeRejected(DomainEvent):
    instrument: str   = ""
    lot_risk:   float = 0.0
    budget:     float = 0.0
    reason:     str   = ""
