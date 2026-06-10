"""NewsAPIAdapter — wraps existing news.py behind INewsProvider."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime

from app.application.ports.news import INewsProvider, Headline, NewsSummary, EconomicEvent

logger = logging.getLogger(__name__)


class NewsAPIAdapter(INewsProvider):
    """Wraps existing news.py get_headlines() behind INewsProvider."""

    async def get_headlines(self, symbol: str, limit: int = 10) -> list[Headline]:
        from app.data_sources.news import get_headlines as _get_headlines
        loop = asyncio.get_running_loop()
        try:
            raw_headlines = await loop.run_in_executor(
                None, lambda: _get_headlines(symbol)
            )
        except Exception as exc:
            logger.warning("NewsAPI: get_headlines(%s) failed: %s", symbol, exc)
            return []

        if not raw_headlines:
            return []

        result: list[Headline] = []
        for h in (raw_headlines if isinstance(raw_headlines, list) else [])[:limit]:
            if isinstance(h, str):
                result.append(Headline(
                    title=h, source="news", published=datetime.utcnow(),
                ))
            elif isinstance(h, dict):
                result.append(Headline(
                    title=h.get("title", h.get("headline", "")),
                    source=h.get("source", {}).get("name", "news") if isinstance(h.get("source"), dict) else str(h.get("source", "news")),
                    published=datetime.utcnow(),
                    url=h.get("url", ""),
                ))
        return result

    async def get_batch_sentiment(
        self, symbols: list[str]
    ) -> dict[str, NewsSummary]:
        """Fetch headlines for all symbols and return per-symbol summaries."""
        tasks = {sym: self.get_headlines(sym) for sym in symbols}
        results: dict[str, NewsSummary] = {}
        for sym, coro in tasks.items():
            headlines = await coro
            results[sym] = NewsSummary(
                symbol=sym,
                headlines=headlines,
                sentiment_score=0.0,  # scored by AI agent
                confidence=0.5 if headlines else 0.0,
            )
        return results

    async def get_economic_calendar(self, days_ahead: int = 7) -> list[EconomicEvent]:
        """Economic calendar — uses NSE earnings calendar as proxy."""
        try:
            from app.data_sources.nse import nse_data
            loop = asyncio.get_running_loop()
            events = await loop.run_in_executor(
                None, lambda: nse_data.get_earnings_calendar(days_ahead)
            )
            return [
                EconomicEvent(
                    title=e.get("symbol", "") + " Earnings",
                    date=datetime.utcnow(),
                    impact="high",
                    country="IN",
                    description=str(e),
                )
                for e in (events or [])
            ]
        except Exception as exc:
            logger.debug("Economic calendar fetch failed: %s", exc)
            return []

    async def health_check(self) -> bool:
        try:
            await self.get_headlines("NIFTY", limit=1)
            return True
        except Exception:
            return False
