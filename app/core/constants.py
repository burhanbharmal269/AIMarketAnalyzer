"""Single source of truth for every magic number and string in the application.

Import from here — never define numeric literals in business logic.
"""

# ── Scoring ───────────────────────────────────────────────────────────────────
SCORE_CATEGORIES: dict[str, int] = {
    "trend":       33,   # +3 for 15-min Supertrend; +3 ORB breakout
    "momentum":    23,   # +3 MACD histogram expansion; +2 ADX direction
    "volume":      15,
    "optionChain": 20,
    "sentiment":   10,
    "riskReward":  10,
    "news":         5,
}
SCORE_MAX_RAW: int = sum(SCORE_CATEGORIES.values())   # 116

# ── Scan defaults ─────────────────────────────────────────────────────────────
DEFAULT_SCAN_SETTINGS: dict = {
    "accountCapital":        100_000,   # 1 lakh minimum — F&O lot margin requirement
    "riskPercent":           2,
    "maxSpread":             1.5,
    "minVolume":             25_000,    # 25K minimum option volume
    "eventWindow":           60,
    "lossStreak":            0,
    "maxDailyLossPct":       3,
    "maxWeeklyDrawdownPct":  8,
    "maxMonthlyDrawdownPct": 15,
    "minScore":              65,        # raw ≈ 75/116 after normalisation
    "maxSignals":            5,
}

# ── Market hours (IST) ────────────────────────────────────────────────────────
MARKET_OPEN_H,       MARKET_OPEN_M       = 9,  15
MARKET_CLOSE_H,      MARKET_CLOSE_M      = 15, 30
TIME_EXIT_H,         TIME_EXIT_M         = 14, 15
OPENING_VOL_END_H,   OPENING_VOL_END_M   = 9,  30   # avoid first-15-min chop
CLOSING_VOL_START_H, CLOSING_VOL_START_M = 15,  0   # avoid last-30-min close volatility
EXPIRY_GATE_HOUR = 11    # Tuesday after 11:00 IST = weekly expiry gamma gate

# ── VIX thresholds ────────────────────────────────────────────────────────────
# VIX thresholds: the hard gate (22) is calibrated for OPTION BUYERS —
# above 22 is extreme fear where premiums are completely mispriced.
# Research VIX <13 thresholds apply to CREDIT strategies (selling), not buying.
# VIX 18-22 = elevated but moves are also larger, creating momentum opportunities.
VIX_HARD_GATE  = 22    # above this: extreme fear, no directional option buying
VIX_CAUTION    = 20    # elevated — OptionChainScorer already penalises high ATM IV
VIX_ELEVATED   = 18
VIX_CALM       = 16
VIX_VERY_CALM  = 14

# ── Risk / money management ───────────────────────────────────────────────────
MAX_PNL_LOSS_CAP   = -3.0   # floor on recorded loss (in R units)
MAX_PNL_WIN_CAP    =  5.0   # ceiling on recorded win
MIN_IV_RANK_GATE   = 90     # IV Rank percentile hard cut-off (raised from 80 — option buyers need movement, high IV is not always bad)

# RSI extremes — hard blocks (research: RSI>80 = chasing overbought, high IV crush risk)
RSI_OVERBOUGHT_GATE = 78    # BUY blocked above this (too extended, reversion likely)
RSI_OVERSOLD_GATE   = 22    # SELL blocked below this (bounce risk, mean-reversion)

# Transaction costs (research: must be factored into profit projections)
STT_RATE_SELL = 0.0015      # 0.15% on option sell (exit) — post April 2026 NSE rate

# ── Monitor daemon ────────────────────────────────────────────────────────────
MONITOR_INTERVAL_SECS  = 60
WATCHDOG_INTERVAL_SECS = 900

# ── Telegram retry ────────────────────────────────────────────────────────────
TELEGRAM_RETRY_DELAYS = (30, 120)   # seconds: retry 1→2 wait, retry 2→3 wait
TELEGRAM_DRAIN_SECS   = 15          # how often the retry thread wakes

# ── Kite Connect ─────────────────────────────────────────────────────────────
KITE_RATE_LIMIT_SLEEP  = 0.34           # ~3 req/sec
KITE_TOKEN_EXPIRY_HOUR = 6              # 6 AM IST daily rollover (regulatory)

# ── NSE scraper ───────────────────────────────────────────────────────────────
NSE_BASE            = "https://www.nseindia.com"
NSE_API_BASE        = "https://www.nseindia.com/api"
OC_TIMEOUT_SECS     = 15
EARNINGS_WINDOW_DAYS = 2   # days ahead to treat as event-risk window

# ── Backtest ──────────────────────────────────────────────────────────────────
BACKTEST_LOOKBACK = "180d"
BACKTEST_INTERVAL = "1d"
BACKTEST_SYMBOLS  = [
    "NIFTY", "BANKNIFTY", "RELIANCE", "INFY",
    "HDFCBANK", "TCS", "ICICIBANK", "AXISBANK",
]
ATR_STOP_MULT = 1.5

# ── Scan cache ────────────────────────────────────────────────────────────────
SCAN_CACHE_TTL_SECS  = 900
SCAN_AUDIT_KEEP_DAYS = 30

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_MAX_BYTES    = 10 * 1_024 * 1_024   # 10 MB per file
LOG_BACKUP_COUNT = 5

# ── Application metadata ──────────────────────────────────────────────────────
APP_VERSION = "0.3.0"
APP_NAME    = "Indian Options Research Desk"

# ── Sector map ────────────────────────────────────────────────────────────────
# Used to cap at 1 approved signal per sector — prevents correlated blowups.
# Indices are exempt (NIFTY and BANKNIFTY are independent, non-correlated products).
SECTOR_MAP: dict[str, str] = {
    "NIFTY": "index", "BANKNIFTY": "index",
    "HDFCBANK":   "banking",  "ICICIBANK":  "banking",  "AXISBANK":    "banking",
    "KOTAKBANK":  "banking",  "SBIN":       "banking",  "INDUSINDBK":  "banking",
    "BANKBARODA": "banking",  "PNB":        "banking",
    "BAJFINANCE": "finance",  "BAJAJFINSV": "finance",  "HDFCLIFE":    "finance",
    "CHOLAFIN":   "finance",
    "INFY":       "it",       "TCS":        "it",       "WIPRO":       "it",
    "HCLTECH":    "it",       "TECHM":      "it",       "LTIM":        "it",
    "PERSISTENT": "it",
    "RELIANCE":   "oil_gas",  "ONGC":       "oil_gas",  "BPCL":        "oil_gas",
    "BHARTIARTL": "telecom",
    "MARUTI":     "auto",     "EICHERMOT":  "auto",     "M&M":         "auto",
    "TATAMOTORS": "auto",     "BAJAJ-AUTO": "auto",     "HEROMOTOCO":  "auto",
    "SUNPHARMA":  "pharma",   "DRREDDY":    "pharma",   "CIPLA":       "pharma",
    "DIVISLAB":   "pharma",   "AUROPHARMA": "pharma",
    "HINDUNILVR": "fmcg",     "NESTLEIND":  "fmcg",     "TATACONSUM":  "fmcg",
    "ITC":        "fmcg",     "BRITANNIA":  "fmcg",     "GODREJCP":    "fmcg",
    "LT":         "infra",    "ADANIPORTS": "infra",    "POWERGRID":   "infra",
    "NTPC":       "infra",    "TATAPOWER":  "infra",
    "JSWSTEEL":   "metals",   "TATASTEEL":  "metals",   "HINDALCO":    "metals",
    "VEDL":       "metals",
    "COALINDIA":  "energy",
    "ULTRACEMCO": "cement",   "GRASIM":     "cement",
    "ASIANPAINT": "consumer", "TITAN":      "consumer", "APOLLOHOSP":  "consumer",
    "ZOMATO":     "consumer", "DMART":      "consumer",
    "BEL":        "defense",
    "DLF":        "realty",
}
EXEMPT_SECTOR = "index"   # sectors with this value bypass the concentration gate

# ── Global cues gate ──────────────────────────────────────────────────────────
# If S&P 500 previous session < this threshold, block all BUY/CE candidates.
# Rationale: >1% US overnight drop correlates strongly with NSE gap-down opens
# and elevated CE risk. Only PE/SELL setups are permitted on such days.
SP500_GATE_PCT: float = -1.0
