from datetime import datetime, timedelta, time as dtime  # timedelta used in signal_valid_until
from zoneinfo import ZoneInfo


CATEGORY_MAX = {
    "trend": 25,
    "momentum": 20,
    "volume": 15,
    "optionChain": 20,
    "sentiment": 10,
    "riskReward": 10,
}


DEFAULT_SETTINGS = {
    "accountCapital": 30000,
    "riskPercent": 2,
    "maxSpread": 1.5,
    "minVolume": 50000,
    "eventWindow": 60,
    "lossStreak": 0,
    "maxDailyLossPct": 3,
    "maxWeeklyDrawdownPct": 8,
    "maxMonthlyDrawdownPct": 15,
    "minScore": 72,   # fixed quality floor — never relaxed
    "maxSignals": 5,  # show up to 5 signals when the wider scan universe finds them
}


def clamp(value, low, high):
    return max(low, min(high, value))


def merged_settings(settings):
    merged = DEFAULT_SETTINGS.copy()
    if settings:
        merged.update({key: value for key, value in settings.items() if value is not None})
    return merged


def is_bullish_trend(candidate):
    return candidate["ema20"] > candidate["ema50"] > candidate["ema200"]


def is_bearish_trend(candidate):
    return candidate["ema20"] < candidate["ema50"] < candidate["ema200"]


def trend_score(candidate):
    aligned = is_bullish_trend(candidate) if candidate["direction"] == "BUY" else is_bearish_trend(candidate)
    score = 12 if aligned else 4

    # Supertrend confirmation (+6 when it agrees with EMA direction)
    st_bullish = candidate.get("supertrendBullish", True)
    st_matches = (
        (candidate["direction"] == "BUY"  and     st_bullish) or
        (candidate["direction"] == "SELL" and not st_bullish)
    )
    if st_matches:
        score += 6

    # Previous day high/low breakout — strong momentum signal
    if candidate.get("pdBreakout"):
        score += 4

    # 15-min EMA9/21 confirms daily direction — adds intraday confluence
    if candidate.get("tf15Aligned"):
        score += 3

    # Price action pattern
    price_action = candidate["priceAction"].lower()
    if "breakout" in price_action:
        score += 3
    elif "retest held" in price_action:
        score += 2

    return clamp(score, 0, CATEGORY_MAX["trend"])


def momentum_score(candidate):
    score = 0
    if candidate["direction"] == "BUY" and 55 <= candidate["rsi"] <= 70:
        score += 7
    elif candidate["direction"] == "SELL" and 30 <= candidate["rsi"] <= 45:
        score += 7
    elif 48 < candidate["rsi"] < 55:
        score += 3

    if candidate["direction"] == "BUY" and candidate["macd"] > candidate["macdSignal"]:
        score += 6
    if candidate["direction"] == "SELL" and candidate["macd"] < candidate["macdSignal"]:
        score += 6

    if candidate["adx"] >= 25:
        score += 7
    elif candidate["adx"] >= 20:
        score += 4
    elif candidate["adx"] >= 16:
        score += 2

    return clamp(score, 0, CATEGORY_MAX["momentum"])


def volume_score(candidate):
    score = 0
    if candidate["relativeVolume"] >= 1.6:
        score += 9
    elif candidate["relativeVolume"] >= 1.3:
        score += 6
    elif candidate["relativeVolume"] >= 1.0:
        score += 3

    if candidate["optionVolume"] >= 100000:
        score += 6
    elif candidate["optionVolume"] >= 50000:
        score += 4
    elif candidate["optionVolume"] >= 20000:
        score += 2

    return clamp(score, 0, CATEGORY_MAX["volume"])


def option_chain_score(candidate):
    score = 0
    if candidate["oiChangePct"] >= 10:
        score += 7
    elif candidate["oiChangePct"] >= 6:
        score += 5
    elif candidate["oiChangePct"] >= 3:
        score += 3

    if 0.95 <= candidate["pcr"] <= 1.2:
        score += 6
    elif 0.8 <= candidate["pcr"] <= 1.35:
        score += 3

    score += 4 if candidate["maxPainDistancePct"] >= 1.0 else 1

    if candidate["spreadPct"] <= 2:
        score += 3
    elif candidate["spreadPct"] <= 3:
        score += 1

    # IV check — raw level ladder
    atm_iv = candidate.get("atmIV", 0)
    if atm_iv > 0:
        if atm_iv < 20:
            score += 3    # cheap premium — excellent for buyers
        elif atm_iv < 28:
            score += 1    # fair value
        elif atm_iv < 35:
            pass          # neutral zone
        elif atm_iv < 42:
            score -= 2    # somewhat expensive
        else:
            score -= 4    # very expensive — erodes edge significantly

    # IV Rank — relative expensiveness vs 52-week history (more precise than raw level)
    iv_rank = candidate.get("ivRank")
    if iv_rank is not None:
        if iv_rank < 20:
            score += 3    # historically cheap IV — option buyers' sweet spot
        elif iv_rank < 35:
            score += 1
        elif iv_rank > 75:
            score -= 3    # historically expensive — premium hurts buyers
        elif iv_rank > 55:
            score -= 1

    return clamp(score, 0, CATEGORY_MAX["optionChain"])


def sentiment_score(candidate, market):
    score = candidate.get("marketSentiment", 0)
    vix = market["indiaVix"]
    # Progressive VIX penalty — calm markets get a bonus, elevated markets lose points.
    # This works in tandem with the VIX-adjusted SL in nse.py: high VIX already widens
    # the SL (reducing RR and lots), so the scoring penalty adds a second filter layer.
    if vix <= 14:
        score += 3    # very calm trending environment
    elif vix <= 16:
        score += 1    # normal
    elif vix <= 18:
        score += 0    # neutral — no bonus, no penalty
    elif vix <= 20:
        score -= 2    # elevated — trade only high-conviction setups
    else:             # 20–22  (above 22 = hard gate, never reaches here)
        score -= 4    # high risk environment — very few signals should pass
    if market["breadth"] > 1.2:
        score += 1
    return clamp(score, 0, CATEGORY_MAX["sentiment"])


def risk_reward_score(candidate):
    rr = candidate["rr"]
    if rr >= 3:
        return 10
    if rr >= 2.5:
        return 8
    if rr >= 2:
        return 6
    return 0


def score_candidate(candidate, market):
    scores = {
        "trend": trend_score(candidate),
        "momentum": momentum_score(candidate),
        "volume": volume_score(candidate),
        "optionChain": option_chain_score(candidate),
        "sentiment": sentiment_score(candidate, market),
        "riskReward": risk_reward_score(candidate),
    }
    return {"scores": scores, "total": sum(scores.values())}


def event_blocked(candidate, market, settings):
    if candidate.get("eventRisk"):
        return True
    for event in market.get("eventCalendar", []):
        if event["severity"] == "high" and event["minutesAway"] <= settings["eventWindow"]:
            return True
    return False


def hard_gate_failures(candidate, market, risk_state, settings):
    failures = []
    aligned = is_bullish_trend(candidate) if candidate["direction"] == "BUY" else is_bearish_trend(candidate)

    if settings["lossStreak"] >= 3:
        failures.append("Stop-trading rule active after 3 consecutive losses.")
    if risk_state["dailyLossPct"] >= settings["maxDailyLossPct"]:
        failures.append("Daily loss limit reached.")
    if risk_state["weeklyDrawdownPct"] >= settings["maxWeeklyDrawdownPct"]:
        failures.append("Weekly drawdown limit reached.")
    if risk_state["monthlyDrawdownPct"] >= settings["maxMonthlyDrawdownPct"]:
        failures.append("Monthly drawdown limit reached.")
    if candidate["rr"] < 2:
        failures.append("Risk reward is below 1:2.")
    if not aligned:
        failures.append("Trend is not aligned with trade direction.")
    if candidate["optionVolume"] < settings["minVolume"]:
        failures.append("Option volume is below minimum liquidity threshold.")
    if candidate["spreadPct"] > settings["maxSpread"]:
        failures.append("Bid-ask spread is excessive.")
    if event_blocked(candidate, market, settings):
        failures.append("Major event risk is too close or manually flagged.")
    if market["indiaVix"] >= 22:
        failures.append("India VIX is elevated beyond directional buying threshold.")

    # Time-of-day gate — avoid opening chop and closing volatility
    now_ist     = datetime.now(ZoneInfo("Asia/Kolkata"))
    now_ist_t   = now_ist.time()
    if dtime(9, 15) <= now_ist_t <= dtime(10, 0):
        failures.append("Opening volatility window (9:15–10:00 IST) — wait for price discovery and trend confirmation.")
    if dtime(14, 45) <= now_ist_t <= dtime(15, 30):
        failures.append("Closing volatility window (14:45–15:30 IST) — avoid new entries near close.")

    # Expiry day gate — Thursday after 11:00 IST
    # Weekly options expire every Thursday; gamma accelerates and time decay becomes
    # punishing after 11am, making new long-option entries a negative-expectancy bet.
    if now_ist.weekday() == 3 and now_ist_t >= dtime(11, 0):
        if candidate.get("expiry") == "Weekly":
            failures.append(
                "Weekly expiry day (Thursday) after 11:00 IST — accelerated gamma and "
                "time decay make new long-option entries unfavourable."
            )

    return failures


def position_sizing(candidate, settings):
    rupee_risk = settings["accountCapital"] * (settings["riskPercent"] / 100)
    per_unit_risk = abs(candidate["entry"] - candidate["stopLoss"])
    lot_risk = per_unit_risk * candidate["lotSize"]
    lots = int(rupee_risk // lot_risk) if lot_risk else 0

    return {
        "rupeeRisk": round(rupee_risk),
        "perUnitRisk": round(per_unit_risk, 2),
        "lotRisk": round(lot_risk),
        "lots": max(0, lots),
        "quantity": max(0, lots) * candidate["lotSize"],
    }


def signal_valid_until(candidate):
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    valid_until = now + timedelta(minutes=candidate["signalValidMinutes"])
    return valid_until.strftime("%I:%M %p")


def build_explanation(candidate, sizing, approved):
    if not approved:
        return (
            "Rejected before recommendation because deterministic risk gates or score "
            "requirements were not satisfied. AI may explain this rejection, but it "
            "cannot convert the setup into a trade."
        )

    return (
        f"{candidate['instrument']} qualifies because trend, momentum, liquidity and "
        f"option-chain evidence align with the trade direction. The setup uses a "
        f"defined stop at {candidate['stopLoss']} and the account risk rule limits "
        f"size to {sizing['lots']} lot(s). The trade remains valid only while price "
        "action holds the entry structure and event risk does not change."
    )


def build_risks(candidate, market):
    risks = []
    if candidate["spreadPct"] > 2:
        risks.append("Spread can reduce realized reward.")
    if market["indiaVix"] > 16:
        risks.append("Volatility is above the calm-market zone.")
    if candidate["expiry"] == "Weekly":
        risks.append("Weekly options carry faster time decay after failed follow-through.")

    for event in market.get("eventCalendar", []):
        if event["severity"] == "high" and event["minutesAway"] <= 1440:
            risks.append(f"{event['name']} can change market sentiment within the next trading day.")

    risks.extend(candidate.get("notes", []))
    return risks[:5]


def scan_market(candidates, market, risk_state, settings=None):
    settings  = merged_settings(settings)
    min_score = settings["minScore"]

    evaluated = []
    for candidate in candidates:
        failures = hard_gate_failures(candidate, market, risk_state, settings)
        score    = score_candidate(candidate, market)
        sizing   = position_sizing(candidate, settings)

        approved = (
            not failures
            and score["total"] >= min_score
            and sizing["lots"] >= 1
        )

        rejection_reasons = list(failures)
        if score["total"] < min_score:
            rejection_reasons.append(f"Score {score['total']} is below threshold {min_score}.")
        if sizing["lots"] < 1:
            rejection_reasons.append("Position size would exceed configured account risk.")

        evaluated.append({
            "candidate":        candidate,
            "approved":         approved,
            "score":            score,
            "sizing":           sizing,
            "validUntil":       signal_valid_until(candidate),
            "explanation":      build_explanation(candidate, sizing, approved),
            "risks":            build_risks(candidate, market),
            "rejectionReasons": rejection_reasons,
        })

    approved_list = sorted(
        [item for item in evaluated if item["approved"]],
        key=lambda item: item["score"]["total"],
        reverse=True,
    )[: settings["maxSignals"]]

    approved_ids = {item["candidate"]["id"] for item in approved_list}
    rejected     = [item for item in evaluated if item["candidate"]["id"] not in approved_ids]

    return {
        "settings":         settings,
        "approved":         approved_list,
        "rejected":         rejected,
        "noTrade":          len(approved_list) == 0,
        "scoreThreshold":   min_score,
        "thresholdRelaxed": False,
        "generatedAt":      datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
    }


def telegram_text(scan, market):
    if scan["noTrade"]:
        return "\n".join(
            [
                "NO TRADE MODE",
                f"Market: {market['regime']}",
                "Reason: No setup passed hard risk gates and score threshold.",
                "Action: Preserve capital. Wait for clean alignment.",
            ]
        )

    messages = []
    for item in scan["approved"]:
        candidate = item["candidate"]
        messages.append(
            "\n".join(
                [
                    f"{candidate['direction']} {candidate['instrument']}",
                    f"Entry: {candidate['entry']}",
                    f"SL: {candidate['stopLoss']}",
                    f"T1/T2/T3: {' / '.join(str(target) for target in candidate['targets'])}",
                    f"RR: 1:{candidate['rr']}",
                    f"Confidence Score: {item['score']['total']}/100",
                    f"Valid Until: {item['validUntil']}",
                    f"Size: {item['sizing']['lots']} lot(s), max risk Rs {item['sizing']['rupeeRisk']}",
                    f"Why: {item['explanation']}",
                    f"Risks: {'; '.join(item['risks'])}",
                ]
            )
        )
    return "\n\n".join(messages)

