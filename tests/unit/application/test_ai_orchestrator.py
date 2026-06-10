"""Unit tests for AIOrchestrator — verifies agent wiring, aggregation, and graceful degradation."""
import pytest
import asyncio
from tests.conftest import FakeAIProvider


@pytest.fixture
def orchestrator(fake_ai):
    from app.application.agents.orchestrator import AIOrchestrator
    return AIOrchestrator(fake_ai, news=None, max_concurrency=2)


@pytest.fixture
def sample_raw_candidate():
    return {
        "instrument": "RELIANCE30JUN261310CE",
        "underlying": "RELIANCE",
        "direction": "BUY",
        "spotPrice": 1310.0,
        "entry": 30.0,
        "stopLoss": 20.0,
        "targets": [45.0, 60.0],
        "lotSize": 250,
        "lots": 2,
        "lotRisk": 2500.0,
        "ema20": 1300.0, "ema50": 1280.0, "ema200": 1250.0,
        "rsi": 62.0, "adx": 28.0, "atr": 0.5,
        "vwap": 1305.0, "macdHist": 0.7,
        "supertrendBull": True, "tf15Aligned": True,
        "vwapConfirmed": True, "orbBreakout": False,
        "atmIv": 22.0, "ivRank": 65.0,
        "optionVolume": 50_000, "oiChangePct": 15.0,
        "spreadPct": 0.8, "pcr": 0.9, "maxPain": 1300.0,
        "delta": 0.45, "theta": -0.12, "vega": 0.08,
        "dte": 20,
        "score": {"total": 82.0},
    }


class TestAIOrchestrator:
    @pytest.mark.asyncio
    async def test_enriches_candidate(self, orchestrator, sample_raw_candidate):
        results = await orchestrator.analyse_candidates(
            [sample_raw_candidate], settings={}, market_raw={"vix": 15}
        )
        assert len(results) == 1
        r = results[0]
        assert "aiScore" in r
        assert "aiRec" in r
        assert "explanation" in r
        assert isinstance(r["aiScore"], float)
        assert 0.0 <= r["aiScore"] <= 100.0

    @pytest.mark.asyncio
    async def test_handles_empty_candidates(self, orchestrator):
        results = await orchestrator.analyse_candidates([], settings={})
        assert results == []

    @pytest.mark.asyncio
    async def test_handles_multiple_candidates(self, orchestrator, sample_raw_candidate):
        c2 = dict(sample_raw_candidate)
        c2["instrument"] = "INFY30JUN261900CE"
        c2["underlying"] = "INFY"
        results = await orchestrator.analyse_candidates(
            [sample_raw_candidate, c2], settings={}, market_raw={"vix": 14}
        )
        assert len(results) == 2
        assert all("aiScore" in r for r in results)

    @pytest.mark.asyncio
    async def test_agent_failure_does_not_drop_candidate(self, sample_raw_candidate):
        """If AI provider raises, orchestrator should still return the candidate (degraded)."""
        from app.application.agents.orchestrator import AIOrchestrator
        from tests.conftest import FakeAIProvider

        class FailingAI(FakeAIProvider):
            async def complete(self, *args, **kwargs):
                raise RuntimeError("AI offline")

        orch = AIOrchestrator(FailingAI(), news=None)
        results = await orch.analyse_candidates(
            [sample_raw_candidate], settings={}, market_raw={}
        )
        # Should return something, not raise
        assert len(results) == 1
