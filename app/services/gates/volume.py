"""Volume liquidity gate — rejects thinly traded option contracts."""
from app.services.gates.base import BaseGate

# Index options (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY) trade 10-100× more
# contracts per day than stock options. A single threshold rejects almost all
# stock option setups. Use a lower floor for stocks; rely on SpreadGate to
# filter genuinely illiquid contracts regardless of underlying type.
_INDEX_SYMBOLS  = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
_STOCK_MIN_VOL  = 3_000   # stock options: 3K contracts/day = tradeable
_INDEX_MIN_VOL  = 20_000  # index options: 20K contracts/day = tradeable


class VolumeLiquidityGate(BaseGate):
    def check(self, candidate, market, risk_state, settings) -> str | None:
        vol        = candidate["optionVolume"]
        underlying = candidate.get("underlying", "")
        is_index   = underlying in _INDEX_SYMBOLS

        # Angel One resets totalTradedVolume at open — new weekly contracts on
        # post-expiry mornings have near-zero volume until ~10:30. If OI > 0
        # the contract is live; pass it and let SpreadGate handle liquidity.
        if vol == 0:
            if candidate.get("oiChangePct", 0) > 0:
                return None
            return "Option volume is zero and open interest shows no activity."

        # Index options use the user-configured threshold (default 25K, UI-adjustable).
        # Stock options use a lower fixed floor — their daily volumes are structurally
        # smaller and the spread gate already filters the truly illiquid ones.
        min_vol = settings["minVolume"] if is_index else _STOCK_MIN_VOL
        if vol < min_vol:
            return (
                f"Option volume {vol:,} below minimum {min_vol:,} "
                f"({'index' if is_index else 'stock'} threshold)."
            )
        return None
