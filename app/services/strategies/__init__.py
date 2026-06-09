"""Strategies sub-package — one class per signal type."""
from app.services.strategies.options import OptionsTradingStrategy
from app.services.strategies.equity  import EquitySwingStrategy, EquityLongTermStrategy

__all__ = ["OptionsTradingStrategy", "EquitySwingStrategy", "EquityLongTermStrategy"]
