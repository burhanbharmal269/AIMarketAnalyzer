"""KiteBrokerAdapter — order management via Kite Connect.

All blocking calls run in thread-pool executors to avoid stalling the event loop.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class KiteBrokerAdapter:
    """Place, modify, cancel orders and query positions via Kite Connect."""

    @property
    def broker_name(self) -> str:
        return "kite"

    def _client(self):
        from app.data_sources.kite import kite_session, KITE_AVAILABLE
        if not KITE_AVAILABLE:
            raise RuntimeError("Kite not configured")
        return kite_session.get_client()

    # ── Orders ────────────────────────────────────────────────────────────────

    async def place_order(
        self,
        tradingsymbol: str,
        exchange: str,
        transaction_type: str,      # "BUY" | "SELL"
        quantity: int,
        order_type: str = "MARKET", # "MARKET" | "LIMIT" | "SL" | "SL-M"
        product: str   = "MIS",     # "MIS" | "NRML" | "CNC"
        price: float   = 0.0,
        trigger_price: float = 0.0,
        validity: str  = "DAY",
        variety: str   = "regular", # "regular" | "co" | "bo"
        tag: str       = "",
    ) -> str:
        """Place an order. Returns order_id on success."""
        loop = asyncio.get_event_loop()
        kite = self._client()
        from app.data_sources.kite import _throttle, _with_auth

        params: dict = dict(
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=transaction_type.upper(),
            quantity=quantity,
            order_type=order_type.upper(),
            product=product.upper(),
            validity=validity,
            variety=variety,
        )
        if price > 0:
            params["price"] = price
        if trigger_price > 0:
            params["trigger_price"] = trigger_price
        if tag:
            params["tag"] = tag[:20]

        def _place():
            _throttle()
            return _with_auth(kite.place_order, **params)

        try:
            order_id = await loop.run_in_executor(None, _place)
            logger.info("Order placed: %s %s %s x%d → order_id=%s",
                        transaction_type, tradingsymbol, order_type, quantity, order_id)
            return str(order_id)
        except Exception as exc:
            logger.error("Order placement failed for %s: %s", tradingsymbol, exc)
            raise

    async def modify_order(
        self,
        order_id: str,
        quantity: int | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
        order_type: str | None = None,
        validity: str | None = None,
        variety: str = "regular",
    ) -> str:
        loop = asyncio.get_event_loop()
        kite = self._client()
        from app.data_sources.kite import _throttle, _with_auth

        params: dict = {"variety": variety, "order_id": order_id}
        if quantity is not None:
            params["quantity"] = quantity
        if price is not None:
            params["price"] = price
        if trigger_price is not None:
            params["trigger_price"] = trigger_price
        if order_type is not None:
            params["order_type"] = order_type.upper()
        if validity is not None:
            params["validity"] = validity

        def _modify():
            _throttle()
            return _with_auth(kite.modify_order, **params)

        return str(await loop.run_in_executor(None, _modify))

    async def cancel_order(self, order_id: str, variety: str = "regular") -> str:
        loop = asyncio.get_event_loop()
        kite = self._client()
        from app.data_sources.kite import _throttle, _with_auth

        def _cancel():
            _throttle()
            return _with_auth(kite.cancel_order, variety=variety, order_id=order_id)

        return str(await loop.run_in_executor(None, _cancel))

    # ── Queries ───────────────────────────────────────────────────────────────

    async def get_order_status(self, order_id: str) -> dict:
        loop = asyncio.get_event_loop()
        kite = self._client()
        from app.data_sources.kite import _throttle, _with_auth

        def _fetch():
            _throttle()
            orders = _with_auth(kite.orders)
            for o in orders:
                if str(o.get("order_id")) == str(order_id):
                    return o
            return {}

        return await loop.run_in_executor(None, _fetch)

    async def get_positions(self) -> dict:
        loop = asyncio.get_event_loop()
        kite = self._client()
        from app.data_sources.kite import _throttle, _with_auth

        def _fetch():
            _throttle()
            return _with_auth(kite.positions)

        return await loop.run_in_executor(None, _fetch)

    async def get_margins(self) -> dict:
        loop = asyncio.get_event_loop()
        kite = self._client()
        from app.data_sources.kite import _throttle, _with_auth

        def _fetch():
            _throttle()
            return _with_auth(kite.margins)

        return await loop.run_in_executor(None, _fetch)

    async def get_tradebook(self) -> list[dict]:
        loop = asyncio.get_event_loop()
        kite = self._client()
        from app.data_sources.kite import _throttle, _with_auth

        def _fetch():
            _throttle()
            return _with_auth(kite.trades)

        return await loop.run_in_executor(None, _fetch)

    async def health_check(self) -> bool:
        from app.data_sources.kite import KITE_AVAILABLE, kite_session
        return KITE_AVAILABLE and kite_session.ensure_connected()
