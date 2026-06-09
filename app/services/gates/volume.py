"""Volume liquidity gate — rejects thinly traded option contracts."""
from app.services.gates.base import BaseGate


class VolumeLiquidityGate(BaseGate):
    def check(self, candidate, market, risk_state, settings) -> str | None:
        if candidate["optionVolume"] < settings["minVolume"]:
            return "Option volume is below minimum liquidity threshold."
        return None
