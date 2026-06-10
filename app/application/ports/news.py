"""INewsProvider — secondary port for news and event data."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Headline:
    title:     str
    source:    str
    published: datetime
    url:       str   = ""
    sentiment: float = 0.0   # -1.0 bearish → +1.0 bullish
    relevance: float = 0.0   # 0.0 → 1.0


@dataclass
class NewsSummary:
    symbol:          str
    headlines:       list[Headline] = field(default_factory=list)
    sentiment_score: float          = 0.0
    confidence:      float          = 0.0
    event_risk:      bool           = False
    events:          list[str]      = field(default_factory=list)

    @property
    def has_positive_bias(self) -> bool:
        return self.sentiment_score > 0.2

    @property
    def has_negative_bias(self) -> bool:
        return self.sentiment_score < -0.2


@dataclass
class EconomicEvent:
    title:       str
    date:        datetime
    impact:      str         = "low"   # "low" | "medium" | "high"
    country:     str         = "IN"
    description: str         = ""


class INewsProvider(ABC):

    @abstractmethod
    async def get_headlines(
        self, symbol: str, limit: int = 10
    ) -> list[Headline]: ...

    @abstractmethod
    async def get_batch_sentiment(
        self, symbols: list[str]
    ) -> dict[str, NewsSummary]: ...

    @abstractmethod
    async def get_economic_calendar(
        self, days_ahead: int = 7
    ) -> list[EconomicEvent]: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
