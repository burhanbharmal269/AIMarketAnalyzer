"""Signal domain events."""
from __future__ import annotations
from dataclasses import dataclass, field
from uuid import UUID, uuid4
from app.domain.market.events import DomainEvent


@dataclass(frozen=True)
class SignalGenerated(DomainEvent):
    signal_id:  UUID  = field(default_factory=uuid4)
    instrument: str   = ""
    direction:  str   = ""
    score:      float = 0.0
    underlying: str   = ""


@dataclass(frozen=True)
class SignalApproved(DomainEvent):
    signal_id:  UUID  = field(default_factory=uuid4)
    instrument: str   = ""
    direction:  str   = ""
    score:      float = 0.0
    lots:       int   = 0
    underlying: str   = ""
    setup_type: str   = ""


@dataclass(frozen=True)
class SignalRejected(DomainEvent):
    signal_id:  UUID        = field(default_factory=uuid4)
    instrument: str         = ""
    reasons:    tuple        = ()
    score:      float        = 0.0


@dataclass(frozen=True)
class ScanCompleted(DomainEvent):
    approved_count: int   = 0
    rejected_count: int   = 0
    duration_ms:    int   = 0
    scan_id:        int   = 0
