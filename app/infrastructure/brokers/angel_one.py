"""AngelOneBrokerAdapter — wraps Angel One order management behind IBrokerProvider.

This is the ONLY file that may call SmartConnect order APIs directly.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime

from app.application.ports.broker import (
    IBrokerProvider, OrderRequest, OrderResult, Position,
    MarginInfo, ProductType, BrokerError, OrderRejectedError,
)

logger = logging.getLogger(__name__)


class AngelOneBrokerAdapter(IBrokerProvider):
    """Angel One SmartAPI order management adapter."""

    @property
    def broker_name(self) -> str:
        return "angel_one"

    async def place_order(self, order: OrderRequest) -> OrderResult:
        from app.data_sources.angel import angel_session, _throttle
        client = angel_session.get_client()
        if not client:
            raise BrokerError("Angel One: not connected")

        loop = asyncio.get_running_loop()

        def _place():
            _throttle()
            return client.placeOrder({
                "variety":          "NORMAL",
                "tradingsymbol":    order.symbol,
                "symboltoken":      order.token,
                "transactiontype":  order.side.value,
                "exchange":         order.exchange,
                "ordertype":        order.order_type.value,
                "producttype":      order.product.value,
                "duration":         "DAY",
                "price":            str(order.price),
                "triggerprice":     str(order.trigger_price),
                "quantity":         str(order.quantity),
            })

        try:
            resp = await loop.run_in_executor(None, _place)
        except Exception as exc:
            raise BrokerError(f"Angel One placeOrder failed: {exc}") from exc

        if not resp or resp.get("status") is False:
            msg = (resp or {}).get("message", "unknown error")
            raise OrderRejectedError(f"Angel One rejected order: {msg}")

        order_id = (resp.get("data") or {}).get("orderid", "")
        return OrderResult(
            order_id=order_id,
            status="PENDING",
            message=resp.get("message", ""),
            broker=self.broker_name,
            raw=resp,
        )

    async def modify_order(
        self, order_id: str, price: float, quantity: int
    ) -> OrderResult:
        from app.data_sources.angel import angel_session, _throttle
        client = angel_session.get_client()
        if not client:
            raise BrokerError("Angel One: not connected")
        loop = asyncio.get_running_loop()

        def _modify():
            _throttle()
            return client.modifyOrder({
                "variety":  "NORMAL",
                "orderid":  order_id,
                "price":    str(price),
                "quantity": str(quantity),
            })

        resp = await loop.run_in_executor(None, _modify)
        if not resp or resp.get("status") is False:
            raise BrokerError(f"Modify failed: {(resp or {}).get('message')}")
        return OrderResult(order_id=order_id, status="MODIFIED", broker=self.broker_name, raw=resp)

    async def cancel_order(self, order_id: str) -> bool:
        from app.data_sources.angel import angel_session, _throttle
        client = angel_session.get_client()
        if not client:
            return False
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(
                None, lambda: client.cancelOrder("NORMAL", order_id)
            )
            return bool(resp and resp.get("status") is not False)
        except Exception as exc:
            logger.warning("Angel One cancelOrder %s failed: %s", order_id, exc)
            return False

    async def get_order_status(self, order_id: str) -> OrderResult:
        from app.data_sources.angel import angel_session, _throttle
        client = angel_session.get_client()
        if not client:
            raise BrokerError("Angel One: not connected")
        loop = asyncio.get_running_loop()

        def _status():
            _throttle()
            return client.orderBook()

        resp = await loop.run_in_executor(None, _status)
        orders = (resp or {}).get("data") or []
        for o in orders:
            if str(o.get("orderid")) == order_id:
                return OrderResult(
                    order_id=order_id,
                    status=o.get("status", "UNKNOWN"),
                    filled_qty=int(o.get("filledshares", 0) or 0),
                    avg_price=float(o.get("averageprice", 0) or 0),
                    broker=self.broker_name,
                    raw=o,
                )
        raise BrokerError(f"Order {order_id} not found in orderbook")

    async def get_positions(self) -> list[Position]:
        from app.data_sources.angel import angel_session, _throttle
        client = angel_session.get_client()
        if not client:
            return []
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(None, lambda: (_throttle(), client.position())[1])
        positions = (resp or {}).get("data") or []
        result = []
        for p in positions:
            qty = int(p.get("netqty", 0) or 0)
            if qty == 0:
                continue
            result.append(Position(
                symbol=p.get("tradingsymbol", ""),
                quantity=abs(qty),
                avg_price=float(p.get("averageprice", 0) or 0),
                ltp=float(p.get("ltp", 0) or 0),
                pnl=float(p.get("unrealised", 0) or 0),
                product=ProductType.INTRADAY,
                direction="BUY" if qty > 0 else "SELL",
            ))
        return result

    async def get_margins(self) -> MarginInfo:
        from app.data_sources.angel import angel_session, _throttle
        client = angel_session.get_client()
        if not client:
            raise BrokerError("Angel One: not connected")
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: (_throttle(), client.rmsLimit())[1]
        )
        data = (resp or {}).get("data") or {}
        available = float(data.get("availablecash", 0) or 0)
        used = float(data.get("utilisedamount", 0) or 0)
        total = available + used
        return MarginInfo(available=available, used=used, total=total)

    async def get_tradebook(self) -> list[dict]:
        from app.data_sources.angel import angel_session, _throttle
        client = angel_session.get_client()
        if not client:
            return []
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(None, lambda: (_throttle(), client.tradeBook())[1])
        return (resp or {}).get("data") or []

    async def health_check(self) -> bool:
        from app.data_sources.angel import angel_session
        return angel_session.ensure_connected()
