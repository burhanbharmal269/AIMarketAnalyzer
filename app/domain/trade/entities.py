"""Trade domain entities."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from app.domain.trade.value_objects import TradeStatus, ExitReason, OrderStatus


@dataclass
class Trade:
    """A trade entry in the journal — paper or live."""
    id:               int | None  = None
    signal_id:        int | None  = None
    instrument:       str         = ""
    underlying:       str         = ""
    direction:        str         = "BUY"
    entry:            float       = 0.0
    stop_loss:        float       = 0.0
    target_1:         float       = 0.0
    target_2:         float       = 0.0
    target_3:         float       = 0.0
    lots:             int         = 1
    quantity:         int         = 0
    confidence_score: float       = 0.0
    status:           TradeStatus = TradeStatus.PAPER
    notes:            str         = ""
    exit_price:       float | None = None
    pnl_r:            float | None = None  # R-multiple
    pnl_inr:          float | None = None  # ₹ P&L
    exit_at:          datetime | None = None
    exit_reason:      ExitReason | None = None
    created_at:       datetime    = field(default_factory=datetime.utcnow)

    def is_open(self) -> bool:
        return self.status in (TradeStatus.PAPER, TradeStatus.OPEN)

    def risk_per_lot(self, lot_size: int) -> float:
        return abs(self.entry - self.stop_loss) * lot_size

    def rr_at_target(self, target: float) -> float:
        risk = abs(self.entry - self.stop_loss)
        if risk <= 0:
            return 0.0
        reward = abs(target - self.entry)
        return round(reward / risk, 2)


@dataclass
class Order:
    """Broker execution order — broker-agnostic."""
    order_id:     str         = ""
    instrument:   str         = ""
    direction:    str         = "BUY"
    quantity:     int         = 0
    order_type:   str         = "MARKET"
    price:        float       = 0.0
    trigger_price: float      = 0.0
    product:      str         = "INTRADAY"
    status:       OrderStatus = OrderStatus.PENDING
    filled_qty:   int         = 0
    avg_price:    float       = 0.0
    broker:       str         = ""
    message:      str         = ""
    created_at:   datetime    = field(default_factory=datetime.utcnow)
    idempotency_key: str      = ""
