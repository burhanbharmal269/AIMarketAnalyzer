"""FastAPI dependency injection — composition root.

All application-layer objects are assembled here and provided to route handlers
via FastAPI's Depends() system. This is the only place that knows about concrete
implementations — routes and handlers only see interfaces.

Usage in a route:
    @router.post("/scan")
    async def scan(
        orchestrator: ScanOrchestrator = Depends(get_scan_orchestrator),
        settings:     ScanSettings     = Body(...),
    ): ...
"""
from __future__ import annotations
import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.application.ports.cache import ICacheProvider
from app.application.ports.market_data import IMarketDataProvider
from app.application.ports.broker import IBrokerProvider
from app.application.ports.ai import IAIProvider
from app.application.ports.news import INewsProvider
from app.application.ports.notification import INotificationProvider
from app.application.services.risk_engine import RiskEngine
from app.application.services.scan_orchestrator import ScanOrchestrator
from app.application.agents.orchestrator import AIOrchestrator
from app.application.events.bus import AsyncEventBus

logger = logging.getLogger(__name__)

# ── Singletons ────────────────────────────────────────────────────────────────
# These are created once at startup and held in module-level state.
# FastAPI Depends() is used only for request-scoped dependencies.

_market_data:   IMarketDataProvider | None = None
_broker:        IBrokerProvider | None     = None
_ai_provider:   IAIProvider | None         = None
_cache:         ICacheProvider | None      = None
_news:          INewsProvider | None       = None
_notifier:      INotificationProvider | None = None
_risk_engine:   RiskEngine | None          = None
_ai_orchestrator: AIOrchestrator | None   = None
_event_bus:     AsyncEventBus | None       = None
_scan_orchestrator: ScanOrchestrator | None = None


def init_dependencies(settings: dict) -> None:
    """Wire all concrete implementations. Called once in app.main:lifespan."""
    global _market_data, _broker, _ai_provider, _cache, _news
    global _notifier, _risk_engine, _ai_orchestrator, _event_bus, _scan_orchestrator

    # ── Cache ──────────────────────────────────────────────────────────────────
    redis_url = settings.get("redis_url", "")
    if redis_url:
        try:
            from app.infrastructure.cache.redis import RedisCache
            _cache = RedisCache(redis_url)
            logger.info("Cache: Redis at %s", redis_url.split("@")[-1] if "@" in redis_url else redis_url)
        except Exception as exc:
            logger.warning("Redis unavailable (%s) — using NullCache", exc)
            from app.infrastructure.cache.redis import NullCache
            _cache = NullCache()
    else:
        from app.infrastructure.cache.redis import NullCache
        _cache = NullCache()

    # ── Market data provider ───────────────────────────────────────────────────
    providers = []

    kite_cfg = settings.get("kite", {})
    if kite_cfg.get("api_key"):
        try:
            from app.infrastructure.market_data.kite_provider import KiteMarketDataAdapter
            from app.infrastructure.market_data.circuit_breaker import CircuitBreakerProvider
            kite_adapter = KiteMarketDataAdapter()
            providers.append(CircuitBreakerProvider(kite_adapter, failure_threshold=5, cooldown_secs=120))
            logger.info("Market data: Kite Connect registered")
        except Exception as exc:
            logger.warning("Kite market data init failed: %s", exc)

    # NSE always available as fallback (breadth, earnings calendar, holidays)
    try:
        from app.infrastructure.market_data.nse import NSEMarketDataAdapter
        from app.infrastructure.market_data.circuit_breaker import CircuitBreakerProvider
        nse = NSEMarketDataAdapter()
        providers.append(CircuitBreakerProvider(nse, failure_threshold=10, cooldown_secs=60))
        logger.info("Market data: NSE scraper registered")
    except Exception as exc:
        logger.warning("NSE adapter init failed: %s", exc)

    if providers:
        from app.infrastructure.market_data.composite import CompositeMarketDataProvider
        _market_data = CompositeMarketDataProvider(providers)
    else:
        logger.error("No market data providers available!")

    # ── Broker ─────────────────────────────────────────────────────────────────
    if kite_cfg.get("api_key"):
        try:
            from app.infrastructure.brokers.kite import KiteBrokerAdapter
            _broker = KiteBrokerAdapter()
            logger.info("Broker: Kite Connect registered")
        except Exception as exc:
            logger.warning("Kite broker init failed: %s", exc)

    # ── AI provider ─────────────────────────────────────────────────────────────
    ai_cfg = settings.get("azure_openai", {})
    if ai_cfg.get("api_key"):
        try:
            from app.infrastructure.ai.azure_openai import AzureOpenAIAdapter
            _ai_provider = AzureOpenAIAdapter(ai_cfg)
            logger.info("AI: Azure OpenAI registered")
        except Exception as exc:
            logger.warning("Azure OpenAI init failed: %s", exc)

    # ── News ────────────────────────────────────────────────────────────────────
    news_cfg = settings.get("newsapi", {})
    if news_cfg.get("api_key"):
        try:
            from app.infrastructure.news.newsapi import NewsAPIAdapter
            _news = NewsAPIAdapter()
            logger.info("News: NewsAPI registered")
        except Exception as exc:
            logger.warning("NewsAPI init failed: %s", exc)

    # ── Notifications ───────────────────────────────────────────────────────────
    try:
        from app.infrastructure.notifications.telegram import TelegramNotificationAdapter
        _notifier = TelegramNotificationAdapter()
    except Exception as exc:
        logger.warning("Telegram notifier init failed: %s", exc)

    # ── Risk engine ─────────────────────────────────────────────────────────────
    _risk_engine = RiskEngine.from_settings(settings)

    # ── AI orchestrator ─────────────────────────────────────────────────────────
    if _ai_provider:
        _ai_orchestrator = AIOrchestrator(_ai_provider, _news)

    # ── Event bus ───────────────────────────────────────────────────────────────
    _event_bus = AsyncEventBus()
    try:
        from app.application.events.handlers import register_all_handlers
        telegram_enabled = bool(settings.get("telegram_token"))
        register_all_handlers(_event_bus, telegram=telegram_enabled)
    except Exception as exc:
        logger.warning("Event handler registration failed: %s", exc)

    # ── Scan orchestrator ───────────────────────────────────────────────────────
    _scan_orchestrator = ScanOrchestrator(
        market_data=_market_data,
        cache=_cache,
        risk_engine=_risk_engine,
        notifier=_notifier,
        ai_orchestrator=_ai_orchestrator,
        event_bus=_event_bus,
    )

    logger.info("All dependencies initialised")


# ── FastAPI dependency providers ──────────────────────────────────────────────

def get_market_data() -> IMarketDataProvider:
    if _market_data is None:
        raise RuntimeError("Market data provider not initialised")
    return _market_data


def get_broker() -> IBrokerProvider | None:
    return _broker


def get_ai_provider() -> IAIProvider | None:
    return _ai_provider


def get_cache() -> ICacheProvider:
    if _cache is None:
        from app.infrastructure.cache.redis import NullCache
        return NullCache()
    return _cache


def get_news() -> INewsProvider | None:
    return _news


def get_risk_engine() -> RiskEngine:
    if _risk_engine is None:
        raise RuntimeError("Risk engine not initialised")
    return _risk_engine


def get_ai_orchestrator() -> AIOrchestrator | None:
    return _ai_orchestrator


def get_event_bus() -> AsyncEventBus:
    if _event_bus is None:
        return AsyncEventBus()   # empty bus — safe fallback
    return _event_bus


def get_scan_orchestrator() -> ScanOrchestrator:
    if _scan_orchestrator is None:
        raise RuntimeError("Scan orchestrator not initialised")
    return _scan_orchestrator


# ── Annotated type aliases for route handlers ─────────────────────────────────
MarketDataDep      = Annotated[IMarketDataProvider, Depends(get_market_data)]
CacheDep           = Annotated[ICacheProvider,       Depends(get_cache)]
RiskEngineDep      = Annotated[RiskEngine,           Depends(get_risk_engine)]
ScanOrchestratorDep = Annotated[ScanOrchestrator,   Depends(get_scan_orchestrator)]
EventBusDep        = Annotated[AsyncEventBus,        Depends(get_event_bus)]
