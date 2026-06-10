"""Repository interfaces — secondary ports for persistence.

Domain logic depends on these interfaces, never on SQLAlchemy/SQLite directly.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from app.domain.trade.entities import Trade
from app.domain.signal.entities import Signal


class ITradeRepository(ABC):

    @abstractmethod
    async def save(self, trade: Trade) -> int:
        """Persist trade, return assigned id."""

    @abstractmethod
    async def get_by_id(self, trade_id: int) -> Trade | None: ...

    @abstractmethod
    async def get_open_trades(self) -> list[Trade]: ...

    @abstractmethod
    async def get_recent(self, limit: int = 50) -> list[Trade]: ...

    @abstractmethod
    async def update_outcome(
        self,
        trade_id:   int,
        outcome:    str,
        exit_price: float,
        pnl_r:      float,
        pnl_inr:    float = 0.0,
        exit_reason: str = "",
    ) -> None: ...

    @abstractmethod
    async def compute_risk_state(self, capital: float, risk_pct: float) -> dict: ...


class ISignalRepository(ABC):

    @abstractmethod
    async def save(self, signal: dict, scan_id: int | None = None) -> int: ...

    @abstractmethod
    async def get_recent(self, limit: int = 100) -> list[dict]: ...

    @abstractmethod
    async def get_analytics(self) -> dict: ...

    @abstractmethod
    async def link_to_trade(self, signal_id: int, trade_id: int) -> None: ...


class IScanRepository(ABC):

    @abstractmethod
    async def save(self, result: dict) -> int:
        """Persist scan result, return scan id."""

    @abstractmethod
    async def get_recent(self, limit: int = 10) -> list[dict]: ...

    @abstractmethod
    async def prune(self, keep_days: int = 30) -> int:
        """Delete old scan records, return count deleted."""
