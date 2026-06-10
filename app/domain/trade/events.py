"""Trade domain events."""
from __future__ import annotations
from dataclasses import dataclass, field
from app.domain.market.events import DomainEvent


@dataclass(frozen=True)
class TradeOpened(DomainEvent):
    trade_id:   int   = 0
    instrument: str   = ""
    direction:  str   = ""
    entry:      float = 0.0
    stop_loss:  float = 0.0
    lots:       int   = 0


@dataclass(frozen=True)
class TradeClosed(DomainEvent):
    trade_id:   int   = 0
    instrument: str   = ""
    exit_price: float = 0.0
    pnl_r:      float = 0.0
    pnl_inr:    float = 0.0
    outcome:    str   = ""


@dataclass(frozen=True)
class SLHit(DomainEvent):
    trade_id:   int   = 0
    instrument: str   = ""
    exit_price: float = 0.0
    pnl_r:      float = 0.0


@dataclass(frozen=True)
class TargetHit(DomainEvent):
    trade_id:   int   = 0
    instrument: str   = ""
    target_num: int   = 1
    exit_price: float = 0.0
    pnl_r:      float = 0.0


@dataclass(frozen=True)
class OrderPlaced(DomainEvent):
    order_id:   str   = ""
    instrument: str   = ""
    broker:     str   = ""
    quantity:   int   = 0
    price:      float = 0.0
