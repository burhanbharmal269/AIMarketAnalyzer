import json
import logging
import re
import time
from datetime import date

from app.config import settings

# ── global market snapshot (S&P 500 + USD/INR) ───────────────────────────────
_GLOBAL_CACHE: dict = {}   # {"data": {...}, "ts": float}
_GLOBAL_TTL = 900          # 15 min — stale enough to not hammer yfinance

def _get_global_context() -> str:
    """Return a one-line string with S&P 500 overnight change and USD/INR for AI context."""
    global _GLOBAL_CACHE
    if _GLOBAL_CACHE and time.time() - _GLOBAL_CACHE["ts"] < _GLOBAL_TTL:
        return _GLOBAL_CACHE["data"]
    try:
        import yfinance as yf
        tickers = yf.download("^GSPC USDINR=X", period="5d", interval="1d",
                              progress=False, auto_adjust=True)
        closes = tickers["Close"].dropna(how="all")
        sp_series = closes["^GSPC"].dropna()
        usd_series = closes["USDINR=X"].dropna()
        parts = []
        if len(sp_series) >= 2:
            sp_chg = round((sp_series.iloc[-1] / sp_series.iloc[-2] - 1) * 100, 2)
            parts.append(f"S&P 500 prev session: {sp_chg:+.2f}%")
        if len(usd_series) >= 1:
            usd_inr = round(float(usd_series.iloc[-1]), 2)
            parts.append(f"USD/INR: {usd_inr}")
        line = " | ".join(parts)
    except Exception:
        line = ""
    _GLOBAL_CACHE = {"data": line, "ts": time.time()}
    return line

# ── caches — all keyed with today's date so they reset each trading day ───────
_EXPLANATION_CACHE: dict = {}   # (instrument, score_bucket, date) -> str
_SENTIMENT_CACHE:   dict = {}   # (symbol, date) -> int  (-3…+3)
_REGIME_CACHE:      dict = {}   # date -> dict

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

def get_batch_news_sentiment(symbol_headlines: dict[str, list[str]]) -> dict[str, int]:
    """Score news sentiment for multiple symbols in ONE API call.

    Returns {symbol: score} where score is -3 (very bearish) to +3 (very bullish).
    Cached per (symbol, date) — at most 1 call per symbol per trading day.
    Symbols with cached scores are excluded from the API call.
    Falls back to {} when AI is not configured — callers default missing symbols to 0.

    One batched call instead of 40 individual calls keeps cost negligible.
    """
    if not openai_enabled() or not symbol_headlines:
        return {}

    today = date.today().isoformat()
    result: dict[str, int] = {}
    uncached: dict[str, list[str]] = {}

    for sym, headlines in symbol_headlines.items():
        key = (sym, today)
        if key in _SENTIMENT_CACHE:
            result[sym] = _SENTIMENT_CACHE[key]
        elif headlines:
            uncached[sym] = headlines

    if not uncached:
        return result

    news_block = ""
    for sym, headlines in uncached.items():
        news_block += f"\n{sym}:\n" + "\n".join(f"  - {h}" for h in headlines[:3])

    system = (
        "You are a financial news sentiment scorer for Indian equity markets. "
        "Reply with valid JSON only — no prose, no markdown."
    )
    user = (
        "Rate the net news sentiment for each symbol from a short-term F&O trader's perspective.\n"
        "Scale: -3 very bearish, -2 bearish, -1 slightly bearish, "
        "0 neutral/no news, +1 slightly bullish, +2 bullish, +3 very bullish.\n"
        f"News:{news_block}\n\n"
        f"Reply as JSON object: {{\"SYMBOL\": score, ...}} for every symbol listed."
    )

    raw = _call(system, user, max_tokens=250)
    if raw:
        try:
            # Extract first JSON object from response (handles markdown code fences)
            match = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                for sym, val in parsed.items():
                    try:
                        score = max(-3, min(3, int(val)))
                    except (TypeError, ValueError):
                        score = 0
                    result[sym] = score
                    _SENTIMENT_CACHE[(sym, today)] = score
                logger.info("News sentiment scored: %d symbols", len(parsed))
        except Exception as exc:
            logger.warning("Sentiment parse failed: %s | raw=%s", exc, raw[:120])

    # Cache zeros for symbols where AI gave no response (avoid re-fetching)
    for sym in uncached:
        if (sym, today) not in _SENTIMENT_CACHE:
            _SENTIMENT_CACHE[(sym, today)] = 0
            result.setdefault(sym, 0)

    return result


def get_market_regime_ai(market: dict) -> dict:
    """AI-classified pre-scan market regime. Called once per scan, cached for the day.

    Returns a structured regime dict that feeds into:
      - sentiment_score()  bonus/penalty based on aiAction
      - hard_gate_failures() blocks the full scan when aiAction == 'avoid'

    Regime classification is more nuanced than rule-based VIX thresholds because
    it combines VIX level, breadth, event calendar, and live news context together.
    """
    _safe = {
        "aiRegime": None, "aiBias": None,
        "aiRisk":   None, "aiAction": None,
    }
    if not openai_enabled():
        return _safe

    today = date.today().isoformat()
    if today in _REGIME_CACHE:
        return _REGIME_CACHE[today]

    # Pull up to 3 market headlines for context
    try:
        from app.data_sources.news import get_market_headlines
        headlines = get_market_headlines(max_results=3)
    except Exception:
        headlines = []

    news_block = ("\nLive headlines:\n" + "\n".join(f"  - {h}" for h in headlines)
                  if headlines else "")

    events = [e["name"] for e in market.get("eventCalendar", [])
              if e.get("minutesAway", 9999) <= 240]   # events within 4 hours

    global_ctx = _get_global_context()

    system = (
        "You are a market regime classifier for Indian F&O intraday traders. "
        "Reply with valid JSON only — no prose, no markdown."
    )
    user = (
        f"India VIX: {market.get('indiaVix')}\n"
        f"Advance/Decline ratio: {market.get('breadth')}\n"
        f"Rule-based regime: {market.get('regime')}\n"
        f"Upcoming events (within 4h): {events}\n"
        + (f"Global: {global_ctx}\n" if global_ctx else "")
        + f"{news_block}\n\n"
        "Classify the current market environment for directional F&O option buying.\n"
        "Reply as JSON:\n"
        '{"regime": "trending_bull|trending_bear|range_bound|volatile",\n'
        ' "bias": "strong_long|long|neutral|short|strong_short",\n'
        ' "risk": "low|medium|high",\n'
        ' "action": "trade_full|trade_reduced|selective|avoid",\n'
        ' "reason": "one sentence max"}'
    )

    raw = _call(system, user, max_tokens=150)
    regime_data = _safe.copy()
    if raw:
        try:
            match = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                regime_data = {
                    "aiRegime": parsed.get("regime"),
                    "aiBias":   parsed.get("bias"),
                    "aiRisk":   parsed.get("risk"),
                    "aiAction": parsed.get("action"),
                    "aiReason": parsed.get("reason"),
                }
                logger.info(
                    "AI regime: %s | bias: %s | action: %s",
                    regime_data["aiRegime"], regime_data["aiBias"], regime_data["aiAction"],
                )
        except Exception as exc:
            logger.warning("Regime parse failed: %s | raw=%s", exc, raw[:120])

    _REGIME_CACHE[today] = regime_data
    return regime_data


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

    # AI explanation for all approved signals (score >= 75).
    # Borderline signals (70-74) often need the most context — rule text alone is weak.
    if not openai_enabled() or score["total"] < 75:
        return rule_text

    score_bucket = (score["total"] // 5) * 5
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
        "You are a professional Indian F&O trader assistant. "
        "Receive structured signal data and give a concise, factual trade analysis. "
        "No generic disclaimers. Reference specific numbers from the data."
    )

    signal_data = {
        "instrument":       candidate["instrument"],
        "direction":        candidate["direction"],
        "score":            score["total"],
        "score_breakdown":  score.get("scores", {}),
        "entry":            candidate["entry"],
        "stop_loss":        candidate["stopLoss"],
        "targets":          candidate["targets"],
        "rr":               candidate.get("rr"),
        "spot":             candidate.get("spotPrice") or candidate.get("ema20"),
        # Trend
        "ema20_vs_200":     f"{candidate.get('ema20')} vs {candidate.get('ema200')}",
        "supertrend":       "bull" if candidate.get("supertrendBullish") else "bear",
        "pdh_breakout":     candidate.get("pdBreakout", False),
        "gap_up":           candidate.get("gapUp", False),
        "gap_down":         candidate.get("gapDown", False),
        "gap_pct":          candidate.get("gapPct", 0),
        "sr_breakout":      candidate.get("srBreakout", False),
        "near_resistance":  candidate.get("nearResistance", False),
        "resistance":       candidate.get("resistance"),
        "support":          candidate.get("support"),
        # Momentum
        "rsi":              round(candidate["rsi"], 1),
        "adx":              round(candidate["adx"], 1),
        "macd_bullish":     candidate["macd"] > candidate["macdSignal"],
        "tf15_aligned":     candidate.get("tf15Aligned", False),
        # Volume / VWAP
        "vwap":             candidate.get("vwap"),
        "vwap_confirmed":   candidate.get("vwapConfirmed", False),
        "rel_volume":       candidate.get("relativeVolume"),
        "volume_spike":     candidate.get("volumeSpike", False),
        # Option chain
        "atm_iv":           candidate.get("atmIV"),
        "iv_rank":          candidate.get("ivRank"),
        "pcr":              candidate.get("pcr"),
        "oi_change_pct":    candidate.get("oiChangePct"),
        "max_pain_dist":    candidate.get("maxPainDistancePct"),
        "dte":              candidate.get("dte"),
        "expiry_type":      candidate.get("expiry"),
        "spread_pct":       candidate.get("spreadPct"),
        # Greeks
        "delta":            candidate.get("delta"),
        "theta_per_day":    candidate.get("theta"),
        # Market context
        "vix":              market["indiaVix"],
        "market_regime":    market.get("regime"),
        "ai_regime":        market.get("aiRegime"),
        "ai_action":        market.get("aiAction"),
        "breadth":          market.get("breadth"),
        "news_sentiment":   candidate.get("newsSentiment"),
    }

    user = (
        f"Signal data:\n{json.dumps(signal_data, indent=2)}"
        f"{news_block}\n\n"
        "Respond in exactly 3 labelled sections (2 sentences each):\n"
        "WHY THIS TRADE:\n"
        "WHY IT COULD FAIL:\n"
        "KEY RISKS:"
    )
    result = _call(system, user, max_tokens=320)
    text = result if result else rule_text
    _EXPLANATION_CACHE[cache_key] = text
    return text


def get_candidate_shortlist(candidates: list[dict], market: dict) -> dict:
    """Use AI to shortlist the most promising candidates before Angel One option chain calls.

    Reduces Angel One API calls from 41 → 10-12 by filtering out candidates that
    don't meet proven F&O criteria given the current regime.

    Returns:
        {
          "shortlist": ["NIFTY", "BANKNIFTY", ...],   # symbols to fetch OC for
          "skipped":   {"WIPRO": "ADX 12 no trend", ...},
          "regimeNote": "...",
          "source": "ai" | "fallback"
        }
    On any AI failure, returns source="fallback" with all candidate symbols so the
    scan degrades gracefully to the original brute-force behaviour.
    """
    all_symbols = [c["underlying"] for c in candidates]

    if not openai_enabled():
        return {"shortlist": all_symbols, "skipped": {}, "regimeNote": "", "source": "fallback"}

    from app.core.constants import SECTOR_MAP
    from app.services.signal_analytics import get_performance_context

    # ── Build compact candidate table for the prompt ──────────────────────────
    rows = []
    for c in candidates:
        sym      = c["underlying"]
        ema_bull = c["ema20"] > c.get("ema50", 0) > c.get("ema200", 0)
        ema_bear = c["ema20"] < c.get("ema50", float("inf")) < c.get("ema200", float("inf"))
        ema_tag  = "aligned" if (ema_bull or ema_bear) else "mixed"
        macd_ok  = (c["macd"] > c.get("macdSignal", 0)) if c["direction"] == "BUY" \
                   else (c["macd"] < c.get("macdSignal", 0))
        iv_rank  = c.get('ivRank')
        rel_vol  = c.get('relativeVolume')
        # Mark intraday fields as unavailable when market is closed (relVol=0 means no session data)
        iv_str   = f"ivRank={round(iv_rank)}" if iv_rank is not None else "ivRank=n/a"
        vol_str  = f"relVol={round(rel_vol, 1)}" if (rel_vol is not None and rel_vol > 0) else "relVol=n/a(closed)"
        vwap_str = "vwap=yes" if c.get('vwapConfirmed') else ("vwap=n/a(closed)" if (rel_vol or 0) == 0 else "vwap=no")
        rows.append(
            f"{sym}: dir={c['direction']} ema={ema_tag} rsi={c['rsi'] or 50} "
            f"adx={c['adx'] or 0} adxRising={c.get('adxRising') or False} "
            f"macd={'confirms' if macd_ok else 'diverges'} "
            f"{vol_str} {iv_str} {vwap_str} "
            f"eventRisk={c.get('eventRisk') or False} "
            f"sector={SECTOR_MAP.get(sym, 'other')}"
        )
    candidate_table = "\n".join(rows)

    # ── Layer 2: historical performance context (empty until trades accumulate) ─
    perf = get_performance_context()
    perf_block = ""
    if perf:
        perf_block = (
            f"\n\nYour historical performance ({perf['dataWindow']}, {perf['totalTrades']} trades, "
            f"{perf['overallWinRate']}% win rate):\n"
            f"By symbol: {perf['bySymbol']}\n"
            f"By score bucket: {perf['byScoreBucket']}\n"
            "Use this to down-rank symbols/score-ranges that historically underperform."
        )

    # ── Layer 3: regime-conditioned rules ─────────────────────────────────────
    vix       = market.get("indiaVix", 15)
    regime    = market.get("regime", "neutral")
    ai_action = market.get("aiAction", "selective")
    bias      = market.get("bias", "neutral")

    if vix > 20:
        regime_rule = (
            "VIX > 20: HIGH VOLATILITY. Only return NIFTY and BANKNIFTY. "
            "No individual stocks — wide spreads and gamma risk make stock options unfavourable."
        )
        max_candidates = 4
    elif vix > 16:
        regime_rule = (
            f"VIX {vix:.1f}: ELEVATED VOLATILITY. Prefer index options. "
            "Accept max 2 stock options only if EMA fully aligned + ADX > 25."
        )
        max_candidates = 8
    else:
        regime_rule = (
            f"VIX {vix:.1f}: NORMAL. Balanced mix of index and liquid stocks allowed."
        )
        max_candidates = 12

    if ai_action == "avoid":
        regime_rule += " AI regime action=AVOID: return only NIFTY/BANKNIFTY as hedges, nothing else."
        max_candidates = 2
    elif ai_action == "reduce":
        max_candidates = min(max_candidates, 6)
        regime_rule += " AI regime action=REDUCE: cut shortlist to preserve capital."

    system = """You are a senior NSE F&O desk analyst. Shortlist the most promising \
option-chain candidates so we only call the Angel One API for symbols worth trading.

HARD GATES — skip immediately if any of these fail (no exceptions):
1. EMA alignment: 20>50>200 for BUY, 20<50<200 for SELL. Mixed EMA = skip.
2. IV Rank > 60: buying expensive options destroys edge. Skip.
3. eventRisk=True: earnings/event within 2 days = binary risk. Skip.

QUALITY SCORING — score each surviving candidate 0-5 points, shortlist the highest scorers:
+2  ADX >= 25 (strong trend). ADX 18-24 = +1 (weak trend). ADX < 18 = +0.
+1  RSI in momentum zone: BUY = 42-76 (trend continuation; mild overbought ok for momentum),
    SELL = 24-68 (overbought-reversal is a valid PE entry, do not penalise RSI 56-68 for SELL).
+1  MACD confirms direction (macd=confirms). Diverging = -0, not a disqualifier.
+1  relVol >= 0.9. If relVol=n/a(closed) treat as neutral (+0), do NOT penalise.
+1  vwap=yes. If vwap=n/a(closed) treat as neutral (+0), do NOT penalise.
NOTE: ivRank is a HARD GATE only — low ivRank (< 60) is GOOD (cheap options). Do not use ivRank in scoring.

SELECTION RULES:
- Shortlist candidates scoring >= 2 points (at least 2 quality signals present).
- If no candidate scores >= 2, admit the top 3 by score anyway — always return something.
- Max 1 symbol per sector (banking, IT, pharma, oil_gas, etc.). Pick highest scorer per sector.
- Indices (NIFTY, BANKNIFTY) get a +1 bonus for liquidity — always prefer them when EMA aligned.
- Always include NIFTY or BANKNIFTY if their EMA is aligned, regardless of other scores.

REGIME RULE (applied after scoring):
""" + regime_rule + f"""

Current market: regime={regime}, bias={bias}, VIX={vix:.1f}
Return at most {max_candidates} symbols.""" + perf_block + """

OUTPUT: Respond with valid JSON only. No markdown, no extra text.
{
  "shortlist": ["SYM1", "SYM2", ...],
  "skipped": {"SYM": "one-line reason", ...},
  "regimeNote": "one sentence on how regime shaped the shortlist"
}"""

    user = f"Candidate indicators:\n{candidate_table}"

    raw = _call(system, user, max_tokens=600)
    if not raw:
        logger.warning("AI shortlist: empty response — falling back to all candidates")
        return {"shortlist": all_symbols, "skipped": {}, "regimeNote": "", "source": "fallback"}

    try:
        import json, re
        # Strip any markdown code fences the model might add
        cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
        data    = json.loads(cleaned)
        shortlist = [s.upper() for s in data.get("shortlist", []) if s]
        # Safety: ensure shortlist only contains symbols from original list
        shortlist = [s for s in shortlist if s in all_symbols]
        if not shortlist:
            raise ValueError("Empty shortlist after validation")
        logger.info(
            "AI shortlist: %d/%d candidates selected (%s skipped)",
            len(shortlist), len(all_symbols), len(data.get("skipped", {}))
        )
        return {
            "shortlist":   shortlist,
            "skipped":     data.get("skipped", {}),
            "regimeNote":  data.get("regimeNote", ""),
            "source":      "ai",
        }
    except Exception as exc:
        logger.warning("AI shortlist parse failed (%s) — falling back to all candidates", exc)
        return {"shortlist": all_symbols, "skipped": {}, "regimeNote": "", "source": "fallback"}


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
