import logging

from app.config import settings

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
    """Per-trade AI analysis with live news context. Falls back to rule-based text."""
    from app.data_sources.news import get_headlines

    rule_text = (
        f"{candidate['instrument']} qualifies because trend, momentum, liquidity and "
        f"option-chain evidence align with the trade direction. "
        f"The setup uses a defined stop at {candidate['stopLoss']} and score "
        f"{score['total']}/100. The trade remains valid only while price action "
        "holds the entry structure and event risk does not change."
    )

    if not openai_enabled():
        return rule_text

    # Fetch live headlines for this instrument
    underlying = candidate.get("underlying", candidate["instrument"])
    headlines = get_headlines(underlying)
    news_block = (
        "\nLatest news:\n" + "\n".join(f"  - {h}" for h in headlines)
        if headlines else "\nNo recent headlines found."
    )

    system = (
        "You are a professional Indian options trader assistant. "
        "Provide concise, factual trade analysis. Be direct. Maximum 280 words. "
        "No financial advice. Incorporate the provided news headlines into your analysis."
    )
    user = (
        f"Analyse this F&O trade setup:\n"
        f"Instrument: {candidate['instrument']}  Underlying: {underlying}\n"
        f"Direction: {candidate['direction']}\n"
        f"Entry: {candidate['entry']}  SL: {candidate['stopLoss']}  "
        f"Targets: {candidate['targets']}\n"
        f"EMA20={candidate['ema20']}  EMA50={candidate['ema50']}  EMA200={candidate['ema200']}\n"
        f"Supertrend={'Bullish' if candidate.get('supertrendBullish') else 'Bearish'}  "
        f"PDH Breakout={'Yes' if candidate.get('pdBreakout') else 'No'}\n"
        f"RSI={candidate['rsi']}  ADX={candidate['adx']}  "
        f"MACD={candidate['macd']} vs Signal={candidate['macdSignal']}\n"
        f"OI Change={candidate['oiChangePct']}%  PCR={candidate['pcr']}  "
        f"ATM IV={candidate.get('atmIV', 'N/A')}%  Rel Vol={candidate['relativeVolume']}\n"
        f"India VIX={market['indiaVix']}  Market: {market['regime']}\n"
        f"Confidence Score: {score['total']}/100"
        f"{news_block}\n\n"
        "Respond in exactly 3 labelled sections:\n"
        "WHY THIS TRADE: (2-3 lines — reference news if relevant)\n"
        "WHY IT COULD FAIL: (2-3 lines)\n"
        "KEY RISKS: (2-3 bullet points)"
    )
    result = _call(system, user, max_tokens=380)
    return result if result else rule_text


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
