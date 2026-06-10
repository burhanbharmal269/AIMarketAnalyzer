"""ICacheProvider — secondary port for distributed cache (Redis)."""
from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import date
from app.domain.market.entities import OptionChainSnapshot


class ICacheProvider(ABC):

    @abstractmethod
    async def get_option_chain(
        self, symbol: str, expiry: date
    ) -> OptionChainSnapshot | None: ...

    @abstractmethod
    async def set_option_chain(
        self, data: OptionChainSnapshot, ttl: int = 300
    ) -> None: ...

    @abstractmethod
    async def get_scan_result(self) -> dict | None: ...

    @abstractmethod
    async def set_scan_result(self, result: dict, ttl: int = 900) -> None: ...

    @abstractmethod
    async def get_regime(self) -> dict | None: ...

    @abstractmethod
    async def set_regime(self, regime: dict, ttl: int = 600) -> None: ...

    @abstractmethod
    async def get_iv_rank(self, symbol: str) -> float | None: ...

    @abstractmethod
    async def set_iv_rank(self, symbol: str, rank: float, ttl: int = 3600) -> None: ...

    @abstractmethod
    async def get_vix(self) -> float | None: ...

    @abstractmethod
    async def set_vix(self, vix: float, ttl: int = 30) -> None: ...

    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, value: str, ttl: int = 60) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
