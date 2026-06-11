(function () {
  const data   = window.RESEARCH_DATA;
  const engine = window.ResearchEngine;

  let latestScan   = null;
  let latestMarket = data.market;
  let apiAvailable = false;

  const els = {
    scanButton:         document.getElementById("scanButton"),
    scanBar:            document.getElementById("scanBar"),
    toast:              document.getElementById("toast"),
    telegramButton:     document.getElementById("telegramButton"),
    closeDialog:        document.getElementById("closeDialog"),
    sendTelegramButton: document.getElementById("sendTelegramButton"),
    telegramDialog:     document.getElementById("telegramDialog"),
    telegramPreview:    document.getElementById("telegramPreview"),
    marketRegime:       document.getElementById("marketRegime"),
    marketBias:         document.getElementById("marketBias"),
    approvedCount:      document.getElementById("approvedCount"),
    rejectedCount:      document.getElementById("rejectedCount"),
    capitalMode:        document.getElementById("capitalMode"),
    riskState:          document.getElementById("riskState"),
    noTradeBanner:      document.getElementById("noTradeBanner"),
    // Journal analytics
    aTotalTrades: document.getElementById("aTotalTrades"),
    aWinRate:     document.getElementById("aWinRate"),
    aTotalR:      document.getElementById("aTotalR"),
    aProfitFactor:document.getElementById("aProfitFactor"),
    aAvgWin:      document.getElementById("aAvgWin"),
    aAvgLoss:     document.getElementById("aAvgLoss"),
    aBest:        document.getElementById("aBest"),
    aWorst:       document.getElementById("aWorst"),

    signalsGrid:        document.getElementById("signalsGrid"),
    scanTimestamp:      document.getElementById("scanTimestamp"),
    riskChecklist:      document.getElementById("riskChecklist"),
    dailySummary:       document.getElementById("dailySummary"),
    summaryProvider:    document.getElementById("summaryProvider"),
    winRate:            document.getElementById("winRate"),
    profitFactor:       document.getElementById("profitFactor"),
    maxDrawdown:        document.getElementById("maxDrawdown"),
    sharpeRatio:        document.getElementById("sharpeRatio"),
    strategyRows:       document.getElementById("strategyRows"),
    backtestSource:     document.getElementById("backtestSource"),
    liveDataDot:        document.getElementById("liveDataDot"),
    liveDataLabel:      document.getElementById("liveDataLabel"),
    // Journal
    logTradeButton:     document.getElementById("logTradeButton"),
    journalForm:        document.getElementById("journalForm"),
    saveJournalButton:  document.getElementById("saveJournalButton"),
    cancelJournalButton:document.getElementById("cancelJournalButton"),
    journalRows:        document.getElementById("journalRows"),
    jInstrument:        document.getElementById("jInstrument"),
    jDirection:         document.getElementById("jDirection"),
    jEntry:             document.getElementById("jEntry"),
    jStopLoss:          document.getElementById("jStopLoss"),
    jTarget1:           document.getElementById("jTarget1"),
    jTarget2:           document.getElementById("jTarget2"),
    jTarget3:           document.getElementById("jTarget3"),
    jScore:             document.getElementById("jScore"),
    jNotes:             document.getElementById("jNotes"),
    settings: {
      accountCapital: document.getElementById("accountCapital"),
      riskPercent:    document.getElementById("riskPercent"),
      lossStreak:     document.getElementById("lossStreak"),
      maxSpread:      document.getElementById("maxSpread"),
      minVolume:      document.getElementById("minVolume"),
      eventWindow:    document.getElementById("eventWindow")
    }
  };

  // ── helpers ──────────────────────────────────────────────────────────────

  var _toastTimer = null;
  function showToast(msg, type) {
    if (!els.toast) return;
    clearTimeout(_toastTimer);
    els.toast.textContent = msg;
    els.toast.className   = "toast " + (type || "info") + " visible";
    _toastTimer = setTimeout(function () {
      els.toast.classList.remove("visible");
    }, 3500);
  }

  function setScanLoading(loading) {
    els.scanButton.disabled = loading;
    if (loading) {
      els.scanButton.classList.add("loading");
      els.scanButton.innerHTML = "<span class=\"scan-spinner\"></span>Scanning… 0s";
      if (els.scanBar) {
        els.scanBar.className    = "scan-bar";
        els.scanBar.textContent  = "Fetching live market data… 0s";
      }
      _startElapsedTimer();
    } else {
      _stopPolling();
      els.scanButton.classList.remove("loading");
      els.scanButton.textContent = "Run Scan";
      if (els.scanBar) els.scanBar.className = "scan-bar hidden";
    }
  }

  function flashMetrics() {
    document.querySelectorAll(".metric-panel").forEach(function (el) {
      el.classList.remove("updated");
      void el.offsetWidth;
      el.classList.add("updated");
    });
  }

  function settingValues() {
    return {
      accountCapital: +els.settings.accountCapital.value,
      riskPercent:    +els.settings.riskPercent.value,
      lossStreak:     +els.settings.lossStreak.value,
      maxSpread:      +els.settings.maxSpread.value,
      minVolume:      +els.settings.minVolume.value,
      eventWindow:    +els.settings.eventWindow.value
    };
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("en-IN").format(value);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function fmtDate(iso) {
    if (!iso) return "";
    try { return new Date(iso).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" }); }
    catch (_) { return iso.slice(0, 16); }
  }

  // ── score bars ────────────────────────────────────────────────────────────

  function scoreRows(item) {
    if (!item.score || !item.score.scores || Object.keys(item.score.scores).length === 0) {
      return "<div class=\"muted-note\">Not scored because this setup failed an early liquidity/structure filter.</div>";
    }
    const labels = {
      trend: "Trend", momentum: "Momentum", volume: "Volume",
      optionChain: "Option Chain", sentiment: "Sentiment", riskReward: "Risk Reward",
      news: "News Sentiment"
    };
    return Object.keys(item.score.scores).map(function (key) {
      const value = item.score.scores[key];
      const max   = engine.categoryMax[key];
      const width = Math.round((value / max) * 100);
      return [
        "<div class=\"score-row\">",
        "<span>" + labels[key] + "</span>",
        "<div class=\"bar\"><span style=\"width:" + width + "%\"></span></div>",
        "<strong>" + value + "</strong>",
        "</div>"
      ].join("");
    }).join("");
  }

  // ── signal cards ──────────────────────────────────────────────────────────

  function isSignalExpired(validUntilStr) {
    // validUntil is like "10:21 AM" — parse against today's date in IST
    try {
      var now   = new Date();
      var parts = validUntilStr.match(/(\d+):(\d+)\s*(AM|PM)/i);
      if (!parts) return false;
      var h = parseInt(parts[1], 10);
      var m = parseInt(parts[2], 10);
      if (parts[3].toUpperCase() === "PM" && h !== 12) h += 12;
      if (parts[3].toUpperCase() === "AM" && h === 12) h = 0;
      var expiry = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m, 0);
      return now > expiry;
    } catch (_) { return false; }
  }

  function renderApprovedCard(item) {
    const c       = item.candidate;
    const expired = isSignalExpired(item.validUntil);
    const logBtn  = apiAvailable && !expired
      ? "<button class=\"log-trade-btn\" data-instrument=\"" + escapeHtml(c.instrument) +
        "\" data-direction=\"" + escapeHtml(c.direction) +
        "\" data-entry=\""    + c.entry +
        "\" data-sl=\""       + c.stopLoss +
        "\" data-t1=\""       + (c.targets[0] || 0) +
        "\" data-t2=\""       + (c.targets[1] || 0) +
        "\" data-t3=\""       + (c.targets[2] || 0) +
        "\" data-score=\""    + item.score.total +
        "\">Log Trade</button>"
      : "";
    const expiredBanner = expired
      ? "<div class=\"expired-banner\">SIGNAL EXPIRED — do not enter at this price. Run a new scan.</div>"
      : "";

    const total = item.score.total;
    const scoreClass = total >= 85 ? "score-high" : total >= 72 ? "score-mid" : "score-low";
    const isBuy = c.direction === "BUY";
    const cardClass = "signal-card" + (isBuy ? " approved" : " sell-card") + (expired ? " expired" : "");

    return [
      "<article class=\"" + cardClass + "\">",
      expiredBanner,
      "<div class=\"signal-head\">",
      "<div>",
      "<div style=\"display:flex;align-items:center;gap:8px;margin-bottom:4px\">",
      "<span class=\"badge " + (isBuy ? "buy" : "sell") + "\">" + (isBuy ? "▲ BUY CE" : "▼ SELL PE") + "</span>",
      logBtn,
      "</div>",
      "<h3>" + escapeHtml(c.instrument) + "</h3>",
      "<span style=\"font-size:0.78rem;color:var(--muted)\">" + escapeHtml(c.style) + "</span>",
      "</div>",
      "<div class=\"score-badge " + scoreClass + "\">",
      "<span class=\"score-num\">" + total + "</span>",
      "<span class=\"score-label\">/ 100</span>",
      "</div>",
      "</div>",
      // Valid until bar
      "<div class=\"valid-until-bar\">Valid until " + escapeHtml(item.validUntil || "—") +
        " &nbsp;·&nbsp; " + item.sizing.lots + " lot(s) &nbsp;·&nbsp; RR 1:" + c.rr + "</div>",
      "<div class=\"trade-levels\">",
      "<div class=\"level-entry\"><span>Entry</span><strong>₹" + c.entry + "</strong></div>",
      "<div class=\"level-sl\"><span>Stop Loss</span><strong>₹" + c.stopLoss + "</strong></div>",
      "<div class=\"level-t1\"><span>Target 1</span><strong>₹" + c.targets[0] + "</strong></div>",
      "<div class=\"level-t2\"><span>Target 2</span><strong>₹" + c.targets[1] + "</strong></div>",
      "<div class=\"level-t3\"><span>Target 3</span><strong>₹" + c.targets[2] + "</strong></div>",
      "<div><span>Expiry</span><strong>" + escapeHtml(c.expiry) + (c.dte != null ? " (" + c.dte + "d)" : "") + "</strong></div>",
      "</div>",
      // Greeks row
      (c.delta != null ? [
        "<div class=\"greeks-row\">",
        "<span>Delta<strong>" + (c.delta >= 0 ? "+" : "") + c.delta.toFixed(3) + "</strong></span>",
        "<span>Theta<strong>" + c.theta.toFixed(2) + "/d</strong></span>",
        "<span>Vega<strong>+" + c.vega.toFixed(2) + "</strong></span>",
        "<span>ATM IV<strong>" + (c.atmIV || "—") + "%</strong></span>",
        "<span>DTE<strong>" + (c.dte != null ? c.dte + "d" : "—") + "</strong></span>",
        (c.ivRank != null
          ? "<span class=\"iv-rank iv-rank--" + (c.ivRank < 35 ? "low" : c.ivRank > 65 ? "high" : "mid") + "\">IV Rank<strong>" + c.ivRank + "</strong></span>"
          : ""),
        "<span class=\"tf15-badge tf15-badge--" + (c.tf15Aligned ? "ok" : "warn") + "\">15m<strong>" + (c.tf15Aligned ? "✓" : "✗") + "</strong></span>",
        "</div>"
      ].join("") : ""),
      "<div class=\"score-block\">" + scoreRows(item) + "</div>",
      "<div class=\"text-block\">",
      "<div class=\"reason-list\"><strong>AI Explanation</strong><p>" + escapeHtml(item.explanation) + "</p></div>",
      "<div class=\"risk-list\"><strong>Key Risks</strong><ul>" +
        item.risks.map(function (r) { return "<li>" + escapeHtml(r) + "</li>"; }).join("") +
      "</ul></div>",
      "</div>",
      "</article>"
    ].join("");
  }

  function renderRejectedCard(item) {
    const c = item.candidate;
    const hasScore = item.score && item.score.scores && Object.keys(item.score.scores).length > 0;
    const total = hasScore ? item.score.total : null;
    const scoreClass = total >= 85 ? "score-high" : total >= 72 ? "score-mid" : "score-low";
    const targets = c.targets || [0, 0, 0];
    const scoreBadge = hasScore
      ? [
        "<div class=\"score-badge " + scoreClass + "\">",
        "<span class=\"score-num\">" + total + "</span>",
        "<span class=\"score-label\">/ 100</span>",
        "</div>"
      ].join("")
      : [
        "<div class=\"score-badge score-low\">",
        "<span class=\"score-num\">N/A</span>",
        "<span class=\"score-label\">pre-filter</span>",
        "</div>"
      ].join("");
    return [
      "<article class=\"signal-card rejected-card\">",
      "<div class=\"signal-head\">",
      "<div>",
      "<div style=\"margin-bottom:4px\"><span class=\"badge rejected\">✕ Rejected</span></div>",
      "<h3>" + escapeHtml(c.instrument) + "</h3>",
      "<span style=\"font-size:0.78rem;color:var(--muted)\">" + escapeHtml(c.style) + "</span>",
      "</div>",
      scoreBadge,
      "</div>",
      "<div class=\"trade-levels\">",
      "<div class=\"level-entry\"><span>Entry</span><strong>₹" + c.entry + "</strong></div>",
      "<div class=\"level-sl\"><span>Stop Loss</span><strong>₹" + c.stopLoss + "</strong></div>",
      "<div class=\"level-t1\"><span>Target 1</span><strong>₹" + targets[0] + "</strong></div>",
      "<div class=\"level-t2\"><span>Target 2</span><strong>₹" + targets[1] + "</strong></div>",
      "<div class=\"level-t3\"><span>Target 3</span><strong>₹" + targets[2] + "</strong></div>",
      "<div><span>Risk Reward</span><strong>1:" + c.rr + "</strong></div>",
      "</div>",
      "<div class=\"score-block\">" + scoreRows(item) + "</div>",
      "<div class=\"text-block\">",
      "<div class=\"risk-list\"><strong>Rejected Because</strong><ul>" +
        item.rejectionReasons.map(function (r) { return "<li>" + escapeHtml(r) + "</li>"; }).join("") +
      "</ul></div>",
      "</div>",
      "</article>"
    ].join("");
  }

  function renderSignals(scan) {
    const cards = scan.approved.map(renderApprovedCard).concat(scan.rejected.map(renderRejectedCard));
    els.signalsGrid.innerHTML = cards.join("");
    els.noTradeBanner.classList.toggle("hidden", !scan.noTrade);


    // Wire "Log Trade" buttons (fast-fill the journal form)
    els.signalsGrid.querySelectorAll(".log-trade-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        els.jInstrument.value = btn.dataset.instrument;
        els.jDirection.value  = btn.dataset.direction;
        els.jEntry.value      = btn.dataset.entry;
        els.jStopLoss.value   = btn.dataset.sl;
        els.jTarget1.value    = btn.dataset.t1;
        els.jTarget2.value    = btn.dataset.t2;
        els.jTarget3.value    = btn.dataset.t3;
        els.jScore.value      = btn.dataset.score;
        els.journalForm.classList.remove("hidden");
        document.getElementById("journal").scrollIntoView({ behavior: "smooth" });
      });
    });
  }

  // ── summary & risk ────────────────────────────────────────────────────────

  function normalizeApiScan(response) {
    return {
      settings:    response.settings,
      approved:    response.approved,
      rejected:    response.rejected,
      noTrade:     response.noTrade,
      generatedAt: new Date(response.generatedAt)
    };
  }

  function renderRisk(scan) {
    const s = scan.settings;
    const risk = data.riskState;
    const checks = [
      "Account risk per trade capped at " + s.riskPercent + "%, equal to Rs " +
        formatNumber(s.accountCapital * s.riskPercent / 100) + ".",
      "Rejecting trades below 1:2 risk reward.",
      "Rejecting options below " + formatNumber(s.minVolume) + " volume.",
      "Rejecting spreads above " + s.maxSpread + "%.",
      "Blocking high-severity economic events inside " + s.eventWindow + " minutes.",
      "Daily loss " + risk.dailyLossPct + "% | Weekly drawdown " +
        risk.weeklyDrawdownPct + "% | Monthly drawdown " + risk.monthlyDrawdownPct + "%."
    ];
    els.riskChecklist.innerHTML = "<strong>Active Checklist</strong><ul>" +
      checks.map(function (c) { return "<li>" + escapeHtml(c) + "</li>"; }).join("") + "</ul>";
  }

  function renderSummary(scan, market) {
    els.marketRegime.textContent  = market.regime;
    els.marketBias.textContent    = market.bias;
    els.approvedCount.textContent = scan.approved.length;
    els.rejectedCount.textContent = scan.rejected.length;
    els.capitalMode.textContent   = scan.noTrade ? "No Trade" : "Protected";
    els.riskState.textContent     = scan.noTrade
      ? "Preserving capital"
      : (scan.settings.riskPercent || 2) + "% account risk per trade";
    els.scanTimestamp.textContent = "Generated " + scan.generatedAt.toLocaleString("en-IN");
    flashMetrics();
  }

  function renderDailySummary(scan, market) {
    const approvedLine = scan.noTrade
      ? "No approved trades — scanner prioritises hard risk gates over signal quantity."
      : scan.approved.length + " high-conviction setup(s) passed all validation gates.";
    const eventNames = (market.eventCalendar || []).map(function (e) {
      return e.name + " in " + Math.round(e.minutesAway / 60) + "h";
    }).join(", ");

    els.dailySummary.innerHTML = [
      "<p><strong>Market condition:</strong> " + escapeHtml(market.regime) + ". " + escapeHtml(market.bias) + ".</p>",
      "<p><strong>Scanner result:</strong> "   + escapeHtml(approvedLine) + "</p>",
      "<p><strong>Volatility:</strong> India VIX is " + market.indiaVix +
        ". Directional trades require quick validation and strict stops.</p>",
      "<p><strong>News:</strong> " + escapeHtml((market.news || []).join(" ")) +
        (eventNames ? " Upcoming: " + escapeHtml(eventNames) + "." : "") + "</p>"
    ].join("");
  }

  // ── backtest ──────────────────────────────────────────────────────────────

  function _setBacktestError(msg) {
    const el = document.getElementById("backtestError");
    const txt = document.getElementById("backtestErrorText");
    if (el)  { el.classList.toggle("hidden", !msg); }
    if (txt && msg) txt.textContent = msg;
  }

  function renderBacktest(btData) {
    _setBacktestError(null); // clear any previous error
    const m = btData.metrics;
    els.winRate.textContent      = m.winRate + "%";
    els.profitFactor.textContent = m.profitFactor === null ? "∞ (perfect)" : m.profitFactor;
    els.maxDrawdown.textContent  = m.maxDrawdownPct + "%";
    els.sharpeRatio.textContent  = m.sharpeProxy;

    if (els.backtestSource) {
      els.backtestSource.textContent = "Live historical data (" + (m.totalTrades || 0) + " trades) · daily candle proxy";
    }

    const disc = document.getElementById("backtestDisclaimer");
    const discTxt = document.getElementById("backtestDisclaimerText");
    if (disc && discTxt && m.disclaimer) {
      discTxt.textContent = m.disclaimer;
      disc.classList.remove("hidden");
    }

    els.strategyRows.innerHTML = (btData.strategies || []).map(function (s) {
      return [
        "<tr>",
        "<td>" + escapeHtml(s.name)   + "</td>",
        "<td>" + s.trades             + "</td>",
        "<td>" + s.winRate            + "%</td>",
        "<td>1:" + s.avgRr            + "</td>",
        "<td>" + escapeHtml(s.status) + "</td>",
        "</tr>"
      ].join("");
    }).join("");
  }

  function showBacktestUnavailable(reason) {
    _setBacktestError(reason || "Backtest data unavailable.");
    ["winRate", "profitFactor", "maxDrawdown", "sharpeRatio"].forEach(function (id) {
      const el = document.getElementById(id);
      if (el) el.textContent = "—";
    });
    if (els.backtestSource) els.backtestSource.textContent = "Unavailable";
    if (els.strategyRows) {
      els.strategyRows.innerHTML =
        '<tr><td colspan="5" class="empty-row">No data — see error above.</td></tr>';
    }
  }

  function loadBacktest() {
    if (!apiAvailable) {
      showBacktestUnavailable("Backend offline — start the server to compute backtest.");
      return;
    }
    fetch("/api/backtest")
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || "Backtest unavailable"); });
        return r.json();
      })
      .then(renderBacktest)
      .catch(function (err) {
        showBacktestUnavailable(err.message || "Could not load backtest data.");
      });
  }

  // ── AI summary ────────────────────────────────────────────────────────────

  function loadAiSummary() {
    if (!apiAvailable) return;
    fetch("/api/summary")
      .then(function (r) { return r.json(); })
      .then(function (result) {
        if (result.summary) {
          els.dailySummary.innerHTML = "<p>" + escapeHtml(result.summary) + "</p>";
          if (els.summaryProvider) {
            els.summaryProvider.textContent =
              result.provider === "openai" ? "OpenAI" : "Rule-based";
          }
        }
      })
      .catch(function () {});
  }

  // ── journal analytics ─────────────────────────────────────────────────────

  function loadAnalytics() {
    if (!apiAvailable) return;
    fetch("/api/journal/analytics")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (els.aTotalTrades)  els.aTotalTrades.textContent  = d.totalTrades || 0;
        if (els.aWinRate)      els.aWinRate.textContent      = (d.winRate  || 0) + "%";
        if (els.aProfitFactor) els.aProfitFactor.textContent = d.profitFactor || "—";
        if (els.aAvgWin)       els.aAvgWin.textContent       = "+" + (d.avgWinR  || 0) + "R";
        if (els.aAvgLoss)      els.aAvgLoss.textContent      = (d.avgLossR || 0) + "R";
        if (els.aBest)         els.aBest.textContent         = (d.bestTrade  >= 0 ? "+" : "") + (d.bestTrade  || 0) + "R";
        if (els.aWorst)        els.aWorst.textContent        = (d.worstTrade || 0) + "R";
        if (els.aTotalR) {
          var r = d.totalR || 0;
          els.aTotalR.textContent = (r >= 0 ? "+" : "") + r + "R";
          els.aTotalR.style.color = r > 0 ? "var(--good)" : r < 0 ? "var(--danger)" : "";
        }
        // Paper vs live breakdown sub-labels
        var paper = d.paper || {}, live = d.live || {};
        var paperLabel = document.getElementById("aBreakdownPaper");
        var liveLabel  = document.getElementById("aBreakdownLive");
        if (paperLabel) paperLabel.textContent = "Paper: " + (paper.totalTrades || 0) + " trades, " + (paper.winRate || 0) + "% win";
        if (liveLabel)  liveLabel.textContent  = "Live: "  + (live.totalTrades  || 0) + " trades, " + (live.winRate  || 0) + "% win";
      })
      .catch(function () {});
  }

  // Pre-fill loss streak from scan response (journal-computed)
  function syncLossStreak(lossStreak) {
    if (lossStreak == null) return;
    var el = els.settings.lossStreak;
    if (el && +el.value < lossStreak) {
      el.value = lossStreak;
      el.style.borderColor = lossStreak >= 3 ? "var(--danger)" : "";
    }
  }

  // ── trade journal ─────────────────────────────────────────────────────────

  function renderJournalRows(items) {
    if (!items || items.length === 0) {
      els.journalRows.innerHTML = "<tr><td colspan=\"10\" class=\"empty-row\">No trades logged yet.</td></tr>";
      return;
    }
    els.journalRows.innerHTML = items.map(function (t) {
      return [
        "<tr>",
        "<td>" + fmtDate(t.created_at)       + "</td>",
        "<td>" + escapeHtml(t.instrument)    + "</td>",
        "<td>" + escapeHtml(t.direction)     + "</td>",
        "<td>" + t.entry                     + "</td>",
        "<td>" + t.stop_loss                 + "</td>",
        "<td>" + [t.target_1, t.target_2, t.target_3].filter(Boolean).join(" / ") + "</td>",
        "<td>" + t.confidence_score          + "</td>",
        "<td>" + escapeHtml(t.status || "paper") + "</td>",
        "<td>" + escapeHtml(t.outcome || "—")    + "</td>",
        "<td>" + (t.pnl_r != null ? t.pnl_r + "R" : "—") + "</td>",
        "</tr>"
      ].join("");
    }).join("");
  }

  function loadJournal() {
    if (!apiAvailable) return;
    fetch("/api/journal")
      .then(function (r) { return r.json(); })
      .then(function (result) { renderJournalRows(result.items || []); })
      .catch(function () {});
  }

  async function saveJournalEntry() {
    const entry = {
      instrument:      els.jInstrument.value.trim(),
      direction:       els.jDirection.value,
      entry:           +els.jEntry.value,
      stopLoss:        +els.jStopLoss.value,
      targets:         [+els.jTarget1.value || 0, +els.jTarget2.value || 0, +els.jTarget3.value || 0],
      confidenceScore: +els.jScore.value,
      status:          "paper",
      notes:           els.jNotes.value.trim()
    };
    if (!entry.instrument || !entry.entry || !entry.stopLoss) {
      alert("Instrument, entry, and stop loss are required.");
      return;
    }
    try {
      const resp = await fetch("/api/journal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entry)
      });
      if (!resp.ok) throw new Error("Save failed");
      els.journalForm.classList.add("hidden");
      // Clear form
      [els.jInstrument, els.jEntry, els.jStopLoss, els.jTarget1, els.jTarget2, els.jTarget3, els.jNotes].forEach(function (el) { el.value = ""; });
      els.jScore.value = "0";
      loadJournal();
      loadAnalytics();
    } catch (err) {
      alert("Could not save trade: " + err.message);
    }
  }

  // ── data source indicator ─────────────────────────────────────────────────

  function checkDataStatus() {
    if (!apiAvailable) {
      els.liveDataDot.style.background   = "#aaa";
      els.liveDataLabel.textContent      = "Manual execution only. No broker orders placed.";
      return;
    }
    fetch("/api/data-status")
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (s.liveDataAvailable) {
          const lastScan = s.lastScanAt
            ? " · Last scan " + new Date(s.lastScanAt).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit" })
            : "";
          if (s.marketOpen) {
            els.liveDataDot.classList.add("live");
            els.liveDataDot.style.background = "";
            els.liveDataLabel.textContent = "Live NSE data — VIX " + s.indiaVix + lastScan;
          } else {
            els.liveDataDot.classList.remove("live");
            els.liveDataDot.style.background = "#f5a623";
            els.liveDataLabel.textContent = "Market closed — VIX " + s.indiaVix + lastScan;
          }
        } else {
          els.liveDataDot.classList.remove("live");
          els.liveDataDot.style.background = "#e02020";
          els.liveDataLabel.textContent    = "NSE OFFLINE — do not trade";
        }
      })
      .catch(function () {
        els.liveDataLabel.textContent = "Data status unknown";
      });
  }

  // ── main render ───────────────────────────────────────────────────────────

  function renderAll(scan, market) {
    latestScan   = scan;
    latestMarket = market;
    renderSummary(scan, market);
    renderSignals(scan);
    renderRisk(scan);
    renderDailySummary(scan, market);
    loadAiSummary();
  }

  function showScanError(message) {
    const bar = els.scanBar;
    if (bar) {
      bar.textContent = "⚠ " + message;
      bar.className = "scan-bar scan-bar--error";
    }
    showToast(message, "error");
  }

  function clearScanError() {
    const bar = els.scanBar;
    if (bar) bar.className = "scan-bar hidden";
  }

  // ── Scan polling state ────────────────────────────────────────────────────
  let _scanPollTimer   = null;
  let _scanElapsedTimer = null;
  let _scanPollCount   = 0;
  let _scanStartedAt   = 0;
  const _POLL_INTERVAL_MS  = 5000;
  const _POLL_MAX_ATTEMPTS = 48;    // give up after 4 min (48 × 5s)

  function _stopPolling() {
    if (_scanPollTimer)    { clearInterval(_scanPollTimer);    _scanPollTimer    = null; }
    if (_scanElapsedTimer) { clearInterval(_scanElapsedTimer); _scanElapsedTimer = null; }
    _scanPollCount = 0;
  }

  function _startElapsedTimer() {
    _scanStartedAt = Date.now();
    _scanElapsedTimer = setInterval(function () {
      const secs = Math.floor((Date.now() - _scanStartedAt) / 1000);
      const bar  = els.scanBar;
      if (bar && !bar.classList.contains("hidden")) {
        bar.textContent = "Fetching live market data… " + secs + "s";
      }
      // Update button label with elapsed time
      const btn = els.scanButton;
      if (btn && btn.disabled) {
        btn.innerHTML = "<span class=\"scan-spinner\"></span>Scanning… " + secs + "s";
      }
    }, 1000);
  }

  function _handleScanPayload(payload) {
    if (payload.dataSource && payload.dataSource !== "live") {
      showScanError("Non-live data returned. Do NOT trade on this scan.");
      return false;
    }
    clearScanError();
    renderAll(normalizeApiScan(payload), payload.market);
    syncLossStreak(payload.lossStreak);
    loadAnalytics();
    const n = payload.approved.length;
    showToast(
      n > 0
        ? "Scan complete — " + n + " signal" + (n > 1 ? "s" : "") + " approved"
        : "Scan complete — no approved signals",
      n > 0 ? "success" : "info"
    );
    return true;
  }

  async function _pollScanResult() {
    _scanPollCount++;
    if (_scanPollCount > _POLL_MAX_ATTEMPTS) {
      _stopPolling();
      setScanLoading(false);
      showScanError("Scan is taking too long — NSE may be slow. Try again in 30s.");
      return;
    }
    try {
      const res = await fetch("/api/scan");
      if (!res.ok) return;   // still running or error — keep polling
      const payload = await res.json();
      // If scan still running (no market data yet), keep polling
      if (payload.scanStatus === "running" && !payload.market) return;
      // Got a result — stop polling and render
      _stopPolling();
      setScanLoading(false);
      _handleScanPayload(payload);
    } catch (_) {
      // Network blip — keep polling, don't show error yet
    }
  }

  async function runScan() {
    if (!apiAvailable) {
      showScanError("Backend server is offline. Start the server before scanning.");
      return;
    }
    _stopPolling();
    setScanLoading(true);
    clearScanError();
    try {
      const response = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingValues())
      });
      if (!response.ok) {
        const err = await response.json().catch(function () { return {}; });
        showScanError("Live scan failed — " + ((err.detail) || ("HTTP " + response.status)) + ". Do NOT trade without fresh data.");
        setScanLoading(false);
        return;
      }
      const payload = await response.json();
      // If scan result is already ready (cached or fast), render immediately
      if (payload.market) {
        setScanLoading(false);
        _handleScanPayload(payload);
        return;
      }
      // Scan is running in background — auto-poll every 5s, spinner stays on
      _scanPollTimer = setInterval(_pollScanResult, _POLL_INTERVAL_MS);
    } catch (_) {
      setScanLoading(false);
      showScanError("Server unreachable — check that the backend is running. Do NOT trade without live data.");
    }
  }

  async function detectApi() {
    try {
      const response = await fetch("/api/health");
      apiAvailable = response.ok;
    } catch (_) {
      apiAvailable = false;
    }
    checkDataStatus();
    loadBacktest();
    loadJournal();
    loadAnalytics();
    // Do NOT auto-scan on load — user must press Run Scan to get live data
    if (!apiAvailable) {
      showScanError("Backend offline — start the server then press Run Scan.");
    }
  }

  // ── event bindings ────────────────────────────────────────────────────────

  // Settings changes do not auto-scan — in live trading, scan is a deliberate action

  els.scanButton.addEventListener("click", runScan);

  els.telegramButton.addEventListener("click", function () {
    if (!latestScan) { runScan(); return; }
    if (apiAvailable) {
      fetch("/api/telegram/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingValues())
      })
        .then(function (r) { return r.json(); })
        .then(function (result) {
          els.telegramPreview.value = result.message || "";
          els.telegramDialog.showModal();
        })
        .catch(function () {
          els.telegramPreview.value = engine.telegramText(latestScan, latestMarket);
          els.telegramDialog.showModal();
        });
    } else {
      els.telegramPreview.value = engine.telegramText(latestScan, latestMarket);
      els.telegramDialog.showModal();
    }
  });

  els.sendTelegramButton.addEventListener("click", function () {
    const msg = els.telegramPreview.value;
    if (!msg || !apiAvailable) return;
    fetch("/api/telegram/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg })
    })
      .then(function (r) { return r.json(); })
      .then(function (result) {
        alert(result.sent ? "Sent to Telegram." : "Failed: " + (result.reason || "unknown"));
      })
      .catch(function () { alert("Telegram send failed."); });
  });

  els.closeDialog.addEventListener("click", function () {
    els.telegramDialog.close();
  });

  // Journal form
  els.logTradeButton.addEventListener("click", function () {
    els.journalForm.classList.toggle("hidden");
  });
  els.cancelJournalButton.addEventListener("click", function () {
    els.journalForm.classList.add("hidden");
  });
  els.saveJournalButton.addEventListener("click", saveJournalEntry);

  // Boot
  detectApi();
})();
