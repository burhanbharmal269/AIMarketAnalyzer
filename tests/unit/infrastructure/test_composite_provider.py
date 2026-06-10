"""Unit tests for CompositeMarketDataProvider and CircuitBreakerProvider."""
import asyncio
import pytest
from unittest.mock import AsyncMock

from app.application.ports.market_data import IMarketDataProvider
from app.core.exceptions import DataUnavailableError, CircuitOpenError
from app.domain.market.value_objects import Symbol


@pytest.fixture
def primary_ok(fake_market_data):
    return fake_market_data


@pytest.fixture
def primary_failing():
    from tests.conftest import FakeMarketDataProvider
    return FakeMarketDataProvider(fail=True)


@pytest.fixture
def secondary_ok(fake_market_data):
    from tests.conftest import FakeMarketDataProvider
    return FakeMarketDataProvider(fail=False)


class TestCompositeProvider:
    @pytest.mark.asyncio
    async def test_returns_primary_when_available(self, primary_ok, secondary_ok):
        from app.infrastructure.market_data.composite import CompositeMarketDataProvider
        composite = CompositeMarketDataProvider([primary_ok, secondary_ok])
        quote = await composite.get_quote(Symbol("RELIANCE"))
        assert quote.ltp == 100.0
        assert primary_ok.call_counts.get("get_quote", 0) == 1
        assert secondary_ok.call_counts.get("get_quote", 0) == 0

    @pytest.mark.asyncio
    async def test_falls_back_to_secondary_on_failure(self, primary_failing, secondary_ok):
        from app.infrastructure.market_data.composite import CompositeMarketDataProvider
        composite = CompositeMarketDataProvider([primary_failing, secondary_ok])
        quote = await composite.get_quote(Symbol("RELIANCE"))
        assert quote.ltp == 100.0
        assert secondary_ok.call_counts.get("get_quote", 0) == 1

    @pytest.mark.asyncio
    async def test_raises_when_all_providers_fail(self):
        from tests.conftest import FakeMarketDataProvider
        from app.infrastructure.market_data.composite import CompositeMarketDataProvider
        p1 = FakeMarketDataProvider(fail=True)
        p2 = FakeMarketDataProvider(fail=True)
        composite = CompositeMarketDataProvider([p1, p2])
        with pytest.raises(DataUnavailableError):
            await composite.get_quote(Symbol("RELIANCE"))


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_opens_after_failures(self):
        from app.infrastructure.market_data.circuit_breaker import CircuitBreakerProvider
        from tests.conftest import FakeMarketDataProvider

        inner = FakeMarketDataProvider(fail=True)
        cb    = CircuitBreakerProvider(inner, failure_threshold=3, cooldown_secs=60)

        # First 3 calls increment failures
        for _ in range(3):
            try:
                await cb.get_quote(Symbol("TEST"))
            except (DataUnavailableError, CircuitOpenError):
                pass

        # 4th call should raise CircuitOpenError before the inner coroutine runs
        # Use a fresh symbol to avoid the "coroutine never awaited" RuntimeWarning —
        # circuit_breaker raises CircuitOpenError in _check_state() before await.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with pytest.raises(CircuitOpenError):
                await cb.get_quote(Symbol("TEST2"))

    @pytest.mark.asyncio
    async def test_circuit_resets_after_success(self):
        from app.infrastructure.market_data.circuit_breaker import CircuitBreakerProvider
        from tests.conftest import FakeMarketDataProvider

        inner = FakeMarketDataProvider(fail=False)
        cb    = CircuitBreakerProvider(inner, failure_threshold=3, cooldown_secs=0)

        # Manually force circuit open
        cb._state    = __import__("app.infrastructure.market_data.circuit_breaker", fromlist=["CircuitState"]).CircuitState.HALF_OPEN
        cb._failures = 3

        # A successful call should close the circuit
        quote = await cb.get_quote(Symbol("TEST"))
        assert quote is not None
