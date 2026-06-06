import logging
import time

import requests

from app.config import settings

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_TTL = 900  # 15-minute cache — news doesn't change that fast

# Search-friendly names for NSE symbols (improves headline relevance)
_SYMBOL_QUERY = {
    "NIFTY":       "Nifty 50 index India market",
    "BANKNIFTY":   "Bank Nifty India banking",
    "FINNIFTY":    "Finnifty NSE financial services India",
    "RELIANCE":    "Reliance Industries stock",
    "HDFCBANK":    "HDFC Bank stock India",
    "ICICIBANK":   "ICICI Bank stock India",
    "INFY":        "Infosys stock earnings",
    "TCS":         "TCS Tata Consultancy stock",
    "AXISBANK":    "Axis Bank stock India",
    "SBIN":        "SBI State Bank India stock",
    "KOTAKBANK":   "Kotak Mahindra Bank stock",
    "LT":          "Larsen Toubro L&T stock India",
    "WIPRO":       "Wipro stock earnings",
    "BAJFINANCE":  "Bajaj Finance stock India",
    "TATAMOTORS":  "Tata Motors stock India",
    "ADANIENT":    "Adani Enterprises stock",
    "ADANIPORTS":  "Adani Ports stock India",
    "MARUTI":      "Maruti Suzuki stock India",
    "SUNPHARMA":   "Sun Pharma stock India",
    "HINDUNILVR":  "Hindustan Unilever HUL stock",
}

_NEWSAPI_URL = "https://newsapi.org/v2/everything"


def get_headlines(symbol: str, max_results: int = 3) -> list[str]:
    """Return top news headlines for an NSE symbol.

    Cached for 15 minutes. Returns empty list if NewsAPI key is not configured
    or the request fails — callers must handle the empty case gracefully.
    """
    if not settings.news_api_key:
        return []

    cached = _CACHE.get(symbol)
    if cached and time.time() - cached["ts"] < _TTL:
        return cached["data"]

    query = _SYMBOL_QUERY.get(symbol, f"{symbol} NSE India stock")
    try:
        resp = requests.get(
            _NEWSAPI_URL,
            params={
                "q":        query,
                "apiKey":   settings.news_api_key,
                "language": "en",
                "sortBy":   "publishedAt",
                "pageSize": max_results,
            },
            timeout=10,
        )
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
        headlines = [a["title"] for a in articles if a.get("title")][:max_results]
        _CACHE[symbol] = {"data": headlines, "ts": time.time()}
        logger.debug("NewsAPI [%s]: %d headlines fetched", symbol, len(headlines))
        return headlines
    except Exception as exc:
        logger.debug("NewsAPI fetch failed [%s]: %s", symbol, exc)
        return []


def get_market_headlines(max_results: int = 4) -> list[str]:
    """Fetch broad Indian market headlines for morning briefings."""
    return get_headlines("NIFTY", max_results=max_results)


def news_enabled() -> bool:
    return bool(settings.news_api_key)
