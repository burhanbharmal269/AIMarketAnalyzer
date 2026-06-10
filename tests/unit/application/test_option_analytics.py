"""Unit tests for OptionAnalyticsService."""
import pytest
from datetime import date, datetime

from app.application.services.option_analytics import OptionAnalyticsService
from app.domain.market.entities import OptionChainSnapshot, StrikeData
from app.domain.market.value_objects import Symbol, Price, Volume, OI, IVPercent, OptionType, Expiry


@pytest.fixture
def svc() -> OptionAnalyticsService:
    return OptionAnalyticsService()


def _make_strike(strike: float, opt_type: OptionType, oi: float, iv: float, volume: float = 10_000) -> StrikeData:
    return StrikeData(
        strike=strike,
        option_type=opt_type,
        ltp=Price(max(0.1, abs(105.0 - strike) + 1.0)),
        oi=OI(oi),
        oi_change=OI(oi * 0.05),
        volume=Volume(int(volume)),
        iv=IVPercent(iv),
    )


@pytest.fixture
def sample_chain() -> OptionChainSnapshot:
    """Chain with spot at 105; CE OI concentrated above 107, PE OI below 103."""
    legs = []
    for k in range(95, 116):
        k_f = float(k)
        # CE: higher OI at strikes above spot
        ce_oi = 1_000_000 if k_f > 107 else 200_000
        # PE: higher OI at strikes below spot
        pe_oi = 900_000  if k_f < 103 else 150_000
        # IV smile
        ce_iv = 22.0 + max(0, k_f - 105) * 0.2
        pe_iv = 23.0 + max(0, 105 - k_f) * 0.2

        legs.append(_make_strike(k_f, OptionType.CALL, ce_oi, ce_iv, 10_000))
        legs.append(_make_strike(k_f, OptionType.PUT,  pe_oi, pe_iv,  9_000))

    return OptionChainSnapshot(
        symbol=Symbol("NIFTY"),
        expiry=Expiry(date(2026, 6, 26)),
        spot_price=Price(105.0),
        strikes=legs,
        source="fake",
        fetched_at=datetime.utcnow(),
    )


class TestIVSurface:
    def test_atm_iv_computed(self, svc, sample_chain):
        surface = svc.compute_iv_surface(sample_chain)
        assert surface.atm_iv > 0
        assert 10.0 <= surface.atm_iv <= 50.0

    def test_skew_finite(self, svc, sample_chain):
        surface = svc.compute_iv_surface(sample_chain)
        assert isinstance(surface.call_iv_skew, float)
        assert isinstance(surface.put_iv_skew, float)


class TestOIBuildUp:
    def test_pcr_computed(self, svc, sample_chain):
        oi = svc.compute_oi_build_up(sample_chain)
        assert oi.pcr_oi > 0

    def test_walls_identified(self, svc, sample_chain):
        oi = svc.compute_oi_build_up(sample_chain)
        assert oi.resistance_wall > 0
        assert oi.support_wall > 0

    def test_net_delta_bias(self, svc, sample_chain):
        oi = svc.compute_oi_build_up(sample_chain)
        assert oi.net_delta_bias in ("bullish", "bearish", "neutral")

    def test_max_pain_computed(self, svc, sample_chain):
        oi = svc.compute_oi_build_up(sample_chain)
        assert oi.max_pain > 0
        # Max pain should be within the strike range
        assert 95 <= oi.max_pain <= 115


class TestGammaProfile:
    def test_gamma_profile_computed(self, svc, sample_chain):
        gp = svc.compute_gamma_profile(sample_chain, spot=105.0)
        assert gp.max_gamma_strike > 0
        assert gp.gamma_flip > 0
        assert isinstance(gp.above_flip, bool)


class TestIVRank:
    def test_iv_rank_in_range(self, svc):
        history = [15.0, 18.0, 22.0, 25.0, 30.0, 20.0]
        rank = svc.iv_rank(22.0, history)
        assert 0.0 <= rank <= 100.0

    def test_insufficient_history_returns_50(self, svc):
        # len < 5 → 50.0
        rank = svc.iv_rank(20.0, [18.0, 22.0])
        assert rank == 50.0

    def test_max_iv_returns_100(self, svc):
        history = [10.0, 15.0, 20.0, 30.0, 25.0]    # 5 items
        rank = svc.iv_rank(30.0, history)
        assert rank == 100.0

    def test_min_iv_returns_0(self, svc):
        history = [10.0, 15.0, 20.0, 30.0, 25.0]
        rank = svc.iv_rank(10.0, history)
        assert rank == 0.0


class TestExpectedMove:
    def test_positive_expected_move(self, svc):
        em = svc.expected_move(spot=100.0, iv=20.0, dte=30)
        assert em > 0

    def test_zero_dte_returns_zero(self, svc):
        assert svc.expected_move(100.0, 20.0, 0) == 0.0

    def test_zero_iv_returns_zero(self, svc):
        assert svc.expected_move(100.0, 0.0, 30) == 0.0
