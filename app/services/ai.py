import logging
from datetime import date

from app.config import settings

# ── explanation cache ─────────────────────────────────────────────────────────
# Keyed by (instrument, score_bucket, date). Cleared naturally each new trading day.
_EXPLANATION_CACHE: dict = {}

logger = logging.getLogger(__name__)

# ── client initialisation ─────────────────────────────────────────────────────
# Azure OpenAI takes priority when all three Azure fields are set.
# Falls back to standard OpenAI if only OPENAI_API_KEY is set.
# Falls back to rule-based text if neither is configured.

_client = None
_provider = "none"

try:
    from openai import AzureOpenAI, OpenAI

    _azure_ready = all([
        settings.azure_openai_api_key,
        settings.azure_openai_endpoint,
        settings.azure_openai_deployment,
    ])

    if _azure_ready:
        _client = AzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
        )
        _provider = "azure"
        logger.info("AI provider: Azure OpenAI (deployment=%s)", settings.azure_openai_deployment)

    elif settings.openai_api_key:
        _client = OpenAI(api_key=settings.openai_api_key)
        _provider = "openai"
        logger.info("AI provider: OpenAI (model=%s)", settings.openai_model)

except Exception as exc:
    logger.warning("Could not initialise AI client: %s", exc)


def _model_name() -> str:
    """Returns the model/deployment name to pass to the API."""
    if _provider == "azure":
        return settings.azure_openai_deployment   # Azure uses deployment name
    return settings.openai_model                  # Standard OpenAI uses model name


def openai_enabled() -> bool:
    return _client is not None


def ai_status() -> dict:
    if _provider == "azure":
        return {
            "enabled":  True,
            "provider": "Azure OpenAI",
            "model":    settings.azure_openai_deployment,
            "mode":     "Azure OpenAI",
        }
    if _provider == "openai":
        return {
            "enabled":  True,
            "provider": "OpenAI",
            "model":    settings.openai_model,
            "mode":     "OpenAI",
        }
    return {
        "enabled":  False,
        "provider": "none",
        "model":    None,
        "mode":     "Rule-based fallback",
    }


# ── shared caller ─────────────────────────────────────────────────────────────

def _call(system: str, user: str, max_tokens: int = 400) -> str | None:
    if not _client:
        return None
    try:
        resp = _client.chat.completions.create(
            model=_model_name(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("AI call failed [%s]: %s", _provider, exc)
        return None


# ── public functions ──────────────────────────────────────────────────────────

def generate_trade_explanation(candidate: dict, score: dict, market: dict) -> str:
    """Per-trade AI analysis with live news context. Falls back to rule-based text.

    AI is only invoked for high-confidence signals (score >= 80). Lower-scoring
    signals that still passed the 72 floor use the rule-based text — they are
    borderline setups and don't need expensive AI analysis.
    Results are cached per (instrument, score_bucket, date) to avoid duplicate
    calls when the same setup appears across multiple scans in one day.
    """
    from app.data_sources.news import get_headlines

    rule_text = (
        f"{candidate['instrument']} qualifies because trend, momentum, liquidity and "
        f"option-chain evidence align with the trade direction. "
        f"The setup uses a defined stop at {candidate['stopLoss']} and score "
        f"{score['total']}/100. The trade remains valid only while price action "
        "holds the entry structure and event risk does not change."
    )

    # Only use AI for top-tier signals — borderline signals (72–79) use rule text
    if not openai_enabled() or score["total"] < 80:
        return rule_text

    # Cache key: same instrument + score band + calendar date = same analysis
    score_bucket = (score["total"] // 5) * 5   # bucket into 5-point bands: 80,85,90…
    cache_key = (candidate["instrument"], score_bucket, date.today().isoformat())
    if cache_key in _EXPLANATION_CACHE:
        logger.debug("AI explanation cache hit: %s", cache_key)
        return _EXPLANATION_CACHE[cache_key]

    underlying = candidate.get("underlying", candidate["instrument"])
    headlines  = get_headlines(underlying)
    news_block = (
        "\nLatest news:\n" + "\n".join(f"  - {h}" for h in headlines)
        if headlines else ""
    )

    system = (
        "You are a professional Indian options trader assistant. "
        "Concise, factual analysis only. No financial advice."
    )
    # Rounded values reduce token count without losing signal
    user = (
        f"F&O setup: {candidate['instrument']} | {candidate['direction']} | Score {score['total']}/100\n"
        f"Entry {candidate['entry']}  SL {candidate['stopLoss']}  Targets {candidate['targets']}\n"
        f"EMA20={round(candidate['ema20'])}  EMA50={round(candidate['ema50'])}  EMA200={round(candidate['ema200'])}\n"
        f"Supertrend={'Bull' if candidate.get('supertrendBullish') else 'Bear'}  "
        f"PDH={'Y' if candidate.get('pdBreakout') else 'N'}  "
        f"RSI={round(candidate['rsi'],1)}  ADX={round(candidate['adx'],1)}\n"
        f"OI%={candidate['oiChangePct']}  PCR={candidate['pcr']}  "
        f"IV={candidate.get('atmIV','?')}%  RelVol={candidate['relativeVolume']}\n"
        f"VIX={market['indiaVix']}  Market: {market['regime']}"
        f"{news_block}\n\n"
        "3 sections (2-3 lines each):\n"
        "WHY THIS TRADE:\nWHY IT COULD FAIL:\nKEY RISKS:"
    )
    result = _call(system, user, max_tokens=300)
    text = result if result else rule_text
    _EXPLANATION_CACHE[cache_key] = text
    return text


def generate_market_summary(scan: dict, market: dict) -> dict:
    """Daily market summary. Uses AI when available, else rule-based."""
    approved_count = len(scan["approved"])
    scanner_line = (
        "No approved trades — hard risk gates or score threshold not met."
        if approved_count == 0
        else f"{approved_count} high-conviction setup(s) passed all validation gates."
    )
    rule_summary = (
        f"Market condition: {market['regime']}. {market['bias']}. "
        f"{scanner_line} "
        f"India VIX at {market['indiaVix']}. "
        + " ".join(market.get("news", []))
    )

    if not openai_enabled():
        return {"provider": "rules", "summary": rule_summary}

    from app.data_sources.news import get_market_headlines

    market_headlines = get_market_headlines(max_results=4)
    news_block = (
        "\nLive market headlines:\n" + "\n".join(f"  - {h}" for h in market_headlines)
        if market_headlines else ""
    )

    system = (
        "You are a daily market analyst for Indian F&O traders. "
        "Write a concise, actionable morning briefing. 140-180 words. "
        "Focus on market structure, key risk factors, and what traders should watch. "
        "Incorporate the provided live headlines into your briefing."
    )
    user = (
        f"Market regime: {market['regime']}\n"
        f"Bias: {market['bias']}\n"
        f"India VIX: {market['indiaVix']}\n"
        f"Breadth A/D ratio: {market.get('breadth', 'N/A')}\n"
        f"Approved signals today: {approved_count}\n"
        f"Scheduled events: {[e['name'] for e in market.get('eventCalendar', [])]}"
        f"{news_block}\n\n"
        "Write a concise morning briefing for Indian F&O traders. "
        "Reference the headlines where relevant. "
        "End with one specific action: trade / reduce size / stay out."
    )
    result = _call(system, user, max_tokens=240)
    return {"provider": _provider, "summary": result if result else rule_summary}
