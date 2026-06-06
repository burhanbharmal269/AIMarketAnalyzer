(function () {
  const categoryMax = {
    trend: 25,
    momentum: 20,
    volume: 15,
    optionChain: 20,
    sentiment: 10,
    riskReward: 10
  };

  function round(value, decimals) {
    const factor = Math.pow(10, decimals || 0);
    return Math.round(value * factor) / factor;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function istHourMinute() {
    const parts = new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    }).formatToParts(new Date());
    const h = parseInt(parts.find(function (p) { return p.type === "hour"; }).value, 10);
    const m = parseInt(parts.find(function (p) { return p.type === "minute"; }).value, 10);
    return [h, m];
  }

  function timeGateBlocked() {
    const hm = istHourMinute();
    const h = hm[0], m = hm[1];
    // Opening auction volatility: 9:15 – 9:30 IST
    if (h === 9 && m >= 15 && m < 30) { return true; }
    // Pre-close / expiry settlement: 14:45 – 15:30 IST
    if (h === 14 && m >= 45) { return true; }
    if (h === 15 && m <= 30) { return true; }
    return false;
  }

  function isBullishTrend(candidate) {
    return candidate.ema20 > candidate.ema50 && candidate.ema50 > candidate.ema200;
  }

  function isBearishTrend(candidate) {
    return candidate.ema20 < candidate.ema50 && candidate.ema50 < candidate.ema200;
  }

  function trendScore(candidate) {
    const aligned = candidate.direction === "BUY" ? isBullishTrend(candidate) : isBearishTrend(candidate);
    let score = aligned ? 18 : 6;

    if (candidate.priceAction.toLowerCase().includes("breakout")) {
      score += 4;
    }
    if (candidate.priceAction.toLowerCase().includes("retest held")) {
      score += 3;
    }
    return clamp(score, 0, categoryMax.trend);
  }

  function momentumScore(candidate) {
    let score = 0;
    if (candidate.direction === "BUY" && candidate.rsi >= 55 && candidate.rsi <= 70) {
      score += 7;
    } else if (candidate.direction === "SELL" && candidate.rsi <= 45 && candidate.rsi >= 30) {
      score += 7;
    } else if (candidate.rsi > 48 && candidate.rsi < 55) {
      score += 3;
    }

    if (candidate.direction === "BUY" && candidate.macd > candidate.macdSignal) {
      score += 6;
    }
    if (candidate.direction === "SELL" && candidate.macd < candidate.macdSignal) {
      score += 6;
    }
    if (candidate.adx >= 25) {
      score += 7;
    } else if (candidate.adx >= 20) {
      score += 4;
    } else if (candidate.adx >= 16) {
      score += 2;
    }
    return clamp(score, 0, categoryMax.momentum);
  }

  function volumeScore(candidate) {
    let score = 0;
    if (candidate.relativeVolume >= 1.6) {
      score += 9;
    } else if (candidate.relativeVolume >= 1.3) {
      score += 6;
    } else if (candidate.relativeVolume >= 1.0) {
      score += 3;
    }

    if (candidate.optionVolume >= 100000) {
      score += 6;
    } else if (candidate.optionVolume >= 50000) {
      score += 4;
    } else if (candidate.optionVolume >= 20000) {
      score += 2;
    }
    return clamp(score, 0, categoryMax.volume);
  }

  function optionChainScore(candidate) {
    let score = 0;
    if (candidate.oiChangePct >= 10) {
      score += 7;
    } else if (candidate.oiChangePct >= 6) {
      score += 5;
    } else if (candidate.oiChangePct >= 3) {
      score += 3;
    }

    if (candidate.pcr >= 0.95 && candidate.pcr <= 1.2) {
      score += 6;
    } else if (candidate.pcr >= 0.8 && candidate.pcr <= 1.35) {
      score += 3;
    }

    if (candidate.maxPainDistancePct >= 1.0) {
      score += 4;
    } else {
      score += 1;
    }

    if (candidate.spreadPct <= 2) {
      score += 3;
    } else if (candidate.spreadPct <= 3) {
      score += 1;
    }
    return clamp(score, 0, categoryMax.optionChain);
  }

  function sentimentScore(candidate, market) {
    let score = candidate.marketSentiment || 0;
    if (market.indiaVix <= 16) {
      score += 2;
    } else if (market.indiaVix > 20) {
      score -= 3;
    }
    if (market.breadth > 1.2) {
      score += 1;
    }
    return clamp(score, 0, categoryMax.sentiment);
  }

  function riskRewardScore(candidate) {
    if (candidate.rr >= 3) {
      return 10;
    }
    if (candidate.rr >= 2.5) {
      return 8;
    }
    if (candidate.rr >= 2) {
      return 6;
    }
    return 0;
  }

  function getSettings(input) {
    return {
      accountCapital: Number(input.accountCapital || 500000),
      riskPercent: Number(input.riskPercent || 1),
      maxSpread: Number(input.maxSpread || 3),
      minVolume: Number(input.minVolume || 20000),
      eventWindow: Number(input.eventWindow || 120),
      lossStreak: Number(input.lossStreak || 0),
      maxDailyLossPct: 3,
      maxWeeklyDrawdownPct: 8,
      maxMonthlyDrawdownPct: 15,
      minScore: 72,
      maxSignals: 5
    };
  }

  function eventBlocked(candidate, market, settings) {
    if (candidate.eventRisk) {
      return true;
    }
    return market.eventCalendar.some(function (event) {
      return event.severity === "high" && event.minutesAway <= settings.eventWindow;
    });
  }

  function hardGateFailures(candidate, market, riskState, settings) {
    const failures = [];
    const alignedTrend = candidate.direction === "BUY" ? isBullishTrend(candidate) : isBearishTrend(candidate);

    if (timeGateBlocked()) {
      failures.push("Time gate: opening (9:15–9:30) or pre-close (14:45–15:30) IST window.");
    }
    if (settings.lossStreak >= 3) {
      failures.push("Stop-trading rule active after 3 consecutive losses.");
    }
    if (riskState.dailyLossPct >= settings.maxDailyLossPct) {
      failures.push("Daily loss limit reached.");
    }
    if (riskState.weeklyDrawdownPct >= settings.maxWeeklyDrawdownPct) {
      failures.push("Weekly drawdown limit reached.");
    }
    if (riskState.monthlyDrawdownPct >= settings.maxMonthlyDrawdownPct) {
      failures.push("Monthly drawdown limit reached.");
    }
    if (candidate.rr < 2) {
      failures.push("Risk reward is below 1:2.");
    }
    if (!alignedTrend) {
      failures.push("Trend is not aligned with trade direction.");
    }
    if (candidate.optionVolume < settings.minVolume) {
      failures.push("Option volume is below minimum liquidity threshold.");
    }
    if (candidate.spreadPct > settings.maxSpread) {
      failures.push("Bid-ask spread is excessive.");
    }
    if (eventBlocked(candidate, market, settings)) {
      failures.push("Major event risk is too close or manually flagged.");
    }
    if (market.indiaVix >= 22) {
      failures.push("India VIX is elevated beyond directional buying threshold.");
    }

    return failures;
  }

  function scoreCandidate(candidate, market) {
    const scores = {
      trend: trendScore(candidate),
      momentum: momentumScore(candidate),
      volume: volumeScore(candidate),
      optionChain: optionChainScore(candidate),
      sentiment: sentimentScore(candidate, market),
      riskReward: riskRewardScore(candidate)
    };
    const total = Object.keys(scores).reduce(function (sum, key) {
      return sum + scores[key];
    }, 0);
    return { scores: scores, total: total };
  }

  function positionSizing(candidate, settings) {
    const rupeeRisk = settings.accountCapital * (settings.riskPercent / 100);
    const perUnitRisk = Math.abs(candidate.entry - candidate.stopLoss);
    const lotRisk = perUnitRisk * candidate.lotSize;
    const lots = Math.max(0, Math.floor(rupeeRisk / lotRisk));
    return {
      rupeeRisk: round(rupeeRisk, 0),
      perUnitRisk: round(perUnitRisk, 2),
      lotRisk: round(lotRisk, 0),
      lots: lots,
      quantity: lots * candidate.lotSize
    };
  }

  function expiryText(candidate, market) {
    const scannedAt = new Date(market.timestamp);
    const validUntil = new Date(scannedAt.getTime() + candidate.signalValidMinutes * 60000);
    return validUntil.toLocaleTimeString("en-IN", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "Asia/Kolkata"
    });
  }

  function buildExplanation(candidate, score, sizing, approved) {
    if (!approved) {
      return "Rejected before recommendation because deterministic risk gates or score requirements were not satisfied. AI may explain this rejection, but it cannot convert the setup into a trade.";
    }
    return [
      candidate.instrument + " qualifies because trend, momentum, liquidity and option-chain evidence align with the trade direction.",
      "The setup uses a defined stop at " + candidate.stopLoss + " and the maximum account risk rule limits size to " + sizing.lots + " lot(s).",
      "The trade remains valid only while price action holds the entry structure and market conditions do not change around major events."
    ].join(" ");
  }

  function buildRisks(candidate, market) {
    const risks = [];
    if (candidate.spreadPct > 2) {
      risks.push("Spread can reduce realized reward.");
    }
    if (market.indiaVix > 16) {
      risks.push("Volatility is above the calm-market zone.");
    }
    if (candidate.expiry === "Weekly") {
      risks.push("Weekly options carry faster time decay after failed follow-through.");
    }
    market.eventCalendar.forEach(function (event) {
      if (event.severity === "high" && event.minutesAway <= 1440) {
        risks.push(event.name + " can change market sentiment within the next trading day.");
      }
    });
    candidate.notes.forEach(function (note) {
      risks.push(note);
    });
    return risks.slice(0, 5);
  }

  function runScan(data, inputSettings) {
    const settings = getSettings(inputSettings);
    const results = data.candidates.map(function (candidate) {
      const failures = hardGateFailures(candidate, data.market, data.riskState, settings);
      const score = scoreCandidate(candidate, data.market);
      const sizing = positionSizing(candidate, settings);
      const approved = failures.length === 0 && score.total >= settings.minScore && sizing.lots >= 1;
      const rejectionReasons = failures.slice();

      if (score.total < settings.minScore) {
        rejectionReasons.push("Score " + score.total + " is below minimum " + settings.minScore + ".");
      }
      if (sizing.lots < 1) {
        rejectionReasons.push("Position size would exceed configured account risk.");
      }

      return {
        candidate: candidate,
        approved: approved,
        score: score,
        sizing: sizing,
        validUntil: expiryText(candidate, data.market),
        explanation: buildExplanation(candidate, score, sizing, approved),
        risks: buildRisks(candidate, data.market),
        rejectionReasons: rejectionReasons
      };
    });

    const approved = results
      .filter(function (item) { return item.approved; })
      .sort(function (a, b) { return b.score.total - a.score.total; })
      .slice(0, settings.maxSignals);

    const rejected = results.filter(function (item) {
      return !approved.some(function (approvedItem) {
        return approvedItem.candidate.id === item.candidate.id;
      });
    });

    return {
      settings: settings,
      approved: approved,
      rejected: rejected,
      noTrade: approved.length === 0,
      generatedAt: new Date()
    };
  }

  function backtestMetrics(backtest) {
    const winRate = backtest.totalTrades ? (backtest.wins / backtest.totalTrades) * 100 : 0;
    const profitFactor = backtest.grossLossR ? backtest.grossProfitR / backtest.grossLossR : 0;
    return {
      winRate: round(winRate, 1),
      profitFactor: round(profitFactor, 2),
      maxDrawdownPct: backtest.maxDrawdownPct,
      sharpeProxy: backtest.sharpeProxy
    };
  }

  function telegramText(scan, market) {
    if (scan.noTrade) {
      return [
        "NO TRADE MODE",
        "Market: " + market.regime,
        "Reason: No setup passed hard risk gates and score threshold.",
        "Action: Preserve capital. Wait for clean alignment."
      ].join("\n");
    }

    return scan.approved.map(function (item) {
      const c = item.candidate;
      return [
        c.direction + " " + c.instrument,
        "Entry: " + c.entry,
        "SL: " + c.stopLoss,
        "T1/T2/T3: " + c.targets.join(" / "),
        "RR: 1:" + c.rr,
        "Confidence Score: " + item.score.total + "/100",
        "Valid Until: " + item.validUntil,
        "Size: " + item.sizing.lots + " lot(s), max risk Rs " + item.sizing.rupeeRisk,
        "Why: " + item.explanation,
        "Risks: " + item.risks.join("; ")
      ].join("\n");
    }).join("\n\n");
  }

  window.ResearchEngine = {
    categoryMax: categoryMax,
    runScan: runScan,
    backtestMetrics: backtestMetrics,
    telegramText: telegramText,
    round: round
  };
})();
