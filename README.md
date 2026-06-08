# AI Market Analyzer — NSE F&O Options Scanner

A live-data research platform for NSE F&O options trading.
Scans **14 liquid F&O instruments** in real time, applies 12 hard risk gates, scores each setup
across 6 categories using directional PCR + OI analysis, surfaces high-confidence
BUY (CE) and SELL (PE) signals, monitors open positions, and auto-logs paper trades.

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
The scanner fetches live NSE data for 14 instruments. Takes **25–60 seconds**.

> **The app never uses sample or fake data for scans.**
> If NSE is unreachable the scan fails with a clear error. Do not trade on a failed scan.

---

## Minimum Capital for F&O Trading

| Capital | What you can trade |
|---|---|
| ₹30,000 | Too small — can't fund even 1 lot at 2% risk for most F&O |
| ₹1,00,000 (default) | NIFTY (1 lot), BANKNIFTY (5 lots), liquid stocks |
| ₹2,00,000+ | Full scan universe — all 14 instruments |
| ₹5,00,000+ | Comfortable sizing across all instruments |

NSE F&O lot sizes range from 15 (BANKNIFTY, MARUTI) to 5,500+ (TATASTEEL).
At 2% risk per trade, position sizing is the binding constraint, not the premium.

---

## Setup (first time only)

### Requirements

| Requirement | Details |
|---|---|
| Python | 3.11 or higher |
| Internet | Live connection to nseindia.com and finance.yahoo.com |
| jugaad-data | Auto-installed via requirements.txt — bypasses NSE Akamai protection |
| Azure OpenAI | Optional — enables AI trade rationale (gpt-4.1-mini) |
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
```

### Step 4 — Configure environment variables

```powershell
copy .env.example .env
```

Fill in `.env`:

```env
# Azure OpenAI (optional — enables AI trade rationale)
AZURE_OPENAI_API_KEY=your_key
AZURE_OPENAI_ENDPOINT=https://your-resource.services.ai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini
AZURE_OPENAI_API_VERSION=2025-01-01-preview

# Telegram (optional — SL/target push alerts)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Scheduler — auto-scan at 9:20 AM and 3:20 PM IST
ENABLE_SCHEDULER=false
```

---

## How the Scanner Works

### Data sources

| Source | Used for | Method |
|---|---|---|
| NSE via jugaad-data | Option chains (all 14 symbols) | Bypasses Akamai bot protection |
| NSE allIndices API | India VIX, market breadth | Direct — no auth needed |
| NSE archives CSV | F&O lot sizes (live, quarterly) | Public file |
| yfinance + SQLite cache | 1-year daily OHLCV → EMA200, ADX, ATR | Cached — survives rate limits |
| NSE chart API | Real-time 1-min intraday closes | Falls back to daily on failure |

### Expiry schedule (NSE changed effective 2025-09-01)

| Instrument | Weekly | Monthly |
|---|---|---|
| NIFTY 50 | Every **Tuesday** | Last Tuesday |
| BANKNIFTY | No weekly | Last Tuesday |
| F&O stocks | No weekly | Last Tuesday |

### Scan universe (14 instruments, dynamic)

Default list (used when NSE index constituent endpoints are blocked):

```
NIFTY, BANKNIFTY,
RELIANCE, HDFCBANK, ICICIBANK, INFY, TCS,
AXISBANK, KOTAKBANK, SBIN, LT,
WIPRO, BHARTIARTL, HCLTECH
```

When NSE `equity-stockIndices` and OI-spurt endpoints are reachable, the universe
is built dynamically from index constituents ranked by traded value + OI build-up.
Refreshed every 30 minutes.

**Angel One SmartAPI integration** (pending account approval) will expand this to
the full 150+ F&O universe with a single fast API call.

### Scan flow

```
Phase 0 — Build universe
  Live: NSE index constituents (NIFTY50 / BANK / FIN / MIDCAP / IT / NEXT50)
        + OI-spurt symbols from NSE live-analysis endpoint
        + lot-size CSV filter
  Fallback: static 14-symbol list

Phase 1 — Serial option chain fetch (one at a time)
  jugaad-data NSELive() per symbol, fresh session each time
  15-second timeout per symbol — slow symbols skipped gracefully
  Results cached 60 seconds

Phase 2 — Parallel indicator computation (6 workers, reads cache only)
  Per symbol: 1-yr OHLCV → EMA200 / ADX / ATR / RelVol / PrevDayHigh
              Intraday 1-min → EMA20/50 / RSI / MACD / Supertrend / 15-min confluence
              Option chain → ATM strike, entry, OI%, PCR, IV, max pain, Greeks

Phase 3 — Scanner scoring (app/services/scanner.py)
  12 hard gates (all must pass)
  6-category score (must total >= 70/100)
  Position sizing (lots = floor(rupeeRisk / lotRisk))

Phase 4 — AI enrichment (Azure OpenAI)
  News + context → 1-2 sentence trade rationale per approved signal
```

---

## Signal Quality System

### Hard Gates — 12 rules, never relaxed

| Gate | Threshold |
|---|---|
| Loss streak | < 3 consecutive losses |
| Daily loss | < 3% of account capital |
| Weekly drawdown | < 8% |
| Monthly drawdown | < 15% |
| Risk/Reward | ≥ 1:2 |
| EMA trend alignment | EMA20 > EMA50 > EMA200 (BUY) or reverse (SELL) |
| Option volume | ≥ 25,000 contracts (filters illiquid monthly strikes) |
| Bid-ask spread | ≤ 1.5% (configurable) |
| Event risk | No high-severity event within 60 min |
| India VIX | < 22 |
| Time gate (open) | Outside 9:15–9:30 IST (price discovery window) |
| Time gate (close) | Outside 14:45–15:30 IST |
| Expiry day | No new weekly long entries after 11:00 IST on Tuesday |

### Scoring — 6 categories, minimum 70/100

| Category | Max | Key factors |
|---|---|---|
| Trend | 25 | EMA20/50/200 alignment, Supertrend, PDH/PDL breakout, 15-min EMA9/21 confluence |
| Momentum | 20 | RSI zone (55–70 BUY / 30–45 SELL), MACD crossover direction, ADX strength |
| Volume | 15 | Relative volume vs 20-day avg, option contract volume |
| Option Chain | 20 | OI build-up direction, directional PCR, max pain distance, spread, IV level, IV Rank |
| Sentiment | 10 | India VIX level, market breadth (advance/decline ratio) |
| Risk/Reward | 10 | RR ≥ 2.0 → 6pts, ≥ 2.5 → 8pts, ≥ 3.0 → 10pts |

### Directional PCR scoring (key improvement)

PCR (Put-Call Ratio) is now scored based on trade direction:

- **BUY (CE)**: PCR > 1.2 = institutions writing puts = bullish = +6pts
- **SELL (PE)**: PCR < 0.7 = institutions writing calls = bearish = +6pts
- Contra-direction PCR gets a penalty (-2pts)

### VIX-adjusted stop loss

Stop distance automatically widens with VIX to match real market noise:

| VIX | ATR multiplier | Min premium % |
|---|---|---|
| < 15 | 0.35× | 15% |
| 15–18 | 0.45× | 18% |
| 18–20 | 0.55× | 22% |
| > 20 | 0.65× | 26% |

This means high-VIX days naturally produce fewer signals — wider SL → worse RR → most setups fail the ≥ 1:2 gate automatically.

### IV Rank

IV Rank is a percentile of current ATM IV vs 52-week stored history.

- **< 20** (green): historically cheap IV — ideal for option buyers
- **20–55**: normal range
- **> 75** (red): historically expensive — IV crush risk for buyers

IV Rank requires 20+ trading days of stored readings to be reliable.
Readings are stored automatically on every scan.

### 15-Minute Confluence

1-min intraday closes are resampled to 15-min candles (no extra API call).
EMA9 vs EMA21 on 15-min determines short-term direction.
When 15-min confirms the daily EMA trend: +3 to trend score.

---

## Understanding Signal Cards

| Element | Meaning |
|---|---|
| Green card (BUY/SELL) | Passed all 12 hard gates AND scored ≥ 70/100 |
| **BUY** | Bullish — buy a CE (Call Option) |
| **SELL** | Bearish — buy a PE (Put Option) |
| Score | Sum of 6 category scores |
| Valid Until | Time by which price must hold entry — do not enter after this |
| Lots | Maximum lots within your 2% account risk |
| Strike badge | ITM / ATM / OTM — each candidate generates all 3 |
| Delta / Theta / Vega | Black-Scholes Greeks at the selected strike |
| IV Rank | Green = cheap IV, Red = expensive |
| 15m badge | ✓ = 15-min EMA confirms daily direction |
| DTE | Days to expiry |
| Red card (collapsed) | Rejected — click to see which gate(s) failed |
| No Trade Mode | Zero signals passed — preserve capital |

---

## Live Trading Workflow

1. **Before 9:15 IST**: Start server with `.\start.ps1`
2. **9:20–9:30 IST**: Wait for price discovery — time gate blocks scans in this window
3. **9:30 IST onwards**: Click **Run Scan** (or enable `ENABLE_SCHEDULER=true` for auto)
4. **Review signals**: Check score, IV Rank, 15m badge, DTE, Valid Until
5. **Execute manually** at your broker — scanner does NOT place orders
6. **Update journal**: Set paper trade → open once executed
7. **Monitor Telegram**: SL/T1/T2/T3 alerts fire automatically
8. **T1 hit**: Trail stop to entry manually in your broker
9. **T2/T3**: Auto-closed in journal — verify with broker
10. **End of day**: Review analytics — win rate, P&L (R), equity curve

---

## Background Services

### Price Monitor (60 seconds, market hours only)

Checks open/paper journal entries against live NSE option prices.

| Event | Action |
|---|---|
| T1 hit | Telegram: "T1 HIT — trail stop to entry" |
| T2 hit | Auto-close as win, Telegram alert |
| T3 hit | Auto-close as win, Telegram alert |
| SL hit | Auto-close as loss, Telegram alert |

### NSE Session Watchdog (15 minutes, market hours)

Validates session by fetching India VIX.
Forces reconnect if dead. Sends Telegram alert if session stays dead after reconnect.

### Telegram Retry Queue (15 seconds, always on)

All Telegram messages are queued in SQLite. Failed sends retry 3 times.
Critical SL alerts are never lost to a momentary network issue.

---

## Project Structure

```
├── app/
│   ├── main.py               FastAPI routes, scan orchestration, startup validation
│   ├── config.py             Settings from .env
│   ├── data_sources/
│   │   ├── nse.py            NSE live data — jugaad-data option chains, dynamic universe,
│   │   │                     _nearest_expiry, per-symbol timeout, yfinance OHLCV cache,
│   │   │                     intraday closes, Black-Scholes Greeks, event calendar
│   │   └── news.py           NewsAPI headlines (15-min cache)
│   └── services/
│       ├── scanner.py        12 hard gates, 6-category scoring (directional PCR/OI),
│       │                     position sizing, VIX-adjusted SL, expiry day gate (Tuesday)
│       ├── storage.py        SQLite — scan audit, journal, IV history, OHLCV cache, alerts
│       ├── monitor.py        Price monitor (60s), NSE session watchdog (15min)
│       ├── ai.py             Azure OpenAI trade rationale
│       ├── backtest.py       Historical win-rate / profit-factor stats
│       ├── telegram.py       Alert formatting, retry queue
│       └── scheduler.py      APScheduler — 9:20 AM and 3:20 PM auto-scans
├── src/
│   ├── app.js                Browser UI — rendering and event handling
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
| GET | `/api/health` | Server health, AI/Telegram status |
| GET | `/api/data-status` | NSE connectivity, VIX, last scan time |
| POST | `/api/scan` | Live scan with custom settings |
| GET | `/api/scan` | Live scan with defaults |
| GET | `/api/backtest` | Historical backtest metrics |
| GET | `/api/summary` | AI market summary (uses scan cache) |
| GET | `/api/audit/recent` | Last 10 scan records |
| POST | `/api/journal` | Log a trade |
| GET | `/api/journal` | List journal entries |
| PATCH | `/api/journal/{id}` | Update exit / outcome / P&L |
| GET | `/api/journal/analytics` | Win rate, profit factor, equity curve |
| GET | `/api/journal/export` | Download all trades as CSV |
| POST | `/api/telegram/send` | Send Telegram message |
| POST | `/api/telegram/preview` | Preview Telegram message |

---

## Common Issues

**0 approved signals (No Trade Mode)**

Expand the red cards to see which gate failed. Most common causes:

| Cause | Fix |
|---|---|
| Capital too low | Set Account Capital ≥ ₹1 lakh in the UI |
| Market closed | Scan after 9:30 IST when option volumes are live |
| EMA misalignment | Wait for clearer trend — mixed market is a valid no-trade |
| VIX ≥ 22 | Hard gate — cannot override. Reduce exposure. |
| Expiry day after 11 IST | Weekly options on Tuesday after 11am are blocked |

**"Position size would exceed account risk"**

The most common issue at low capital:
- At ₹30K capital with 2% risk: budget = ₹600
- NIFTY 1 lot risk ≈ ₹3,000 — needs ₹1.5L capital minimum
- Solution: increase Account Capital in the Risk Controls panel

**Option volume below threshold**

- During market hours (9:15–15:30): volume builds — usually clears 25K by 10am
- After close (15:30+): volumes frozen at day-end, monthly equity options often < 25K
- NSE weekly NIFTY options always have millions in volume on expiry day

**NSE data unavailable (startup warning)**

NSE uses Akamai bot protection. jugaad-data library handles this automatically.
If it fails, restart the server — it re-acquires cookies on each startup.

**Azure OpenAI 404 / ResourceNotFound**

Your endpoint URL is the Project URL (services.ai.azure.com).
Change to the OpenAI URL format: `https://your-resource.services.ai.azure.com` is correct
if using the latest API format — verify the deployment name matches exactly.

**IV Rank shows blank**

Normal for the first 3–4 weeks. 20+ daily readings per symbol needed.
Stored automatically on every scan.

---

## SQLite Database

| Table | Purpose |
|---|---|
| `scan_audit` | Full JSON of every scan run |
| `trade_journal` | All logged trades — paper and live |
| `iv_history` | Daily ATM IV per symbol (builds IV Rank over time) |
| `daily_ohlcv` | 1-year OHLCV cache per symbol (survives yfinance rate limits) |
| `pending_alerts` | Telegram retry queue |

Location: `data/research.db` (auto-created on first start).

---

## Pending Improvements

| Feature | Status | Impact |
|---|---|---|
| Angel One SmartAPI | Pending account approval | Full 150+ F&O universe, no scraping |
| Dynamic universe (live) | Partially working | NSE index constituent endpoints often blocked |
| VWAP intraday signal | Not yet implemented | +5–8 pts to score on confirmation |
| Bollinger Band squeeze | Not yet implemented | Better entry timing |
| Real-time WebSocket prices | Requires broker API | Eliminates 60s option chain lag |
