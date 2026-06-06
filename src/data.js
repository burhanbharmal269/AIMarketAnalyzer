window.RESEARCH_DATA = {
  market: {
    timestamp: "2026-06-06T09:36:00+05:30",
    regime: "Trending with controlled volatility",
    bias: "Bullish index structure with selective participation",
    indiaVix: 14.2,
    breadth: 1.38,
    globalSentiment: "Neutral-positive",
    eventCalendar: [
      {
        name: "RBI Monetary Policy",
        severity: "high",
        minutesAway: 1620
      },
      {
        name: "US CPI Release",
        severity: "high",
        minutesAway: 780
      }
    ],
    news: [
      "Banking and financial services lead early participation.",
      "IT stocks remain mixed ahead of US data.",
      "India VIX is stable, supporting directional option trades with strict stops."
    ]
  },
  riskState: {
    dailyLossPct: 0.4,
    weeklyDrawdownPct: 1.6,
    monthlyDrawdownPct: 3.2
  },
  candidates: [
    {
      id: "bnf-ce-62000",
      instrument: "BANKNIFTY 62000 CE",
      underlying: "BANKNIFTY",
      direction: "BUY",
      style: "Intraday momentum continuation",
      expiry: "Weekly",
      signalValidMinutes: 45,
      entry: 125,
      stopLoss: 105,
      targets: [145, 165, 190],
      lotSize: 15,
      optionVolume: 118000,
      bid: 123.8,
      ask: 125.2,
      spreadPct: 1.12,
      priceAction: "Higher high breakout above opening range",
      ema20: 61820,
      ema50: 61570,
      ema200: 60940,
      rsi: 64,
      macd: 18.4,
      macdSignal: 10.2,
      adx: 24,
      relativeVolume: 1.45,
      oiChangePct: 8.5,
      pcr: 1.08,
      maxPainDistancePct: 0.8,
      marketSentiment: 7,
      rr: 2.6,
      eventRisk: false,
      notes: [
        "Call OI unwinding near resistance supports upside continuation.",
        "Trend aligns across 5m, 15m and 1h views."
      ]
    },
    {
      id: "nifty-pe-23300",
      instrument: "NIFTY 23300 PE",
      underlying: "NIFTY",
      direction: "BUY",
      style: "Counter-trend breakdown attempt",
      expiry: "Weekly",
      signalValidMinutes: 30,
      entry: 86,
      stopLoss: 75,
      targets: [98, 108, 120],
      lotSize: 75,
      optionVolume: 74000,
      bid: 84,
      ask: 86.7,
      spreadPct: 3.09,
      priceAction: "Price rejected lower level but remains above EMA50",
      ema20: 23410,
      ema50: 23372,
      ema200: 23260,
      rsi: 48,
      macd: -1.8,
      macdSignal: 2.6,
      adx: 16,
      relativeVolume: 0.94,
      oiChangePct: 4.2,
      pcr: 1.18,
      maxPainDistancePct: 0.6,
      marketSentiment: 5,
      rr: 1.91,
      eventRisk: false,
      notes: [
        "Counter-trend puts are weak while index remains above key EMAs."
      ]
    },
    {
      id: "finnifty-ce-24100",
      instrument: "FINNIFTY 24100 CE",
      underlying: "FINNIFTY",
      direction: "BUY",
      style: "Breakout continuation",
      expiry: "Weekly",
      signalValidMinutes: 40,
      entry: 92,
      stopLoss: 76,
      targets: [108, 124, 144],
      lotSize: 40,
      optionVolume: 16400,
      bid: 90.3,
      ask: 93.6,
      spreadPct: 3.57,
      priceAction: "Breakout candle strong, but option liquidity is below threshold",
      ema20: 24040,
      ema50: 23880,
      ema200: 23520,
      rsi: 61,
      macd: 8.2,
      macdSignal: 4.1,
      adx: 23,
      relativeVolume: 1.45,
      oiChangePct: 9.7,
      pcr: 1.01,
      maxPainDistancePct: 1.1,
      marketSentiment: 7,
      rr: 2.75,
      eventRisk: false,
      notes: [
        "Underlying setup is acceptable, option market quality is not."
      ]
    },
    {
      id: "reliance-ce-3000",
      instrument: "RELIANCE 3000 CE",
      underlying: "RELIANCE",
      direction: "BUY",
      style: "Stock option swing attempt",
      expiry: "Monthly",
      signalValidMinutes: 90,
      entry: 47,
      stopLoss: 39,
      targets: [55, 63, 72],
      lotSize: 250,
      optionVolume: 28600,
      bid: 45.8,
      ask: 47.9,
      spreadPct: 4.44,
      priceAction: "Stock is approaching resistance with mixed sector participation",
      ema20: 2954,
      ema50: 2940,
      ema200: 2860,
      rsi: 57,
      macd: 4.6,
      macdSignal: 4.2,
      adx: 18,
      relativeVolume: 1.08,
      oiChangePct: 3.6,
      pcr: 0.94,
      maxPainDistancePct: 0.9,
      marketSentiment: 6,
      rr: 3.12,
      eventRisk: true,
      notes: [
        "Stock options need stricter spread and event filters than index options."
      ]
    }
  ],
  backtest: {
    totalTrades: 126,
    wins: 72,
    losses: 54,
    grossProfitR: 154.8,
    grossLossR: 69.6,
    maxDrawdownPct: 6.9,
    sharpeProxy: 1.34,
    strategies: [
      {
        name: "Index trend continuation",
        trades: 54,
        winRate: 63,
        avgRr: 2.42,
        status: "Promising"
      },
      {
        name: "Opening range breakout",
        trades: 38,
        winRate: 55,
        avgRr: 2.15,
        status: "Needs filter tuning"
      },
      {
        name: "F&O stock momentum",
        trades: 34,
        winRate: 47,
        avgRr: 2.35,
        status: "Paper trade only"
      }
    ]
  }
};
