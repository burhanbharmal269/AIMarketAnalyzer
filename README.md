# Indian Options Research Desk

A live-data-only research platform for NSE F&O options trading.
Scans 40 liquid instruments in parallel, applies 12 hard risk gates, scores each setup
across 6 categories, surfaces high-confidence BUY (CE) and SELL (PE) signals,
monitors open positions in real time, and auto-logs paper trades for analytics.

**Personal use only. Manual execution. No broker integration.**

---

## Quick Start

### 1. Start the server (recommended — auto-restarts on crash)

```powershell
.\start.ps1
```

Or directly without auto-restart:

```powershell
py -m uvicorn app.main:app --port 8000
```

### 2. Open the dashboard

```
http://localhost:8000
```

### 3. Click Run Scan

The scanner fetches live NSE data for 40 instruments in parallel.
Takes **10–20 seconds**. A progress bar appears while scanning.

> **The app never uses sample or fake data for scans.**
> If NSE is unreachable, the scan fails with a red error banner.
> Do not trade without a successful live scan.

---

## Setup (first time only)

### Requirements

| Requirement | Details |
|---|---|
| Python | 3.11 or higher — use `py` launcher on Windows |
| Internet | Live connection to nseindia.com and finance.yahoo.com |
| AI analysis | Azure OpenAI key (optional but recommended) |
| News headlines | NewsAPI.org free key — 100 req/day (optional) |
| Telegram alerts | Bot token + Chat ID (optional) |

### Step 1 — Clone / open the project folder

```powershell
cd "C:\Users\bharm\Documents\AI Stock Market Tool"
```

### Step 2 — Create virtual environment

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

You should see `(.venv)` in your prompt.

### Step 3 — Install dependencies

```powershell
pip install -r requirements.txt
```

### Step 4 — Configure environment variables

```powershell
copy .env.example .env
```

Open `.env` and fill in:

```env
# NewsAPI — free key at https://newsapi.org/register
NEWS_API_KEY=your_key_here

# Azure OpenAI — Azure AI Foundry → Resource → Keys and Endpoint
AZURE_OPENAI_API_KEY=your_azure_key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini
AZURE_OPENAI_API_VERSION=2025-01-01-preview

# Standard OpenAI (only if Azure fields above are blank)
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini

# Telegram alerts (optional but strongly recommended for live use)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Auto-scan at 9:20 AM and 3:20 PM IST
ENABLE_SCHEDULER=false
```

> **Azure endpoint**: Use the **OpenAI** URL (`*.openai.azure.com`), not the Project URL (`*.services.ai.azure.com`).
> In Azure AI Foundry: Resource → Keys and Endpoint → copy the "OpenAI" URL.

---

## Starting the Server

### Recommended: `start.ps1` (auto-restart)

```powershell
.\start.ps1
```

- Shows a startup banner with the dashboard URL
- Activates `.venv` automatically if present
- Restarts the server automatically if it crashes
- Shows timestamp and restart count on each boot
- Press **Ctrl+C** to stop

### Alternative: direct uvicorn

```powershell
py -m uvicorn app.main:app --port 8000 --log-level info
```

### Logs

All server output is written to `logs/app.log` with automatic rotation (10 MB per file, 5 files kept).
Check this file when diagnosing issues.

On startup you will see clear validation lines:

```
STARTUP OK   — NSE reachable, VIX 14.23
STARTUP OK   — Database accessible
STARTUP OK   — Telegram configured
STARTUP WARN — Azure OpenAI not configured (AI explanations disabled)
```

---

## How to Use

### Running a scan

1. Open **http://localhost:8000**
2. Set **Account Capital** in the Risk Controls section
3. Set **Risk %** (default 2% — do not exceed 3% for live trading)
4. Click **Run Scan**

> **Loss streak is auto-computed from your journal.** You do not need to set it manually.
> The system reads your recent trade outcomes and applies the stricter of journal-computed vs manual.

### Understanding signal cards

| Element | Meaning |
|---|---|
| Green card (BUY/SELL badge) | Passed all 12 hard gates, scored ≥ 72/100 |
| **BUY** | Bullish setup — buy a CE (Call Option) |
| **SELL** | Bearish setup — buy a PE (Put Option) |
| Confidence score | Sum of 6 category scores out of 100 |
| Valid Until | Time by which price must hold — do not enter after this |
| Lots | Maximum position size within your account risk |
| Delta / Theta / Vega | Black-Scholes Greeks at ATM strike |
| IV Rank badge | Green = cheap IV (good for buyers), Red = expensive |
| 15m badge | ✓ = 15-min EMA confirms daily trend, ✗ = no confirmation |
| DTE | Days to expiry |
| Red card (collapsed) | Rejected — click to see which gate(s) failed |
| No Trade Mode banner | Zero signals passed — preserve capital |

### Auto paper trade logging

When a scan produces approved signals, **they are automatically logged as paper trades**
in the journal. You do not need to click "Log Trade" unless you want to add notes or
log a trade that was not from the scanner.

### Logging a manual trade

Click **Log Trade** on any approved signal card to pre-fill the journal form.
Fill in Entry, Stop Loss, T1/T2/T3, then click **Save Trade**.

### Updating a trade outcome

After closing a position, click on the trade in the journal table and update:
- **Exit Price**
- **Outcome** (win / loss)
- **P&L (R)** — e.g. 1.0 means you made 1R profit

The risk state (daily/weekly/monthly drawdown) is computed from these real outcomes.

### Exporting trades

Click **Export CSV** in the journal section header to download all trades as a CSV file.
Filename: `journal_YYYYMMDD_HHMM.csv`.

---

## Background Services

Three daemon threads run automatically after server startup:

### Price Monitor (every 60 seconds, market hours only)

Checks all open/paper journal entries against live NSE option prices.

| Event | Action |
|---|---|
| T1 hit | Telegram alert: "T1 HIT — Trail stop to entry" |
| T2 hit | Auto-close entry as **win**, Telegram alert |
| T3 hit | Auto-close entry as **win**, Telegram alert |
| SL hit | Auto-close entry as **loss**, Telegram alert |

Market hours: 09:15–15:30 IST only.

### NSE Session Watchdog (every 15 minutes, market hours only)

Validates the NSE HTTP session by fetching India VIX.
If dead: forces a session reset and attempts reconnection.
Sends a Telegram alert if the session stays dead after reconnection attempt.
This ensures SL alerts do not silently stop firing during the trading day.

### Telegram Retry Queue (every 15 seconds, always on)

All Telegram messages are queued in SQLite before sending.
If a send fails (network glitch), the system retries up to **3 times**:
- Attempt 2: 30 seconds later
- Attempt 3: 120 seconds later
- After 3 failures: marked as failed and logged

Critical SL alerts are never lost to a momentary network issue.

---

## Signal Quality System

### Hard Gates — 12 rules, NEVER relaxed

All 12 must pass. Failing even one rejects the signal completely.

| Gate | Threshold |
|---|---|
| Loss streak | < 3 consecutive losses |
| Daily loss | < 3% of account capital |
| Weekly drawdown | < 8% |
| Monthly drawdown | < 15% |
| Risk/Reward | ≥ 1:2 |
| EMA trend alignment | EMA20 > EMA50 > EMA200 (BUY) or reverse (SELL) |
| Option volume | ≥ 20,000 |
| Bid-ask spread | ≤ 3% |
| Event risk | No high-severity event within 120 min |
| India VIX | < 22 |
| Time gate (open) | Outside 09:15–09:30 IST |
| Time gate (close) | Outside 14:45–15:30 IST |

### Scoring — 6 categories, minimum 72/100

| Category | Max | Key factors |
|---|---|---|
| Trend | 25 | EMA alignment, Supertrend, PDH/PDL breakout, 15-min confluence |
| Momentum | 20 | RSI zone, MACD crossover, ADX strength |
| Volume | 15 | Relative volume, option volume |
| Option Chain | 20 | OI change%, PCR, max pain, spread, IV level, IV Rank |
| Sentiment | 10 | Market breadth, VIX level |
| Risk/Reward | 10 | RR ≥ 2.0 → 6pts, ≥ 2.5 → 8pts, ≥ 3.0 → 10pts |

### IV Rank

IV Rank is a 0–100 percentile of current ATM IV vs stored history.
- **< 20** (green): historically cheap IV — ideal for option buyers
- **20–65** (neutral): normal range
- **> 65** (red): historically expensive — premium cost erodes edge

> IV Rank requires **20+ trading days** of stored history to be reliable.
> It shows as blank on the first few weeks of use.

### 15-Minute Confluence

The 1-min intraday data is resampled to 15-min candles (no extra API call).
EMA9 vs EMA21 on 15-min determines short-term momentum direction.
When 15-min confirms the daily EMA trend: `+3` to trend score.
Shown as **15m ✓** (green) or **15m ✗** (red) on each signal card.

---

## Scan Flow

```
USER CLICKS "Run Scan"
        │
        ▼
┌─────────────────────────────────────┐
│  Browser  (src/app.js)              │
│  1. Disable button, show spinner    │
│  2. POST /api/scan  { settings }    │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI  (app/main.py)  ──  build_scan()                       │
│                                                                  │
│  A. Read risk state from journal  (compute_risk_state)          │
│     - Daily / weekly / monthly drawdown %                       │
│     - Auto-computed consecutive loss streak                     │
│     - Loss streak = max(journal, manual input)                  │
│                                                                  │
│  B. Fetch live NSE data  ─── fails? ──▶  HTTP 503 + Telegram    │
│     No sample data. No silent fallback. Scan stops.             │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  NSEDataSource.get_live_candidates()  (app/data_sources/nse.py) │
│                                                                  │
│  1. India VIX  (NSE /api/allIndices)                            │
│  2. F&O lot sizes  (NSE archives CSV)                           │
│  3. Top-40 liquid F&O symbols  (NSE equity-stockIndices)        │
│                                                                  │
│  4. For each symbol — PARALLEL  (6 workers):                    │
│     a) yfinance → 200-day OHLCV                                 │
│        EMA200, ADX, ATR, RelVol, PrevDayHigh/Low               │
│     b) NSE chart API → real-time 1-min closes                   │
│        EMA20/50, RSI, MACD, Supertrend                          │
│        Resample → 15-min EMA9/21 confluence                     │
│     c) NSE option chain → nearest expiry                        │
│        ATM strike, entry, bid/ask, OI%, PCR, ATM IV, volume     │
│        Max pain (two-pass), DTE, Greeks (Black-Scholes)         │
│     d) Build candidate dict                                     │
│        Store IV reading → compute IV Rank percentile           │
│        Reject: entry < ₹1, mixed EMA, zero-DTE after 14:00     │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  scan_market()  (app/services/scanner.py)                       │
│                                                                  │
│  1. Hard gate check  (12 gates — all must pass)                 │
│  2. Score  (6 categories → must total ≥ 72)                     │
│  3. Position sizing  (lots = floor(rupeeRisk / lotRisk))        │
│  4. Sort approved by score DESC, cap at 5                       │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  AI enrichment  (app/services/ai.py)  — if key configured       │
│  NewsAPI headlines → Azure OpenAI → 1-2 sentence rationale      │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Record scan to SQLite  (scan_audit table)                      │
│  Cache result for 15 min  (used by /api/summary)                │
│  Auto-log approved signals as paper trades  (trade_journal)     │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Browser renders result                                         │
│  - Approved signal cards with Greeks, IV Rank, 15m badge       │
│  - Rejected cards (collapsed, with gate failure reasons)        │
│  - Market regime, VIX, breadth, drawdown panel                  │
│  - Loss streak auto-synced from journal response                │
│  - Analytics bar refreshed                                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
├── app/
│   ├── main.py               FastAPI routes, scan orchestration, log rotation
│   ├── config.py             Settings from .env
│   ├── sample_data.py        Backtest fallback only (never used for live scans)
│   ├── data_sources/
│   │   ├── nse.py            NSE live data, parallel fetch, option chain, event calendar
│   │   └── news.py           NewsAPI headlines with 15-min cache
│   └── services/
│       ├── scanner.py        Hard gates, 6-category scoring, position sizing
│       ├── storage.py        SQLite — scan audit, journal, risk state, IV history, alert queue
│       ├── monitor.py        Price monitor (60s), NSE watchdog (15min)
│       ├── ai.py             Azure/OpenAI trade rationale generation
│       ├── backtest.py       Historical win-rate / profit-factor stats
│       ├── telegram.py       Alert formatting, send with retry queue
│       └── scheduler.py      APScheduler — 9:20 AM and 3:20 PM auto-scans
├── src/
│   ├── app.js                Browser UI — rendering and event handling
│   ├── engine.js             Client-side scoring mirror (synced with Python)
│   └── data.js               Static sample data (UI development only)
├── logs/
│   └── app.log               Rotating server log (10 MB × 5 files, auto-created)
├── data/
│   └── research.db           SQLite database (auto-created on first run)
├── index.html                Dashboard
├── styles.css                UI styles
├── start.ps1                 Auto-restart launcher (recommended for live use)
├── requirements.txt          Python dependencies
└── .env.example              Environment variable template
```

---

## SQLite Database Tables

| Table | Purpose |
|---|---|
| `scan_audit` | Full JSON of every scan run |
| `trade_journal` | All logged trades — paper and live |
| `iv_history` | Daily ATM IV per symbol (builds IV Rank over time) |
| `pending_alerts` | Telegram retry queue |

Database location: `data/research.db` (auto-created on first start).

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Dashboard HTML |
| GET | `/api/health` | Server health + AI/Telegram status |
| GET | `/api/data-status` | NSE connectivity, VIX, last scan time |
| POST | `/api/scan` | Run live scan with settings |
| GET | `/api/scan` | Run live scan with defaults |
| GET | `/api/backtest` | Historical backtest metrics |
| GET | `/api/summary` | AI market summary (uses cached scan) |
| GET | `/api/audit/recent` | Last 10 scan records |
| POST | `/api/journal` | Log a trade |
| GET | `/api/journal` | List journal entries |
| PATCH | `/api/journal/{id}` | Update exit price / outcome / P&L |
| GET | `/api/journal/analytics` | Win rate, profit factor, equity curve |
| GET | `/api/journal/export` | Download all trades as CSV |
| POST | `/api/telegram/send` | Send Telegram message |
| POST | `/api/telegram/preview` | Preview Telegram message |

---

## Common Issues

**Red error banner: "Live scan failed"**
NSE session expired or server is unreachable. Check `logs/app.log`.
The NSE watchdog attempts reconnection every 15 minutes automatically.
You can also restart the server — it reconnects immediately on startup.

**`STARTUP WARN — NSE unreachable`**
NSE blocks requests outside market hours or when cookies expire.
During market hours (9:00–15:30 IST) scans should work reliably.
Outside market hours, NSE returns 403 for option chain endpoints.

**Azure OpenAI 404 / ResourceNotFound**
Your `AZURE_OPENAI_ENDPOINT` is the Project endpoint (`services.ai.azure.com`).
Change it to the OpenAI endpoint: `your-resource.openai.azure.com`

**All signals rejected — No Trade Mode**
Expand the red cards to see which gate failed. Most common:
- India VIX ≥ 22 — market too volatile, cannot override
- Time in opening chop window (9:15–9:30 IST)
- Time in pre-close window (14:45–15:30 IST)
- No clean EMA alignment across the watchlist today

**IV Rank shows blank**
Normal for first 3–4 weeks. IV Rank requires 20+ daily readings per symbol.
Readings are stored automatically on every scan.

**Loss streak field shows higher than expected**
Correct behaviour — the system reads your actual recent trade outcomes from the journal
and uses the stricter number. Review your recent closed trades in the journal.

**`py` command not found**
Install Python from python.org and check "Add to PATH". Verify with `py --version`.

**Database permission errors**
Move the project folder out of `C:\Program Files` or any UAC-protected location.
The database is created in `data/research.db` relative to the project root.

---

## Live Trading Workflow

1. **Morning (before 9:15 IST)**: Start the server with `.\start.ps1`
2. **9:20 IST**: Click **Run Scan** (or enable `ENABLE_SCHEDULER=true` for auto-scan)
3. **Review approved signals**: Check score, Greeks, IV Rank, 15m badge, Valid Until
4. **Execute manually**: Enter position through your broker at the signal's entry price
5. **Update journal**: Change paper trade status to "open" once executed
6. **Monitor alerts**: Telegram notifications fire at T1/T2/T3 and SL
7. **T1 hit**: Trail stop to entry price manually in your broker
8. **T2/T3 hit**: Position auto-closed in journal — verify broker execution
9. **SL hit**: Exit immediately — journal auto-closes as loss
10. **End of day (3:20 IST)**: Review analytics dashboard — win rate, P&L (R), equity curve
