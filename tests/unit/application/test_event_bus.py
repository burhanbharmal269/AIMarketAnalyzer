"""Unit tests for AsyncEventBus."""
import asyncio
import pytest
from app.application.events.bus import AsyncEventBus
from app.domain.signal.events import SignalApproved, SignalRejected
from uuid import uuid4


class TestAsyncEventBus:
    @pytest.mark.asyncio
    async def test_handler_called_on_publish(self):
        bus    = AsyncEventBus()
        calls  = []

        async def handler(event: SignalApproved):
            calls.append(event)

        bus.subscribe(SignalApproved, handler)
        event = SignalApproved(instrument="TEST30JUN26100CE", direction="BUY", score=80.0)
        await bus.publish(event)
        assert len(calls) == 1
        assert calls[0].instrument == "TEST30JUN26100CE"

    @pytest.mark.asyncio
    async def test_no_handler_for_type_is_silent(self):
        bus = AsyncEventBus()
        event = SignalApproved(instrument="TEST", direction="BUY", score=70.0)
        await bus.publish(event)   # should not raise

    @pytest.mark.asyncio
    async def test_multiple_handlers_all_called(self):
        bus    = AsyncEventBus()
        calls  = []

        async def h1(e): calls.append("h1")
        async def h2(e): calls.append("h2")

        bus.subscribe(SignalApproved, h1)
        bus.subscribe(SignalApproved, h2)
        await bus.publish(SignalApproved(instrument="X", direction="BUY", score=75.0))
        assert set(calls) == {"h1", "h2"}

    @pytest.mark.asyncio
    async def test_failing_handler_does_not_prevent_other_handlers(self):
        bus   = AsyncEventBus()
        calls = []

        async def bad_handler(e):
            raise RuntimeError("handler explosion")

        async def good_handler(e):
            calls.append("good")

        bus.subscribe(SignalApproved, bad_handler)
        bus.subscribe(SignalApproved, good_handler)
        await bus.publish(SignalApproved(instrument="X", direction="BUY", score=75.0))
        assert calls == ["good"]   # good handler still ran

    @pytest.mark.asyncio
    async def test_unsubscribe_works(self):
        bus   = AsyncEventBus()
        calls = []

        async def handler(e):
            calls.append("called")

        bus.subscribe(SignalApproved, handler)
        bus.unsubscribe(SignalApproved, handler)
        await bus.publish(SignalApproved(instrument="X", direction="BUY", score=75.0))
        assert calls == []

    @pytest.mark.asyncio
    async def test_different_event_types_isolated(self):
        bus   = AsyncEventBus()
        approved_calls = []
        rejected_calls = []

        async def on_approved(e): approved_calls.append(e)
        async def on_rejected(e): rejected_calls.append(e)

        bus.subscribe(SignalApproved, on_approved)
        bus.subscribe(SignalRejected, on_rejected)

        await bus.publish(SignalApproved(instrument="X", direction="BUY", score=75.0))
        assert len(approved_calls) == 1
        assert len(rejected_calls) == 0

    @pytest.mark.asyncio
    async def test_publish_many(self):
        bus   = AsyncEventBus()
        calls = []

        async def handler(e):
            calls.append(e.instrument)

        bus.subscribe(SignalApproved, handler)
        events = [
            SignalApproved(instrument=f"INST{i}", direction="BUY", score=70.0)
            for i in range(3)
        ]
        await bus.publish_many(events)
        assert len(calls) == 3
