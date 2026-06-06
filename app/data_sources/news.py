import logging
import time

import requests

from app.config import settings

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_TTL = 900  # 15-minute cache — news doesn't change that fast

# Search-friendly names for NSE symbols (improves headline relevance)
_SYMBOL_QUERY = {
    # Indices
    "NIFTY":       "Nifty 50 index India market",
    "BANKNIFTY":   "Bank Nifty India banking sector",
    "FINNIFTY":    "Nifty Financial Services Finnifty India",
    # IT / Tech
    "INFY":        "Infosys stock earnings results",
    "TCS":         "TCS Tata Consultancy Services stock",
    "WIPRO":       "Wipro stock earnings India IT",
    "HCLTECH":     "HCL Technologies stock India IT",
    "TECHM":       "Tech Mahindra stock India IT",
    # Banking / Finance
    "HDFCBANK":    "HDFC Bank stock India",
    "ICICIBANK":   "ICICI Bank stock India",
    "AXISBANK":    "Axis Bank stock India",
    "SBIN":        "SBI State Bank of India stock",
    "KOTAKBANK":   "Kotak Mahindra Bank stock",
    "INDUSINDBK":  "IndusInd Bank stock India",
    "BAJFINANCE":  "Bajaj Finance NBFC stock India",
    "BAJAJFINSV":  "Bajaj Finserv stock India",
    "HDFCLIFE":    "HDFC Life Insurance stock India",
    # Energy / PSU
    "RELIANCE":    "Reliance Industries RIL stock",
    "NTPC":        "NTPC power stock India PSU",
    "POWERGRID":   "Power Grid Corporation stock India",
    "ONGC":        "ONGC oil gas stock India",
    # Auto
    "TATAMOTORS":  "Tata Motors EV stock India",
    "MARUTI":      "Maruti Suzuki automobile stock India",
    "M&M":         "Mahindra Mahindra auto stock India",
    "EICHERMOT":   "Eicher Motors Royal Enfield stock India",
    # Pharma
    "SUNPHARMA":   "Sun Pharma pharmaceutical stock India",
    "DRREDDY":     "Dr Reddy Laboratories stock India",
    "CIPLA":       "Cipla pharma stock India",
    "DIVISLAB":    "Divi's Laboratories stock India",
    # FMCG / Consumer
    "HINDUNILVR":  "Hindustan Unilever HUL FMCG stock",
    "ASIANPAINT":  "Asian Paints stock India",
    "TITAN":       "Titan Company watches jewellery stock India",
    "PIDILITIND":  "Pidilite Industries Fevicol stock India",
    # Metals
    "TATASTEEL":   "Tata Steel stock India metals",
    "JSWSTEEL":    "JSW Steel stock India",
    "HINDALCO":    "Hindalco aluminium copper stock India",
    # Infra / Conglomerate
    "LT":          "Larsen Toubro L&T infrastructure stock India",
    "ADANIENT":    "Adani Enterprises stock India",
    "ADANIPORTS":  "Adani Ports SEZ stock India",
    "ULTRACEMCO":  "UltraTech Cement stock India",
    "GRASIM":      "Grasim Industries Aditya Birla stock India",
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
