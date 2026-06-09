# AI Market Analyzer — NSE F&O Options Scanner

A live-data research platform for NSE F&O options trading.
Scans **40 liquid F&O instruments** in real time using **Angel One SmartAPI** as the primary data source,
applies hard risk gates, scores each setup across 7 categories using a 3-timeframe model,
surfaces high-confidence BUY (CE) and SELL (PE) signals, monitors open positions,
auto-trails stops at T1, and accumulates signal accuracy data over time.

**Personal use only. Manual execution. No automatic order placement.**

---

## Quick Start

### 1. Start the server

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or with auto-restart on crash:

```powershell
.\start.ps1
```

### 2. Open the dashboard

```
http://localhost:8000
```

### 3. Set your capital and click Run Scan

Set **Account Capital** to your actual trading capital (minimum ₹1 lakh recommended for F&O).
The scanner fetches live data for 40 instruments via Angel One. Takes **30–60 seconds**.

> **The app never uses sample or fake data for scans.**
> If data is unavailable the scan fails with a clear error. Do not trade on a failed scan.

---

## Minimum Capital for F&O Trading

| Capital | What you can trade |
|---|---|
| ₹30,000 | Too small — can't fund even 1 lot at 2% risk for most F&O |
| ₹1,00,000 (default) | NIFTY (1 lot), BANKNIFTY (5 lots), liquid stocks |
| ₹2,00,000+ | Full scan universe — all 40 instruments |
| ₹5,00,000+ | Comfortable sizing across all instruments |

NSE F&O lot sizes range from 15 (BANKNIFTY, MARUTI) to 5,500+ (TATASTEEL).
At 2% risk per trade, position sizing is the binding constraint, not the premium.

---

## Setup (first time only)

### Requirements

| Requirement | Details |
|---|---|
| Python | 3.11 or higher |
| Internet | Live connection to Angel One SmartAPI + nseindia.com |
| Angel One SmartAPI | Primary data source — option chains, intraday candles, daily OHLCV |
| Azure OpenAI | Optional — enables AI trade rationale and regime classification |
| Telegram | Optional — real-time SL/target alerts |

### Step 1 — Clone / open the project

```powershell
cd d:\AIMarketAnalyzer
```

### Step 2 — Create virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### Step 3 — Install dependencies

```powershell
pip install -r requirements.txt
pip install tzdata   # required for Asia/Kolkata timezone on Windows
```

### Step 4 — Configure environment variables

```powershell
copy .env.example .env
```

Fill in `.env`:

```env
# Angel One SmartAPI (primary data source — required for full functionality)
ANGEL_API_KEY=your_app_key
ANGEL_CLIENT_ID=your_client_id
ANGEL_PIN=your_4digit_mpin
ANGEL_TOTP_SECRET=your_totp_secret_from_app

# Azure OpenAI (optional — enables AI regime classification + trade rationale)
AZURE_OPENAI_API_KEY=your_key
AZURE_OPENAI_ENDPOINT=https://your-resource.services.ai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_API_VERSION=2025-01-01-preview

# Standard OpenAI fallback (used when Azure fields are not set)
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini

# Telegram (optional — SL/target push alerts)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Scheduler — auto-scan at market open, 11:15, 13:05, 14:15, EOD
ENABLE_SCHEDULER=false
```

---

## How the Scanner Works

### Data Sources (priority order)

| Source | Used for | Notes |
|---|---|---|
| **Angel One SmartAPI** | Option chains, 5-min intraday candles, daily OHLCV, live LTP | Primary — fast, no Akamai blocking |
| NSE scraping (jugaad-data) | Option chain fallback when Angel One unavailable | Slower, Akamai-protected |
| NSE allIndices API | India VIX, market breadth | Always available |
| NSE archives CSV | F&O lot sizes (live, quarterly update) | Public file |
| yfinance + SQLite | Daily OHLCV fallback when Angel One unavailable | Rate-limited, cached |
| Azure / OpenAI | AI regime classification, news sentiment, trade rationale | Optional |

Angel One is the **single API for all real-time data** — option chains, intraday candles (5-min),
and historical daily OHLCV all come from `getCandleData` / `optionGreek`.
A global rate limiter (`_throttle()`) ensures the 3 req/sec limit is never violated even
during parallel 40-symbol scans.

### Angel One Session Management

- **Startup login** with 3-retry TOTP regeneration (handles 30-second TOTP window)
- **24-hour JWT** automatically refreshed 1 hour before expiry by a background keepalive thread
- **Session status** visible at `GET /api/health` → `angelOne.connected`

### Expiry Schedule (NSE changed effective 2025-09-01)

| Instrument | Weekly | Monthly |
|---|---|---|
| NIFTY 50 | Every **Tuesday** | Last Tuesday |
| BANKNIFTY | No weekly | Last Tuesday |
| F&O stocks | No weekly | Last Tuesday |

### Scan Universe (40 instruments)

```
Indices:   NIFTY, BANKNIFTY
Large-cap: RELIANCE, HDFCBANK, ICICIBANK, INFY, TCS, AXISBANK, KOTAKBANK,
           SBIN, LT, WIPRO, BHARTIARTL, HCLTECH, BAJFINANCE, BAJAJFINSV
Mid/large: MARUTI, SUNPHARMA, TECHM, TITAN, ASIANPAINT, HINDUNILVR,
           ULTRACEMCO, NESTLEIND, POWERGRID, NTPC, ONGC, M&M, ADANIPORTS,
           JSWSTEEL, TATASTEEL, HINDALCO, GRASIM, DRREDDY, CIPLA,
           DIVISLAB, INDUSINDBK, HDFCLIFE, EICHERMOT, APOLLOHOSP, TATACONSUM
```

When Angel One is unavailable the scanner falls back to a dynamic NSE-constituent
universe (top stocks by traded value + OI spurts), capped at 12 symbols.

### Scan Flow

```
Phase 0 — Build universe
  Angel One available : full 40-symbol list (hardcoded, fastest path)
  Fallback            : NSE index constituents + OI spurts, or static 14-symbol list

Phase 1 — Parallel option chain pre-fetch (8 workers)
  Angel One: optionGreek() per symbol, global 0.35s rate limiter
  Fallback:  jugaad-data NSELive() serial, 15s timeout per symbol
  Results cached 60 seconds

Phase 1.5 — NIFTY master direction
  Compute NIFTY EMA20 vs EMA200 → BUY or SELL
  Suppress all stock candidates trading against NIFTY direction

Phase 2 — Parallel indicator + VWAP computation (8 workers)
  Per symbol:
    Angel One getCandleData(FIVE_MINUTE) → 5-min candles for today
    Resample to 10-min (research), 15-min (primary signals), 30-min (trend gate)
    Daily OHLCV (Angel One ONE_DAY or yfinance cache) → EMA50/200, ADX, ATR

Phase 3 — AI pre-scan
  Market regime classification (aiAction: trade_full / trade_reduced / selective / avoid)
  News sentiment batch (one OpenAI call covers all 40 underlyings)

Phase 4 — Scoring + hard gates
  Hard gates: all must pass (binary — one failure = rejected)
  7-category score: must total ≥ 70/100 (normalised from 110-point raw scale)
  Position sizing: lots = floor(2% capital / lot risk)

Phase 5 — AI enrichment
  Per approved signal: 1–2 sentence trade rationale
```

---

## 3-Timeframe Signal Model

All intraday signals are derived from a **single** Angel One 5-min candle fetch,
resampled inside pandas — no extra API calls.

| Timeframe | Bars/session | Used for | Why |
|---|---|---|---|
| 5-min | ~78 | VWAP, spot price, gap detection, intraday volume | Most accurate VWAP (78 bars vs 25 on 15-min) |
| 15-min | ~25 | **Primary signals**: RSI(14), MACD(12,26,9), EMA9/21 | RSI lookback = 210 min (stable); ~3× fewer MACD false signals vs 5-min |
| 30-min | ~12 | Macro trend gate: EMA5 vs EMA10 | Prevents entries that fight the 2-hour trend cycle |
| Daily | 200+ | EMA50/200, ADX, ATR, Supertrend, S/R levels, RelVol | Slow indicators that need months of data |

**3-TF confluence bonus**: when 15-min + 30-min + daily all align → +1 extra score point.

---

## Signal Quality System

### Hard Gates — all must pass

| Gate | Threshold |
|---|---|
| Loss streak | < 3 consecutive losses |
| Daily loss | < 3% of account capital |
| Weekly drawdown | < 8% |
| Monthly drawdown | < 15% |
| Risk/Reward | ≥ 1:1.5 (S/R-anchored targets — see below) |
| EMA trend alignment | EMA20 > EMA50 > EMA200 (BUY) or reverse (SELL) |
| Option volume | ≥ 25,000 contracts |
| Bid-ask spread | ≤ 1.5% (configurable) |
| Event risk | No high-severity event within 60 min |
| India VIX | < 22 |
| Time gate (open) | Outside 9:15–9:30 IST (price discovery window) |
| Time gate (close) | Outside 14:45–15:30 IST |
| Expiry day | No new weekly long entries after 11:00 IST on Tuesday |
| AI regime gate | Blocked when AI classifies market as `avoid` |

### Scoring — 7 categories, normalised to 100

| Category | Normalised Max | Key factors |
|---|---|---|
| Trend | 27 | EMA alignment, Supertrend, PDH/PDL breakout, VWAP confirmation, 15-min + 30-min TF confluence, S/R multi-touch breakout, gap direction |
| Momentum | 18 | RSI zone (55–70 BUY / 30–45 SELL), MACD direction, ADX strength |
| Volume | 14 | Relative volume (U-shape corrected), option contract volume |
| Option Chain | 18 | OI build-up, directional PCR (nearest expiry only), max pain distance, spread, IV level, IV Rank |
| Sentiment | 9 | India VIX level, market breadth (A/D ratio), AI regime classification |
| Risk/Reward | 9 | RR ≥ 2.5→10pts, ≥2.0→8pts, ≥1.5→5pts, ≥1.0→2pts |
| News | 5 | AI-classified sentiment aligned with trade direction |

Raw scores sum to 110 max. Normalised to 100 before comparison against the 70 threshold.

### S/R-Anchored Targets

Targets are grounded in actual price structure — not arbitrary multiples:

1. **S/R detection**: swing highs/lows on ±5-bar window over last 60 daily bars
2. **Multi-touch quality filter**: levels tested 2+ times (within 0.5%) preferred over single-touch — institutional memory levels only
3. **Delta conversion**: `T1_option = spot_distance_to_SR × |delta|` — translates the spot move to expected option premium move
4. **T1** = first S/R level (clamped 1:1 to 3:1 RR), **T2** = 1.8× T1 distance, **T3** = 2.8× T1 distance
5. RR is now genuinely variable (1.0–3.0+) based on where structure actually is

### Volume Model (U-Shape Corrected)

NSE F&O volume follows a pronounced U-shape: heavy at open (discovery, gap-fills),
thin at midday, heavy at close (institutional rebalancing).

A linear projection at 11 AM would assume only 32% of daily volume has elapsed;
the real figure is ~43%. This caused false-low RelVol readings in morning and false-high
at midday. The U-shape corrected model uses an empirical cumulative profile so
a normal-volume day always reads RelVol ≈ 1.0 regardless of when the scan runs.

### VIX-Adjusted Stop Loss

Stop distance automatically widens with VIX to match real market noise:

| VIX | ATR multiplier | Min premium % |
|---|---|---|
| < 15 | 0.35× | 15% |
| 15–18 | 0.45× | 18% |
| 18–20 | 0.55× | 22% |
| > 20 | 0.65× | 26% |

High-VIX days naturally produce fewer signals — wider SL → worse RR → most setups fail the ≥ 1:1.5 gate.

### IV Rank

IV Rank is a percentile of current ATM IV vs all stored daily readings for that symbol.

- **< 20** (green): historically cheap — ideal for option buyers
- **20–55**: normal range
- **> 75** (red): historically expensive — IV crush risk

Requires 20+ days of stored readings. Stored automatically on every scan.

### NIFTY Master Direction Filter

NIFTY EMA20 vs EMA200 is computed at the start of every scan.
All stock candidates that trade **against** the NIFTY direction are suppressed
before scoring runs — stocks fighting the macro tape are never approved.
Index signals (NIFTY, BANKNIFTY) are always evaluated independently.

---

## Understanding Signal Cards

| Element | Meaning |
|---|---|
| Green card | Passed all hard gates AND scored ≥ 70/100 |
| **BUY** | Bullish — buy a CE (Call Option) |
| **SELL** | Bearish — buy a PE (Put Option) |
| Score | 7-category normalised score out of 100 |
| Valid Until | Time by which price must hold entry — do not enter after this |
| Lots | Maximum lots within your configured account risk |
| Strike badge | ITM / ATM / OTM — all 3 candidates generated per symbol |
| Delta / Theta / Vega | Black-Scholes Greeks at the selected strike |
| IV Rank | Green = cheap IV, Red = expensive |
| 15m / 30m badges | ✓ = that timeframe EMA confirms daily direction |
| Touch count | Number of times S/R level was tested — higher = stronger level |
| RR | Risk/Reward to T1 (variable, S/R-anchored) |
| DTE | Days to expiry |
| Red card (collapsed) | Rejected — click to see which gate(s) failed and score breakdown |
| No Trade Mode | Zero signals passed — preserve capital |

---

## Live Trading Workflow

1. **Before 9:15 IST**: Start server with `.\start.ps1`. Angel One logs in automatically.
2. **9:20–9:30 IST**: Time gate blocks new entries — wait for price discovery
3. **9:30 IST onwards**: Click **Run Scan** (or enable `ENABLE_SCHEDULER=true` for auto-scans at 10:05, 11:15, 13:05, 14:15 IST)
4. **Review signals**: Check score, IV Rank, TF badges, DTE, Valid Until, touch count on S/R levels
5. **Execute manually** at your broker — scanner does NOT place orders
6. **Journal**: Paper trades auto-logged. One open position per underlying at a time.
7. **Monitor Telegram**: SL/T1/T2/T3 alerts fire automatically every 60 seconds during market hours
8. **T1 hit**: Stop is **automatically trailed to entry (breakeven)** in the journal and signal_log
9. **T2/T3 hit**: Auto-closed in journal as win — verify and close at your broker
10. **16:00 IST**: Daily OHLCV cache refreshed automatically — next scan uses complete EOD candle
11. **End of day**: Review signal analytics — win rate by score bucket, VIX regime, data source

---

## Background Services

### Angel One Session Keepalive

Background thread checks session age every 30 minutes.
Re-logins 1 hour before the 24-hour JWT expires — scans never hit a dead session.

### Price Monitor (60 seconds, market hours only)

Checks open/paper journal entries against live Angel One option prices.

| Event | Action |
|---|---|
| T1 hit | Trail stop to entry price (breakeven) in journal + signal_log. Telegram alert. |
| T2 hit | Auto-close as win in journal + signal_log. Telegram alert. |
| T3 hit | Auto-close as win in journal + signal_log. Telegram alert. |
| SL hit | Auto-close as loss. Telegram alert. |

### NSE Session Watchdog (15 minutes, market hours)

Validates NSE session by fetching India VIX.
Forces reconnect if dead. Sends Telegram alert if session stays dead after reconnect.

### Telegram Retry Queue (15 seconds, always on)

All Telegram messages are queued in SQLite. Failed sends retry 3 times.
Critical SL alerts are never lost to a momentary network issue.

### Daily OHLCV Cache Refresh (16:05 IST)

Deletes today's cached daily OHLCV rows so the next morning's first scan
re-fetches the complete EOD candle from Angel One. Prevents indicators
from running on partial-day data the following morning.

---

## Project Structure

```
├── app/
│   ├── main.py               FastAPI routes, scan orchestration, startup validation
│   ├── config.py             Settings from .env (Angel One, OpenAI, Telegram)
│   ├── data_sources/
│   │   ├── angel.py          Angel One SmartAPI — session, option chains, intraday candles,
│   │   │                     daily OHLCV, live LTP, batch LTP, global rate limiter
│   │   ├── nse.py            NSE data — breadth, VIX, lot sizes, dynamic universe,
│   │   │                     3-TF indicator model, U-shape volume, multi-touch S/R,
│   │   │                     candidate builder, option chain parsing, event calendar
│   │   └── news.py           NewsAPI headlines (15-min cache)
│   └── services/
│       ├── scanner.py        Hard gates, 7-category scoring, position sizing,
│       │                     VIX-adjusted SL, expiry day gate, score normalisation
│       ├── storage.py        SQLite — scan audit, journal, signal_log, IV history,
│       │                     OHLCV cache, alerts, deduplication, scan_id linkage
│       ├── monitor.py        Price monitor (60s), T1 trail stop, NSE session watchdog
│       ├── ai.py             Azure / OpenAI regime classification, news sentiment, rationale
│       ├── backtest.py       Historical win-rate / profit-factor stats
│       ├── telegram.py       Alert formatting, retry queue
│       └── scheduler.py      APScheduler — market-hours scans + 16:05 OHLCV refresh
├── src/
│   ├── app.js                Browser UI — signal cards, score bars, journal, analytics
│   └── engine.js             Client-side scoring mirror
├── logs/
│   └── app.log               Rotating log (10 MB × 5 files)
├── data/
│   └── research.db           SQLite database (auto-created)
├── index.html                Dashboard
├── styles.css                UI styles
├── start.ps1                 Auto-restart launcher
├── requirements.txt          Python dependencies
└── .env.example              Environment template
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Dashboard |
| GET | `/api/health` | Server health, Angel One session status, AI/Telegram |
| GET | `/api/data-status` | NSE connectivity, VIX, last scan time |
| POST | `/api/scan` | Live scan with custom settings |
| GET | `/api/scan` | Live scan with defaults |
| GET | `/api/option-ltp` | Real-time quote for a specific option via Angel One |
| GET | `/api/backtest` | Historical backtest metrics |
| GET | `/api/summary` | AI market summary (uses 15-min scan cache) |
| GET | `/api/audit/recent` | Last 10 scan records |
| POST | `/api/journal` | Log a trade manually |
| GET | `/api/journal` | List journal entries |
| PATCH | `/api/journal/{id}` | Update exit / outcome / P&L |
| GET | `/api/journal/analytics` | Win rate, profit factor, equity curve |
| GET | `/api/journal/export` | Download all trades as CSV |
| GET | `/api/signals` | Recent signal_log rows (outcome filter: win/loss) |
| GET | `/api/signals/analytics` | Accuracy by score bucket, VIX regime, data source, flags |
| GET | `/api/signals/export` | Download full signal_log as CSV |
| POST | `/api/telegram/send` | Send Telegram message |
| POST | `/api/telegram/preview` | Preview Telegram message |
| POST | `/api/admin/ohlcv-refresh` | Manually clear today's daily OHLCV cache |

---

## SQLite Database

| Table | Purpose |
|---|---|
| `scan_audit` | Full JSON of every scan run (pruned after 30 days) |
| `trade_journal` | All logged trades — paper and live, with outcome tracking |
| `signal_log` | 59-field per-signal record for accuracy accumulation over time |
| `iv_history` | Daily ATM IV per symbol (builds IV Rank over time) |
| `daily_ohlcv` | Historical OHLCV cache per symbol (Angel One primary, yfinance fallback) |
| `pending_alerts` | Telegram retry queue |

Location: `data/research.db` (auto-created on first start).

### Signal Log Analytics

After 30+ closed signals, `GET /api/signals/analytics` breaks win rate and avg R by:

- Score bucket (70–74, 75–79, 80–84, 85–89, 90+)
- VIX regime (<14, 14–18, 18–22, 22+)
- Data source (angel-15min vs daily-fallback)
- VWAP confirmed, S/R breakout, 15-min aligned, PDH/PDL breakout
- Expiry type (Weekly vs Monthly) and strike type (ITM/ATM/OTM)
- Exit reason (sl_hit, t1_hit, t2_hit, t3_hit)

---

## Common Issues

**0 approved signals (No Trade Mode)**

Expand the red cards to see which gate failed. Most common causes:

| Cause | Fix |
|---|---|
| Capital too low | Set Account Capital ≥ ₹1 lakh in the UI |
| Market closed | Scan after 9:30 IST when option volumes are live |
| AI regime = avoid | AI classified market as untradeable (e.g. sharp decline with IV spike) |
| EMA misalignment | Wait for clearer trend — mixed market is a valid no-trade |
| VIX ≥ 22 | Hard gate — cannot override. Reduce exposure. |
| Expiry day after 11 IST | Weekly options on Tuesday after 11am are blocked |

**"Position size would exceed account risk"**

The most common issue at low capital:
- At ₹30K capital with 2% risk: budget = ₹600
- NIFTY 1 lot risk ≈ ₹3,000 — needs ₹1.5L capital minimum
- Solution: increase Account Capital in the Risk Controls panel

**Angel One login fails at startup**

- Check all 4 env vars: `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET`
- TOTP secret must be the base32 string from the Angel One app (not the 6-digit code)
- The login retries 3 times with a fresh TOTP code each attempt to handle clock skew

**Option volume below threshold**

- During market hours (9:15–15:30): volume builds — usually clears 25K by 10am
- After close: volumes frozen at day-end, monthly equity options often < 25K
- NIFTY weekly options always have high volume during market hours

**IV Rank shows blank**

Normal for the first 3–4 weeks. Requires 20+ daily readings per symbol.
Stored automatically on every scan.

**Score seems lower than expected**

Scores are normalised to 100 (raw max is 110). A raw score of 77/110 = 70/100.
The 70/100 displayed threshold is post-normalisation — the gate is correctly calibrated.
