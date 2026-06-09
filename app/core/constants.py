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
    "minScore":              70,        # raw ≈ 81/116 after normalisation
    "maxSignals":            5,
}

# ── Market hours (IST) ────────────────────────────────────────────────────────
MARKET_OPEN_H,       MARKET_OPEN_M       = 9,  15
MARKET_CLOSE_H,      MARKET_CLOSE_M      = 15, 30
TIME_EXIT_H,         TIME_EXIT_M         = 14, 15
OPENING_VOL_END_H,   OPENING_VOL_END_M   = 9,  30   # avoid first-15-min chop
CLOSING_VOL_START_H, CLOSING_VOL_START_M = 14, 45   # avoid close volatility
EXPIRY_GATE_HOUR = 11    # Tuesday after 11:00 IST = weekly expiry gamma gate

# ── VIX thresholds ────────────────────────────────────────────────────────────
VIX_HARD_GATE  = 22    # above this: no directional option buying
VIX_CAUTION    = 20
VIX_ELEVATED   = 18
VIX_CALM       = 16
VIX_VERY_CALM  = 14

# ── Risk / money management ───────────────────────────────────────────────────
MAX_PNL_LOSS_CAP   = -3.0   # floor on recorded loss (in R units)
MAX_PNL_WIN_CAP    =  5.0   # ceiling on recorded win
MIN_IV_RANK_GATE   = 80     # IV Rank percentile hard cut-off

# ── Monitor daemon ────────────────────────────────────────────────────────────
MONITOR_INTERVAL_SECS  = 60
WATCHDOG_INTERVAL_SECS = 900

# ── Telegram retry ────────────────────────────────────────────────────────────
TELEGRAM_RETRY_DELAYS = (30, 120)   # seconds: retry 1→2 wait, retry 2→3 wait
TELEGRAM_DRAIN_SECS   = 15          # how often the retry thread wakes

# ── Angel One ─────────────────────────────────────────────────────────────────
ANGEL_RATE_LIMIT_SLEEP = 0.35           # 1/3 s → ≤3 req/sec
ANGEL_SESSION_TTL_SECS = 23 * 3_600    # 23-hour JWT token TTL

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
    "BAJFINANCE": "finance",  "BAJAJFINSV": "finance",  "HDFCLIFE":    "finance",
    "INFY":       "it",       "TCS":        "it",       "WIPRO":       "it",
    "HCLTECH":    "it",       "TECHM":      "it",
    "RELIANCE":   "oil_gas",  "ONGC":       "oil_gas",
    "BHARTIARTL": "telecom",
    "MARUTI":     "auto",     "EICHERMOT":  "auto",     "M&M":         "auto",
    "SUNPHARMA":  "pharma",   "DRREDDY":    "pharma",   "CIPLA":       "pharma",
    "DIVISLAB":   "pharma",
    "HINDUNILVR": "fmcg",     "NESTLEIND":  "fmcg",     "TATACONSUM":  "fmcg",
    "LT":         "infra",    "ADANIPORTS": "infra",    "POWERGRID":   "infra",
    "NTPC":       "infra",
    "JSWSTEEL":   "metals",   "TATASTEEL":  "metals",   "HINDALCO":    "metals",
    "ULTRACEMCO": "cement",   "GRASIM":     "cement",
    "ASIANPAINT": "consumer", "TITAN":      "consumer", "APOLLOHOSP":  "consumer",
}
EXEMPT_SECTOR = "index"   # sectors with this value bypass the concentration gate
