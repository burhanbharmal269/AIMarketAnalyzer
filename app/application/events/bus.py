"""AsyncEventBus — lightweight in-process pub/sub for domain events.

No external broker required. Handlers are async functions registered by type.
The bus is used to decouple the scan pipeline from side effects (Telegram,
journal writes, monitor triggers) without making the core flow dependent on them.

Usage:
    bus = AsyncEventBus()
    bus.subscribe(SignalApproved, my_async_handler)
    await bus.publish(SignalApproved(...))
"""
from __future__ import annotations
import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable, Type

from app.domain.signal.events import SignalApproved, SignalRejected
from app.domain.trade.events import TradeOpened, SLHit, TargetHit

logger = logging.getLogger(__name__)

DomainEvent = (
    SignalApproved | SignalRejected | TradeOpened | SLHit | TargetHit
)
Handler = Callable[[DomainEvent], Awaitable[None]]


class AsyncEventBus:
    """In-process event bus. Thread-safe for asyncio contexts."""

    def __init__(self) -> None:
        self._handlers: dict[type, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: Type[DomainEvent], handler: Handler) -> None:
        """Register an async handler for a specific event type."""
        self._handlers[event_type].append(handler)
        logger.debug("Subscribed %s → %s", event_type.__name__, handler.__qualname__)

    def unsubscribe(self, event_type: Type[DomainEvent], handler: Handler) -> None:
        handlers = self._handlers.get(event_type, [])
        try:
            handlers.remove(handler)
        except ValueError:
            pass

    async def publish(self, event: DomainEvent) -> None:
        """Publish event to all registered handlers. Failures are logged, not raised."""
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            return
        tasks = [self._safe_call(h, event) for h in handlers]
        await asyncio.gather(*tasks)

    async def publish_many(self, events: list[DomainEvent]) -> None:
        """Publish multiple events sequentially."""
        for event in events:
            await self.publish(event)

    @staticmethod
    async def _safe_call(handler: Handler, event: DomainEvent) -> None:
        try:
            await handler(event)
        except Exception as exc:
            logger.warning(
                "Event handler %s failed for %s: %s",
                handler.__qualname__, type(event).__name__, exc,
                exc_info=True,
            )
