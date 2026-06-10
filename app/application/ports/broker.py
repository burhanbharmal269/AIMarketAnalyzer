"""IBrokerProvider — secondary port for order execution.

Domain logic must NEVER import broker SDKs directly.
All broker communication flows through this interface.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"
    SL     = "SL"
    SL_M   = "SL-M"


class ProductType(str, Enum):
    INTRADAY = "INTRADAY"
    DELIVERY = "DELIVERY"
    MTF      = "MTF"


class OrderSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


@dataclass
class OrderRequest:
    symbol:          str
    exchange:        str
    token:           str
    side:            OrderSide
    quantity:        int
    order_type:      OrderType
    product:         ProductType
    price:           float        = 0.0
    trigger_price:   float        = 0.0
    idempotency_key: str          = ""   # dedup on retry


@dataclass
class OrderResult:
    order_id:   str        = ""
    status:     str        = "PENDING"
    message:    str        = ""
    filled_qty: int        = 0
    avg_price:  float      = 0.0
    broker:     str        = ""
    raw:        dict       = field(default_factory=dict)


@dataclass
class Position:
    symbol:    str
    quantity:  int
    avg_price: float
    ltp:       float
    pnl:       float
    product:   ProductType
    direction: str = "BUY"

    @property
    def pnl_pct(self) -> float:
        if self.avg_price <= 0:
            return 0.0
        return round((self.ltp - self.avg_price) / self.avg_price * 100, 2)


@dataclass
class MarginInfo:
    available: float
    used:      float
    total:     float

    @property
    def utilization_pct(self) -> float:
        return round(self.used / self.total * 100, 1) if self.total > 0 else 0.0


class BrokerError(Exception):
    """Base for all broker failures."""


class OrderRejectedError(BrokerError):
    """Broker rejected the order — do not retry automatically."""


class IBrokerProvider(ABC):
    """Secondary port — broker-agnostic order management."""

    @property
    @abstractmethod
    def broker_name(self) -> str: ...

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResult: ...

    @abstractmethod
    async def modify_order(
        self, order_id: str, price: float, quantity: int
    ) -> OrderResult: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> OrderResult: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_margins(self) -> MarginInfo: ...

    @abstractmethod
    async def get_tradebook(self) -> list[dict]: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
