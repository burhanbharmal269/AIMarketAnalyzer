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
    jTarget2:           document.getElementById("jTarget2"),
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
      els.scanButton.innerHTML = "<span class=\"scan-spinner\"></span>Scanning…";
      if (els.scanBar) els.scanBar.classList.remove("hidden");
    } else {
      els.scanButton.classList.remove("loading");
      els.scanButton.textContent = "Run Scan";
      if (els.scanBar) els.scanBar.classList.add("hidden");
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
    const labels = {
      trend: "Trend", momentum: "Momentum", volume: "Volume",
      optionChain: "Option Chain", sentiment: "Sentiment", riskReward: "Risk Reward"
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

  function renderApprovedCard(item) {
    const c = item.candidate;
    const logBtn = apiAvailable
      ? "<button class=\"log-trade-btn\" data-instrument=\"" + escapeHtml(c.instrument) +
        "\" data-direction=\"" + escapeHtml(c.direction) +
        "\" data-entry=\""    + c.entry +
        "\" data-sl=\""       + c.stopLoss +
        "\" data-t2=\""       + (c.targets[1] || 0) +
        "\" data-score=\""    + item.score.total +
        "\">Log Trade</button>"
      : "";

    return [
      "<article class=\"signal-card\">",
      "<div class=\"signal-head\">",
      "<div><h3>" + escapeHtml(c.instrument) + "</h3><span>" + escapeHtml(c.style) + "</span></div>",
      "<div style=\"display:flex;gap:8px;align-items:center\">",
      "<span class=\"badge buy\">" + escapeHtml(c.direction) + " | Confidence " + item.score.total + "/100</span>",
      logBtn,
      "</div>",
      "</div>",
      "<div class=\"trade-levels\">",
      "<div><span>Entry</span><strong>"          + c.entry          + "</strong></div>",
      "<div><span>Stop Loss</span><strong>"      + c.stopLoss       + "</strong></div>",
      "<div><span>Target 1</span><strong>"       + c.targets[0]     + "</strong></div>",
      "<div><span>Target 2</span><strong>"       + c.targets[1]     + "</strong></div>",
      "<div><span>Target 3</span><strong>"       + c.targets[2]     + "</strong></div>",
      "<div><span>Risk Reward</span><strong>1:"  + c.rr             + "</strong></div>",
      "<div><span>Confidence</span><strong>"     + item.score.total + "/100</strong></div>",
      "<div><span>Valid Until</span><strong>"    + item.validUntil  + "</strong></div>",
      "<div><span>Position Size</span><strong>"  + item.sizing.lots + " lot(s)</strong></div>",
      "</div>",
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
    return [
      "<article class=\"signal-card\">",
      "<div class=\"signal-head\">",
      "<div><h3>" + escapeHtml(c.instrument) + "</h3><span>" + escapeHtml(c.style) + "</span></div>",
      "<span class=\"badge rejected\">Rejected | " + item.score.total + "/100</span>",
      "</div>",
      "<div class=\"trade-levels\">",
      "<div><span>Entry</span><strong>"  + c.entry      + "</strong></div>",
      "<div><span>SL</span><strong>"     + c.stopLoss   + "</strong></div>",
      "<div><span>RR</span><strong>1:"   + c.rr         + "</strong></div>",
      "<div><span>Spread</span><strong>" + c.spreadPct  + "%</strong></div>",
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
        els.jTarget2.value    = btn.dataset.t2;
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

  function renderBacktest(btData) {
    const m = btData.metrics;
    els.winRate.textContent      = m.winRate + "%";
    els.profitFactor.textContent = m.profitFactor;
    els.maxDrawdown.textContent  = m.maxDrawdownPct + "%";
    els.sharpeRatio.textContent  = m.sharpeProxy;

    const src = m.dataSource === "live"
      ? "Live historical data (" + (m.totalTrades || 0) + " trades)"
      : "Sample data — connect NSE for live metrics";
    if (els.backtestSource) els.backtestSource.textContent = src;

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

  function loadBacktest() {
    if (!apiAvailable) {
      const m = engine.backtestMetrics(data.backtest);
      renderBacktest({ metrics: { ...m, dataSource: "sample" }, strategies: data.backtest.strategies });
      return;
    }
    fetch("/api/backtest")
      .then(function (r) { return r.json(); })
      .then(renderBacktest)
      .catch(function () {
        const m = engine.backtestMetrics(data.backtest);
        renderBacktest({ metrics: { ...m, dataSource: "sample" }, strategies: data.backtest.strategies });
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
        "<td>" + t.target_2                  + "</td>",
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
      targets:         [0, +els.jTarget2.value, 0],
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
      [els.jInstrument, els.jEntry, els.jStopLoss, els.jTarget2, els.jNotes].forEach(function (el) { el.value = ""; });
      els.jScore.value = "0";
      loadJournal();
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
          els.liveDataDot.classList.add("live");
          els.liveDataDot.style.background = "";
          els.liveDataLabel.textContent    = "Live NSE data — VIX " + s.indiaVix;
        } else {
          els.liveDataDot.classList.remove("live");
          els.liveDataDot.style.background = "#e67e22";
          els.liveDataLabel.textContent    = "Sample data (NSE offline)";
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

  function runLocalScan() {
    setScanLoading(true);
    setTimeout(function () {
      latestScan = engine.runScan(data, settingValues());
      renderAll(latestScan, data.market);
      setScanLoading(false);
      const n = latestScan.approved.length;
      showToast(n > 0 ? "Scan complete — " + n + " signal" + (n > 1 ? "s" : "") + " approved" : "Scan complete — no approved signals", n > 0 ? "success" : "info");
    }, 50);
  }

  async function runScan() {
    if (!apiAvailable) { runLocalScan(); return; }
    setScanLoading(true);
    try {
      const response = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingValues())
      });
      if (!response.ok) throw new Error("API error");
      const payload = await response.json();
      renderAll(normalizeApiScan(payload), payload.market);
      const n = payload.approved.length;
      showToast(n > 0 ? "Scan complete — " + n + " signal" + (n > 1 ? "s" : "") + " approved" : "Scan complete — no approved signals", n > 0 ? "success" : "info");
    } catch (_) {
      apiAvailable = false;
      showToast("Live scan failed — using local data", "error");
      runLocalScan();
      return;
    } finally {
      setScanLoading(false);
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
    runScan();
  }

  // ── event bindings ────────────────────────────────────────────────────────

  Object.keys(els.settings).forEach(function (key) {
    els.settings[key].addEventListener("input", runScan);
  });

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
