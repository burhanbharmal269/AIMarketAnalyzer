"""KiteBrokerAdapter — Zerodha Kite Connect (future integration).

Stub implementation — raises NotImplementedError until Kite SDK is integrated.
Exists so the IBrokerProvider interface is proven extensible.
"""
from __future__ import annotations
from app.application.ports.broker import (
    IBrokerProvider, OrderRequest, OrderResult, Position, MarginInfo, BrokerError,
)


class KiteBrokerAdapter(IBrokerProvider):
    """Zerodha Kite Connect broker adapter — STUB, not yet implemented."""

    @property
    def broker_name(self) -> str:
        return "kite"

    async def place_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError("Kite adapter not yet implemented")

    async def modify_order(self, order_id: str, price: float, quantity: int) -> OrderResult:
        raise NotImplementedError("Kite adapter not yet implemented")

    async def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("Kite adapter not yet implemented")

    async def get_order_status(self, order_id: str) -> OrderResult:
        raise NotImplementedError("Kite adapter not yet implemented")

    async def get_positions(self) -> list[Position]:
        raise NotImplementedError("Kite adapter not yet implemented")

    async def get_margins(self) -> MarginInfo:
        raise NotImplementedError("Kite adapter not yet implemented")

    async def get_tradebook(self) -> list[dict]:
        raise NotImplementedError("Kite adapter not yet implemented")

    async def health_check(self) -> bool:
        return False
