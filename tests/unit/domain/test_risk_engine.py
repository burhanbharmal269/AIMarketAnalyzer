"""Unit tests for RiskEngine — all business rules covered."""
import pytest
from app.application.services.risk_engine import RiskEngine, RiskConfig
from app.domain.risk.entities import Portfolio
from app.domain.signal.entities import Candidate


@pytest.fixture
def engine() -> RiskEngine:
    return RiskEngine(RiskConfig(
        capital=500_000,
        risk_pct=2.0,
        max_daily_loss_pct=3.0,
        max_weekly_loss_pct=8.0,
        max_monthly_loss_pct=15.0,
        max_open_positions=5,
        min_rr=1.5,
        max_lots_per_trade=10,
        loss_streak_pause_at=3,
        loss_streak_halt_at=5,
    ))


class TestPositionSizing:
    def test_atr_based_sizing(self, engine, sample_candidate, clean_portfolio):
        # ATR=0.5, lot_size=250, risk_budget=10_000 → lot_risk=125, lots=80 (capped at 10)
        decision = engine.evaluate(sample_candidate, clean_portfolio)
        assert decision.approved
        assert 1 <= decision.lots <= engine._cfg.max_lots_per_trade

    def test_zero_atr_falls_back_to_price_risk(self, engine, clean_portfolio):
        c = Candidate.from_raw({
            "instrument": "TEST30JUN26100CE",
            "underlying": "TEST",
            "direction": "BUY",
            "entry": 10.0,
            "stopLoss": 7.0,
            "targets": [15.0],
            "lotSize": 100,
            "atr": 0.0,
        })
        decision = engine.evaluate(c, clean_portfolio)
        # entry-to-SL = 3, lot_risk = 300, budget=10_000 → lots=33, capped at 10
        assert decision.lots <= 10

    def test_zero_risk_metrics_returns_zero_lots(self, engine, clean_portfolio):
        c = Candidate.from_raw({
            "instrument": "TEST30JUN26100CE",
            "underlying": "TEST",
            "direction": "BUY",
            "lotSize": 1,
            "atr": 0.0,
            "entry": 0.0,
            "stopLoss": 0.0,
        })
        decision = engine.evaluate(c, clean_portfolio)
        assert not decision.approved
        assert any("budget" in f.lower() or "cannot compute" in f.lower() for f in decision.failures)


class TestDrawdownGates:
    def test_daily_loss_gate(self, engine, sample_candidate, clean_portfolio):
        portfolio = Portfolio(
            daily_pnl_pct=-3.5,   # exceeds 3% limit
            daily_pnl_inr=-17_500,
            weekly_pnl_pct=-3.5,
            monthly_pnl_pct=-3.5,
        )
        decision = engine.evaluate(sample_candidate, portfolio)
        assert not decision.approved
        assert any("daily" in f.lower() for f in decision.failures)

    def test_weekly_loss_gate(self, engine, sample_candidate, clean_portfolio):
        portfolio = Portfolio(daily_pnl_pct=0.0, weekly_pnl_pct=-9.0)
        decision = engine.evaluate(sample_candidate, portfolio)
        assert not decision.approved
        assert any("weekly" in f.lower() for f in decision.failures)

    def test_healthy_portfolio_passes(self, engine, sample_candidate, clean_portfolio):
        decision = engine.evaluate(sample_candidate, clean_portfolio)
        assert decision.approved
        assert not decision.failures


class TestLossStreak:
    def test_halt_on_streak(self, engine, sample_candidate):
        portfolio = Portfolio(loss_streak=5)
        decision = engine.evaluate(sample_candidate, portfolio)
        assert not decision.approved
        assert any("consecutive" in f.lower() for f in decision.failures)

    def test_below_halt_threshold_passes(self, engine, sample_candidate, clean_portfolio):
        portfolio = Portfolio(loss_streak=2)
        decision = engine.evaluate(sample_candidate, portfolio)
        # streak < halt threshold; should not be blocked for streak alone
        streak_failures = [f for f in decision.failures if "consecutive" in f.lower()]
        assert not streak_failures


class TestRiskReward:
    def test_rr_below_minimum_fails(self, engine, clean_portfolio):
        c = Candidate.from_raw({
            "instrument": "TEST30JUN26100CE",
            "underlying": "TEST",
            "direction": "BUY",
            "entry": 10.0,
            "stopLoss": 7.0,    # risk = 3
            "targets": [12.0],  # reward = 2, R:R = 0.67
            "lotSize": 1,
            "atr": 3.0,
        })
        decision = engine.evaluate(c, clean_portfolio)
        rr_failures = [f for f in decision.failures if "r:r" in f.lower()]
        assert rr_failures

    def test_good_rr_passes(self, engine, sample_candidate, clean_portfolio):
        # sample_candidate: entry=30, SL=20, T1=45 → R:R = 15/10 = 1.5
        decision = engine.evaluate(sample_candidate, clean_portfolio)
        rr_failures = [f for f in decision.failures if "r:r" in f.lower()]
        assert not rr_failures


class TestMaxPositions:
    def test_max_positions_gate(self, engine, sample_candidate):
        portfolio = Portfolio(open_position_count=5)
        decision = engine.evaluate(sample_candidate, portfolio)
        assert not decision.approved
        assert any("concurrent" in f.lower() for f in decision.failures)


class TestDuplicateUnderlying:
    def test_duplicate_underlying_fails(self, engine, sample_candidate):
        portfolio = Portfolio(open_underlyings={"RELIANCE"})
        decision = engine.evaluate(sample_candidate, portfolio)
        assert not decision.approved
        assert any("reliance" in f.lower() for f in decision.failures)
