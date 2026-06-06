# Indian Options Research Desk

An AI-assisted real-time research platform for NSE F&O (Futures & Options) trading.
Scans 40 liquid F&O instruments in parallel, applies hard risk gates, scores each setup
across 6 categories, and surfaces only high-confidence BUY signals.

---

## Run Scan — Full Flow Diagram

```
USER CLICKS "Run Scan"
        │
        ▼
┌─────────────────────────────────────┐
│  Browser  (src/app.js)              │
│  1. Disable button, show spinner    │
│  2. Read settings from UI controls  │
│     - Account Capital               │
│     - Risk % (default 2%)           │
│     - Max Spread, Min Volume, etc.  │
│  3. POST /api/scan  { settings }    │
└──────────────┬──────────────────────┘
               │  HTTP POST /api/scan
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI  (app/main.py)  ──  build_scan()                       │
│                                                                  │
│  A. Compute real risk state from journal  (compute_risk_state)  │
│     - Reads closed trades from SQLite                           │
│     - Calculates daily / weekly / monthly drawdown %            │
│                                                                  │
│  B. Try live NSE data  ─── fails? ──▶  use sample_data()        │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  NSEDataSource.get_live_candidates()  (app/data_sources/nse.py) │
│                                                                  │
│  1. Fetch India VIX  (NSE /api/allIndices)                       │
│  2. Fetch live F&O lot sizes  (NSE archives CSV)                 │
│  3. Fetch top-40 liquid F&O symbols  (NSE equity-stockIndices)  │
│                                                                  │
│  4. For each symbol — runs in PARALLEL (6 workers):             │
│     ┌──────────────────────────────────────────────────┐        │
│     │  _fetch_candidate(symbol)                         │        │
│     │                                                   │        │
│     │  a) yfinance  →  200-day daily OHLCV              │        │
│     │     Computes: EMA200, ADX, ATR, RelVol            │        │
│     │               PrevDayHigh / PrevDayLow            │        │
│     │                                                   │        │
│     │  b) NSE chart API  →  real-time 1-min closes      │        │
│     │     Computes: EMA20, EMA50, RSI, MACD, spot price │        │
│     │                                                   │        │
│     │  c) Supertrend (period=7, multiplier=3)           │        │
│     │     from daily data; fallback = EMA20 vs EMA200   │        │
│     │                                                   │        │
│     │  d) NSE option chain  →  nearest expiry           │        │
│     │     ATM strike, entry price, bid/ask spread       │        │
│     │     OI change %, PCR, ATM IV, option volume       │        │
│     │     Max pain strike (two-pass algorithm)          │        │
│     │                                                   │        │
│     │  e) Build candidate                               │        │
│     │     CE if EMA bullish, PE if EMA bearish          │        │
│     │     ATR-based stop / T1 / T2 / T3                 │        │
│     │     PDH/PDL breakout flag                         │        │
│     │     Reject if entry < ₹1 or mixed EMA trend       │        │
│     └──────────────────────────────────────────────────┘        │
│                                                                  │
│  Returns: list of candidate dicts  (typically 10–25 pass build) │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  scan_market()  (app/services/scanner.py)                        │
│                                                                  │
│  For every candidate:                                            │
│                                                                  │
│  STEP 1 — Hard Gate Checks  (12 gates — NEVER relaxed)          │
│  ──────────────────────────────────────────────────────         │
│   ✗  Loss streak >= 3  consecutive losses                        │
│   ✗  Daily loss % >= 3%  (of account capital)                    │
│   ✗  Weekly drawdown % >= 8%                                     │
│   ✗  Monthly drawdown % >= 15%                                   │
│   ✗  Risk Reward < 1:2                                           │
│   ✗  EMA trend not aligned with trade direction                  │
│   ✗  Option volume < 20,000                                      │
│   ✗  Bid-ask spread > 3%                                         │
│   ✗  High-severity event within 120 min                          │
│   ✗  India VIX >= 22                                             │
│   ✗  Time 09:15–09:30 IST  (opening chop window)                │
│   ✗  Time 14:45–15:30 IST  (closing volatility window)          │
│                                                                  │
│  STEP 2 — Scoring  (fixed floor = 72/100, never lowered)        │
│  ────────────────────────────────────────────────────────       │
│  Trend        /25   EMA align + Supertrend + PDH/PDL breakout   │
│  Momentum     /20   RSI zone + MACD crossover + ADX strength    │
│  Volume       /15   Relative volume + option volume             │
│  Option Chain /20   OI change % + PCR + max pain + spread + IV  │
│  Sentiment    /10   Market breadth + VIX level                  │
│  Risk/Reward  /10   RR>=2 (6pts), >=2.5 (8pts), >=3 (10pts)     │
│                                                                  │
│  STEP 3 — Position Sizing                                        │
│  ────────────────────────                                        │
│  Rupee risk  = accountCapital × riskPercent%                     │
│  Lot risk    = |entry − stopLoss| × lotSize                      │
│  Lots        = floor(rupeeRisk / lotRisk)                        │
│  Rejected if lots < 1                                            │
│                                                                  │
│  STEP 4 — Sort & Cap                                             │
│  ──────────────────                                              │
│  Sort approved by score DESC, take top 5                         │
│                                                                  │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  AI Enrichment  (app/services/ai.py)  — only if key configured  │
│                                                                  │
│  For each approved signal:                                       │
│  1. NewsAPI → latest 3 headlines for the underlying symbol      │
│  2. Azure OpenAI / OpenAI → 1–2 sentence trade rationale        │
│     (prompt includes Supertrend, PDH breakout, ATM IV)          │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Record scan to SQLite  (scan_audit table)                       │
│  Return JSON response to browser                                 │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Browser  (src/app.js)  — renders result                         │
│                                                                  │
│  1. Hide spinner, re-enable button, flash metric panels          │
│  2. Render approved signal cards  (green)                        │
│     - Entry / Stop Loss / T1 / T2 / T3                           │
│     - Confidence score bar per category                          │
│     - Position size (lots + max rupee risk)                      │
│     - Valid-until time                                           │
│     - AI rationale + latest news headlines                       │
│  3. Render rejected cards  (collapsed, show rejection reasons)  │
│  4. Update market regime, VIX, breadth, drawdown panel          │
│  5. Show "No Trade" banner if zero signals passed               │
│  6. Toast notification: "X signals found" or "No trade mode"    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Requirements

| Requirement | Details |
|---|---|
| Python | 3.11 or higher — use the `py` launcher on Windows |
| Internet | Live connection to nseindia.com and finance.yahoo.com |
| AI analysis | Azure OpenAI **or** standard OpenAI key (optional but recommended) |
| News headlines | NewsAPI.org free key — 100 requests/day (optional) |
| Telegram alerts | Bot token + Chat ID (optional) |

---

## Setup

### Step 1 — Open the project folder

```
cd "C:\Users\bharm\Documents\AI Stock Market Tool"
```

### Step 2 — Create a virtual environment

```
py -m venv .venv
.venv\Scripts\activate
```

You should see `(.venv)` in your prompt after activation.

### Step 3 — Install dependencies

```
pip install -r requirements.txt
```

This installs FastAPI, yfinance, pandas, ta (technical analysis), and all other required
packages. Takes 1–2 minutes on first run.

### Step 4 — Create your .env file

```
copy .env.example .env
```

Open `.env` and fill in your keys:

```env
# NewsAPI — optional, free key at https://newsapi.org/register
NEWS_API_KEY=your_key_here

# Azure OpenAI — from Azure AI Foundry → your resource → Keys and Endpoint
AZURE_OPENAI_API_KEY=your_azure_key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini
AZURE_OPENAI_API_VERSION=2025-01-01-preview

# Standard OpenAI (only used if Azure fields above are blank)
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini

# Telegram (optional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

ENABLE_SCHEDULER=false
```

> **Azure endpoint note**: Use the **OpenAI** endpoint (`*.openai.azure.com`),
> **not** the Project endpoint (`*.services.ai.azure.com`).
> In Azure AI Foundry: Resource → Keys and Endpoint → copy the "OpenAI" URL.

### Step 5 — Start the server

```
py -m uvicorn app.main:app --reload --port 8000
```

You will see output like:

```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

### Step 6 — Open the dashboard

Navigate to **http://localhost:8000** in your browser.

---

## How to Use

### Running a scan

1. Set **Account Capital** (e.g. ₹5,00,000)
2. Set **Risk %** — default is **2%**. Do not exceed 3% for live trading.
3. Set **Consecutive Losses** to your actual current streak (0 if none)
4. Click **Run Scan**

The scan fetches live data from NSE for 40 instruments in parallel.
It takes approximately **10–20 seconds**. A progress bar slides across the top.

### Understanding the results

| Element | Meaning |
|---|---|
| Green card | Passed all 12 hard gates and scored ≥ 72/100 |
| Confidence score | Sum of 6 category scores out of 100 |
| Valid Until | Time by which price action must hold — do not enter after this |
| Lots | Maximum position size within your account risk |
| Red card (collapsed) | Rejected — click to expand and see which gate(s) failed |
| "No Trade Mode" banner | Zero signals passed — preserve capital, wait for alignment |

### Trade journal

Click **Log Trade** on any approved signal to pre-fill the journal form.
After closing a trade, update it with **Exit Price**, **Outcome**, and **P&L (R)**
so the daily / weekly / monthly drawdown state is accurate for future scans.

---

## Project Structure

```
├── app/
│   ├── main.py               FastAPI routes and scan orchestration
│   ├── config.py             Settings loaded from .env
│   ├── sample_data.py        Fallback data when NSE is offline
│   ├── data_sources/
│   │   ├── nse.py            NSE live data, parallel candidate fetch, option chain
│   │   └── news.py           NewsAPI headlines with 15-min cache
│   └── services/
│       ├── scanner.py        Hard gates, 6-category scoring, position sizing
│       ├── storage.py        SQLite — scan audit, trade journal, risk state
│       ├── ai.py             Azure/OpenAI trade rationale generation
│       ├── backtest.py       Historical win-rate / profit-factor stats
│       ├── telegram.py       Alert formatting and Telegram send
│       └── scheduler.py      APScheduler — 9:20 AM and 3:20 PM auto-scans
├── src/
│   └── app.js                Browser UI — rendering and event handling
├── index.html                Dashboard page
├── styles.css                UI styles
├── requirements.txt          Python dependencies
├── .env.example              Environment variable template
└── data/
    └── research.db           SQLite database (auto-created on first run)
```

---

## Common Issues

**`python` command not found or uses wrong version (3.8)**
Use `py` instead of `python`. The `py` launcher picks the latest Python installed.
Run `py --version` to confirm it shows 3.11+.

**NSE data unavailable / sample data shown**
NSE rate-limits external requests. The app automatically falls back to sample data
and labels the source as "sample". Wait a few minutes and try again.

**Azure OpenAI 404 / ResourceNotFound**
Your `AZURE_OPENAI_ENDPOINT` is likely the Project endpoint (`services.ai.azure.com`).
Change it to the OpenAI endpoint (`your-resource.openai.azure.com`).

**All signals rejected — No Trade Mode**
Expand the red cards to read the rejection reasons. The most common causes:
- India VIX ≥ 22 (cannot override — market is too volatile)
- Time is in opening chop window (9:15–9:30 IST)
- Time is in closing volatility window (14:45–15:30 IST)
- No instrument has a clean EMA20 > EMA50 > EMA200 bullish alignment today

**Database errors on first run**
The `data/` folder and `research.db` are created automatically on startup.
If you see permission errors, move the project out of a system-protected folder.
