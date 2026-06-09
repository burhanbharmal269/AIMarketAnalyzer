"""
Angel One SmartAPI data source.

Primary data source for option chains, intraday candles (VWAP), and live LTP.
nse.py uses this first and falls back to jugaad-data scraping if unavailable.

Environment variables:
    ANGEL_API_KEY      — SmartAPI app key (from smartapi.angelone.in)
    ANGEL_CLIENT_ID    — Angel One login ID (e.g. A123456)
    ANGEL_PIN          — 4-digit MPIN
    ANGEL_TOTP_SECRET  — TOTP secret from the Angel One mobile app
"""

import logging
import os
import threading
import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# ── Ensure .env is loaded regardless of import order ─────────────────────────
# ANGEL_AVAILABLE is read at module load time via os.getenv. If angel.py is
# imported before config.py (test scripts, direct imports), the .env file may
# not yet be in os.environ and all four vars would read as empty strings,
# silently disabling Angel One even when credentials are correctly configured.
# Loading dotenv here is idempotent — it does NOT overwrite vars already set.
try:
    from dotenv import load_dotenv as _load_dotenv
    import pathlib as _pathlib
    _load_dotenv(_pathlib.Path(__file__).resolve().parents[2] / ".env")
except Exception:
    pass

# ── Credentials ───────────────────────────────────────────────────────────────
ANGEL_API_KEY     = os.getenv("ANGEL_API_KEY", "")
ANGEL_CLIENT_ID   = os.getenv("ANGEL_CLIENT_ID", "")
ANGEL_PIN         = os.getenv("ANGEL_PIN", "")
ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")

ANGEL_AVAILABLE = all([ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PIN, ANGEL_TOTP_SECRET])

try:
    from SmartApi import SmartConnect
    _SMARTAPI = True
except ImportError:
    _SMARTAPI = False

try:
    import pyotp
    _PYOTP = True
except ImportError:
    _PYOTP = False

try:
    import pandas as pd
    _PANDAS = True
except ImportError:
    _PANDAS = False

from app.core.constants import ANGEL_SESSION_TTL_SECS, ANGEL_RATE_LIMIT_SLEEP

_SESSION_TTL      = ANGEL_SESSION_TTL_SECS
_RATE_LIMIT_SLEEP = ANGEL_RATE_LIMIT_SLEEP

# Global rate limiter — prevents burst violations when 8 threads fire simultaneously.
# Per-function time.sleep() doesn't help in parallel scans: each thread sleeps 0.35s
# locally but all call the API at the same time.  This lock + timestamp serialises
# every Angel One API call across all threads so the 3 req/sec limit is never breached.
_rate_lock       = threading.Lock()
_last_angel_call: float = 0.0


def _throttle() -> None:
    """Block the calling thread until the global Angel One rate window allows a call."""
    global _last_angel_call
    with _rate_lock:
        gap = _RATE_LIMIT_SLEEP - (time.time() - _last_angel_call)
        if gap > 0:
            time.sleep(gap)
        _last_angel_call = time.time()


# ── NSE index tokens (cash segment) ──────────────────────────────────────────
# Used for getCandleData (OHLCV + intraday) and ltpData.
# Keys = our symbol name, Values = (exchange, token)
_INDEX_TOKENS: dict[str, tuple[str, str]] = {
    "NIFTY":      ("NSE", "26000"),
    "BANKNIFTY":  ("NSE", "26009"),
    "FINNIFTY":   ("NSE", "26037"),
    "MIDCPNIFTY": ("NSE", "26074"),
    "NIFTYNXT50": ("NSE", "26013"),
}

# NSE cash-segment tokens for the 40 most liquid F&O stocks.
# Source: Angel One instrument master (margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json)
# Update quarterly or call get_stock_token() to fetch dynamically.
_STOCK_TOKENS: dict[str, tuple[str, str]] = {
    "RELIANCE":    ("NSE", "2885"),
    "HDFCBANK":    ("NSE", "1333"),
    "ICICIBANK":   ("NSE", "4963"),
    "INFY":        ("NSE", "1594"),
    "TCS":         ("NSE", "11536"),
    "AXISBANK":    ("NSE", "5900"),
    "KOTAKBANK":   ("NSE", "1922"),
    "SBIN":        ("NSE", "3045"),
    "LT":          ("NSE", "11483"),
    "WIPRO":       ("NSE", "3787"),
    "BHARTIARTL":  ("NSE", "10604"),
    "HCLTECH":     ("NSE", "7229"),
    "BAJFINANCE":  ("NSE", "317"),
    "BAJAJFINSV":  ("NSE", "16675"),
    "MARUTI":      ("NSE", "10999"),
    "SUNPHARMA":   ("NSE", "3351"),
    "TECHM":       ("NSE", "13538"),
    "TITAN":       ("NSE", "3506"),
    "ASIANPAINT":  ("NSE", "236"),
    "HINDUNILVR":  ("NSE", "1394"),
    "ULTRACEMCO":  ("NSE", "11532"),
    "NESTLEIND":   ("NSE", "17963"),
    "POWERGRID":   ("NSE", "14977"),
    "NTPC":        ("NSE", "11630"),
    "ONGC":        ("NSE", "2475"),
    "M&M":         ("NSE", "2031"),
    "ADANIPORTS":  ("NSE", "15083"),
    "JSWSTEEL":    ("NSE", "11723"),
    "TATASTEEL":   ("NSE", "3499"),
    "HINDALCO":    ("NSE", "1363"),
    "GRASIM":      ("NSE", "1232"),
    "DRREDDY":     ("NSE", "881"),
    "CIPLA":       ("NSE", "694"),
    "DIVISLAB":    ("NSE", "10940"),
    "INDUSINDBK":  ("NSE", "5258"),
    "HDFCLIFE":    ("NSE", "467"),
    "EICHERMOT":   ("NSE", "910"),
    "APOLLOHOSP":  ("NSE", "157"),
    "TATACONSUM":  ("NSE", "3432"),
    # Tier-2 expansion — verified Angel One ScripMaster tokens
    "TATAMOTORS":  ("NSE", "3456"),
    "BPCL":        ("NSE", "526"),
    "ITC":         ("NSE", "1660"),
    "BEL":         ("NSE", "383"),
    "BRITANNIA":   ("NSE", "547"),
    "HEROMOTOCO":  ("NSE", "1348"),
    "BANKBARODA":  ("NSE", "4668"),
    "PNB":         ("NSE", "2730"),
    "AUROPHARMA":  ("NSE", "275"),
    "VEDL":        ("NSE", "3063"),
    "TATAPOWER":   ("NSE", "3426"),
    # Dynamic lookup via searchScrip for newer listings
    # COALINDIA, DLF, GODREJCP, ZOMATO, LTIM, PERSISTENT, CHOLAFIN, BAJAJ-AUTO, DMART
}

_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

_LOT_SIZES = {
    "NIFTY": 75, "BANKNIFTY": 15, "FINNIFTY": 40,
    "MIDCPNIFTY": 75, "NIFTYNXT50": 25,
    "RELIANCE": 250, "HDFCBANK": 550, "ICICIBANK": 700,
    "INFY": 300, "TCS": 150, "AXISBANK": 1200, "SBIN": 1500,
    "KOTAKBANK": 400, "LT": 375, "WIPRO": 2400,
    "BAJFINANCE": 125, "BAJAJFINSV": 125, "BHARTIARTL": 950,
    "HCLTECH": 700, "TECHM": 600, "MARUTI": 15, "M&M": 700,
    "SUNPHARMA": 700, "DRREDDY": 125, "CIPLA": 650,
    "HINDUNILVR": 300, "ASIANPAINT": 200, "TITAN": 375,
    "ADANIPORTS": 1250, "JSWSTEEL": 600, "TATASTEEL": 5500,
    "HINDALCO": 2150, "NTPC": 3000, "ONGC": 3850,
    "ULTRACEMCO": 100, "GRASIM": 475, "INDUSINDBK": 500,
    "HDFCLIFE": 1100, "EICHERMOT": 100, "NESTLEIND": 50,
    "POWERGRID": 4800, "DIVISLAB": 200, "APOLLOHOSP": 125,
    "TATACONSUM": 1350,
    # Tier-2 expansion (lot sizes as of 2025 — verify quarterly from NSE F&O circulars)
    "TATAMOTORS": 1425, "BAJAJ-AUTO": 125,  "HEROMOTOCO": 300,
    "BANKBARODA": 5850, "PNB":        8000,
    "BPCL":       1800, "COALINDIA":  4200,  "ITC":        3200, "TATAPOWER":  4350,
    "BEL":        2900, "DLF":        1350,
    "VEDL":       4100,
    "AUROPHARMA": 650,  "BRITANNIA":  200,   "GODREJCP":   500,
    "ZOMATO":     4500, "DMART":      132,
    "LTIM":       150,  "PERSISTENT": 125,
    "CHOLAFIN":   1250,
}


# ── Session management ────────────────────────────────────────────────────────

class AngelOneSession:
    """Thread-safe SmartAPI session with automatic TOTP login and JWT refresh."""

    def __init__(self):
        self._obj: "SmartConnect | None" = None
        self._logged_in_at: float = 0.0
        self._lock = threading.Lock()
        # Token cache: symbol -> (exchange, token)  populated via searchScrip
        self._token_cache: dict[str, tuple[str, str]] = {}

    def is_configured(self) -> bool:
        return ANGEL_AVAILABLE and _SMARTAPI and _PYOTP

    def login(self, retries: int = 3) -> bool:
        """Login with TOTP retry — regenerates code each attempt to handle clock skew."""
        if not self.is_configured():
            logger.warning("Angel One not configured — check env vars")
            return False
        with self._lock:
            for attempt in range(1, retries + 1):
                try:
                    totp_code = pyotp.TOTP(ANGEL_TOTP_SECRET).now()
                    obj = SmartConnect(api_key=ANGEL_API_KEY)
                    resp = obj.generateSession(ANGEL_CLIENT_ID, ANGEL_PIN, totp_code)
                    if resp and resp.get("status") is not False and resp.get("data"):
                        self._obj = obj
                        self._logged_in_at = time.time()
                        name = resp["data"].get("name", ANGEL_CLIENT_ID)
                        logger.info("Angel One login OK — %s (attempt %d)", name, attempt)
                        return True
                    msg = (resp or {}).get("message", "no response")
                    logger.warning("Angel One login attempt %d/%d failed: %s", attempt, retries, msg)
                except Exception as exc:
                    logger.warning("Angel One login attempt %d/%d error: %s", attempt, retries, exc)
                if attempt < retries:
                    time.sleep(1.5)   # wait for next TOTP window
            logger.error("Angel One login failed after %d attempts", retries)
            self._obj = None
            return False

    def get_client(self) -> "SmartConnect | None":
        """Return active SmartConnect client, refreshing session if expired or missing."""
        age = time.time() - self._logged_in_at
        # If session is fresh, return as-is
        if self._obj is not None and age <= _SESSION_TTL:
            return self._obj
        # Session expired or never set — re-login
        if not self.login():
            return None
        return self._obj

    def ensure_connected(self) -> bool:
        """Returns True if a valid session is active (or just established)."""
        return self.get_client() is not None

    def status(self) -> dict:
        """Return session health info for the /api/health endpoint."""
        age = time.time() - self._logged_in_at
        connected = self._obj is not None and age <= _SESSION_TTL
        return {
            "connected":     connected,
            "configured":    self.is_configured(),
            "session_age_s": round(age) if self._logged_in_at > 0 else None,
            "session_ttl_s": _SESSION_TTL,
            "client_id":     ANGEL_CLIENT_ID if ANGEL_CLIENT_ID else None,
        }

    def get_token(self, symbol: str) -> tuple[str, str] | None:
        """Return (exchange, token) for a symbol. Index → hardcoded; Stock → searchScrip."""
        if symbol in _INDEX_TOKENS:
            return _INDEX_TOKENS[symbol]
        if symbol in _STOCK_TOKENS:
            return _STOCK_TOKENS[symbol]
        # Dynamic lookup via searchScrip for unknown symbols
        if symbol in self._token_cache:
            return self._token_cache[symbol]
        client = self.get_client()
        if not client:
            return None
        try:
            _throttle()
            resp = client.searchScrip("NSE", symbol)
            if resp and resp.get("status") and resp.get("data"):
                for row in resp["data"]:
                    if row.get("tradingsymbol", "").upper() == symbol.upper():
                        token = str(row.get("symboltoken", ""))
                        if token:
                            self._token_cache[symbol] = ("NSE", token)
                            return ("NSE", token)
        except Exception as exc:
            logger.debug("Angel One searchScrip(%s) failed: %s", symbol, exc)
        return None

    def logout(self):
        if self._obj:
            try:
                self._obj.terminateSession(ANGEL_CLIENT_ID)
            except Exception:
                pass
        self._obj = None
        self._logged_in_at = 0.0


angel_session = AngelOneSession()


def _session_keepalive_worker():
    """Background thread: re-login 1 hour before JWT expires so scans never hit a dead session."""
    while True:
        try:
            age = time.time() - angel_session._logged_in_at
            # Refresh when 1 hour before TTL (23h window → refresh at 22h)
            if angel_session._obj is not None and age > (_SESSION_TTL - 3600):
                logger.info("Angel One session nearing expiry — refreshing proactively")
                angel_session.login()
        except Exception as exc:
            logger.debug("Session keepalive tick error: %s", exc)
        time.sleep(1800)   # check every 30 minutes


def startup_login() -> bool:
    """Call from FastAPI startup to establish session immediately.
    Returns True if connected, False if credentials not set or login failed.
    """
    if not angel_session.is_configured():
        logger.info("Angel One not configured — running in NSE-scrape mode")
        return False
    ok = angel_session.login()
    if ok:
        t = threading.Thread(target=_session_keepalive_worker, daemon=True, name="angel-keepalive")
        t.start()
        logger.info("Angel One keepalive thread started")
    return ok


# ── Expiry helpers ────────────────────────────────────────────────────────────

# Indices whose weekly contracts expire every Tuesday (NSE schedule since 2025-09-01).
# These use the nearest upcoming Tuesday, not the end-of-month date.
_WEEKLY_TUESDAY_EXPIRY = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


def _next_weekday(from_date: date, weekday: int) -> date:
    """Return the nearest upcoming date (>= from_date) that falls on `weekday` (0=Mon…6=Sun)."""
    days_ahead = (weekday - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead)


def _last_thursday_of_month(year: int, month: int) -> date:
    """Last Thursday of the given month — standard NSE stock monthly F&O expiry."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - 3) % 7)  # 3 = Thursday


def _nearest_stock_monthly_expiry(today: date) -> date:
    """Last Thursday of current month, rolling to next month if already past."""
    d = _last_thursday_of_month(today.year, today.month)
    if d < today:
        nxt_month = today.month + 1 if today.month < 12 else 1
        nxt_year  = today.year if today.month < 12 else today.year + 1
        d = _last_thursday_of_month(nxt_year, nxt_month)
    return d


def _guess_expiry(symbol: str) -> str:
    """Return Angel One expiry string (e.g. '09Jun2026') for the nearest valid expiry.

    Expiry rules (NSE F&O schedule):
      - Weekly index contracts (NIFTY, BANKNIFTY, etc.): nearest upcoming Tuesday.
      - Stock monthly contracts: last Thursday of the current (or next) month.
    """
    today = date.today()
    if symbol in _WEEKLY_TUESDAY_EXPIRY:
        d = _next_weekday(today, weekday=1)   # 1 = Tuesday
    else:
        d = _nearest_stock_monthly_expiry(today)
    return d.strftime("%d%b%Y")


def _expiry_to_nse_fmt(angel_expiry: str) -> str:
    """Convert '09Jun2026' → '09-Jun-2026' (NSE format used by nse.py parser)."""
    try:
        return datetime.strptime(angel_expiry.upper(), "%d%b%Y").strftime("%d-%b-%Y")
    except Exception:
        return angel_expiry


# ── Option chain ──────────────────────────────────────────────────────────────

def get_option_chain(symbol: str) -> dict | None:
    """
    Fetch option chain from Angel One and return in NSE 'records' format
    so nse.py's _parse_option_chain() works without modification.

    Returns NSE-compatible dict with keys: records.expiryDates, records.data, filtered.
    Returns None if Angel One unavailable or call fails.
    """
    client = angel_session.get_client()
    if client is None:
        return None

    expiry_str = _guess_expiry(symbol)

    # Angel One rate limit: sleep before each call
    _throttle()

    resp = None
    for fmt in [expiry_str, expiry_str.upper()]:
        try:
            resp = client.optionGreek({"name": symbol, "expirydate": fmt})
            if resp and resp.get("status") is not False and resp.get("data"):
                break
        except Exception as exc:
            logger.debug("Angel optionGreek %s [%s] error: %s", symbol, fmt, exc)

    if not resp or not resp.get("data"):
        logger.warning("Angel OC failed for %s (expiry %s)", symbol, expiry_str)
        return None

    rows = resp["data"]
    nse_expiry = _expiry_to_nse_fmt(expiry_str)

    # Build NSE-compatible records.data rows
    nse_rows = []
    total_ce_oi = 0
    total_pe_oi = 0

    for row in rows:
        sp = float(row.get("strikePrice", 0) or 0)
        if sp <= 0:
            continue

        ce_raw = row.get("CE") or {}
        pe_raw = row.get("PE") or {}

        def _f(d: dict, key: str, default=0):
            try:
                return float(d.get(key) or default)
            except (TypeError, ValueError):
                return float(default)

        ce_oi   = _f(ce_raw, "openInterest")
        pe_oi   = _f(pe_raw, "openInterest")
        ce_chg  = _f(ce_raw, "changeinOpenInterest")
        pe_chg  = _f(pe_raw, "changeinOpenInterest")
        ce_pchg = round(ce_chg / ce_oi * 100, 2) if ce_oi > 0 else 0.0
        pe_pchg = round(pe_chg / pe_oi * 100, 2) if pe_oi > 0 else 0.0

        total_ce_oi += ce_oi
        total_pe_oi += pe_oi

        spot = _f(ce_raw, "underlyingValue") or _f(pe_raw, "underlyingValue")

        nse_rows.append({
            "strikePrice": sp,
            "expiryDate":  nse_expiry,
            "CE": {
                "lastPrice":              _f(ce_raw, "lastPrice"),
                "openInterest":           ce_oi,
                "changeinOpenInterest":   ce_chg,
                "pchangeinOpenInterest":  ce_pchg,
                "totalTradedVolume":      _f(ce_raw, "totalTradedVolume"),
                "impliedVolatility":      _f(ce_raw, "impliedVolatility"),
                "bidprice":               _f(ce_raw, "bidprice") or _f(ce_raw, "lastPrice") * 0.995,
                "askPrice":               _f(ce_raw, "askPrice") or _f(ce_raw, "lastPrice") * 1.005,
                "underlyingValue":        spot,
            },
            "PE": {
                "lastPrice":              _f(pe_raw, "lastPrice"),
                "openInterest":           pe_oi,
                "changeinOpenInterest":   pe_chg,
                "pchangeinOpenInterest":  pe_pchg,
                "totalTradedVolume":      _f(pe_raw, "totalTradedVolume"),
                "impliedVolatility":      _f(pe_raw, "impliedVolatility"),
                "bidprice":               _f(pe_raw, "bidprice") or _f(pe_raw, "lastPrice") * 0.995,
                "askPrice":               _f(pe_raw, "askPrice") or _f(pe_raw, "lastPrice") * 1.005,
                "underlyingValue":        spot,
            },
        })

    if not nse_rows:
        return None

    return {
        "records": {
            "expiryDates": [nse_expiry],
            "data": nse_rows,
        },
        "filtered": {
            "CE": {"totOI": total_ce_oi},
            "PE": {"totOI": total_pe_oi},
        },
        "_source": "angel",
    }


# ── Intraday candles (for VWAP) ───────────────────────────────────────────────

def get_intraday_candles(symbol: str, interval: str = "FIVE_MINUTE") -> "pd.DataFrame | None":
    """
    Fetch today's intraday candles from Angel One.
    Returns DataFrame: datetime, open, high, low, close, volume.
    Used to compute VWAP and intraday momentum signals.

    interval: ONE_MINUTE | THREE_MINUTE | FIVE_MINUTE | TEN_MINUTE | FIFTEEN_MINUTE
    """
    if not _PANDAS:
        return None

    client = angel_session.get_client()
    if client is None:
        return None

    token_info = angel_session.get_token(symbol)
    if not token_info:
        logger.debug("No token for %s — cannot fetch intraday candles", symbol)
        return None

    exchange, token = token_info
    now_ist   = datetime.now(IST)
    from_date = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    to_date   = now_ist

    _throttle()
    try:
        resp = client.getCandleData({
            "exchange":    exchange,
            "symboltoken": token,
            "interval":    interval,
            "fromdate":    from_date.strftime("%Y-%m-%d %H:%M"),
            "todate":      to_date.strftime("%Y-%m-%d %H:%M"),
        })
        if not resp or resp.get("status") is False or not resp.get("data"):
            return None

        df = pd.DataFrame(resp["data"], columns=["datetime", "open", "high", "low", "close", "volume"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["datetime"] = pd.to_datetime(df["datetime"])
        return df.dropna(subset=["close"]).reset_index(drop=True)

    except Exception as exc:
        logger.debug("Angel intraday candles %s failed: %s", symbol, exc)
        return None


def compute_vwap(candles: "pd.DataFrame") -> float | None:
    """
    Compute VWAP from a DataFrame with columns: high, low, close, volume.
    VWAP = sum(typical_price × volume) / sum(volume)
    Returns None if data is insufficient.
    """
    if candles is None or len(candles) < 3:
        return None
    try:
        tp = (candles["high"] + candles["low"] + candles["close"]) / 3
        vol = candles["volume"]
        total_vol = vol.sum()
        if total_vol <= 0:
            return None
        vwap = (tp * vol).sum() / total_vol
        return round(float(vwap), 2)
    except Exception as exc:
        logger.debug("VWAP computation failed: %s", exc)
        return None


# ── Live LTP ──────────────────────────────────────────────────────────────────

def get_ltp(symbol: str) -> float | None:
    """Return last traded price for a symbol using Angel One ltpData."""
    client = angel_session.get_client()
    if client is None:
        return None

    token_info = angel_session.get_token(symbol)
    if not token_info:
        return None

    exchange, token = token_info
    _throttle()
    try:
        resp = client.ltpData(exchange, symbol, token)
        if resp and resp.get("status") is not False:
            ltp = (resp.get("data") or {}).get("ltp")
            return float(ltp) if ltp else None
    except Exception as exc:
        logger.debug("Angel LTP(%s) error: %s", symbol, exc)
    return None


def get_ltp_batch(symbols: list[str]) -> dict[str, float]:
    """
    Fetch LTP for multiple symbols in one Angel One API call.
    Returns {symbol: ltp}. Faster than individual ltpData calls.
    """
    client = angel_session.get_client()
    if client is None:
        return {}

    exchange_tokens = []
    sym_map: dict[str, str] = {}  # token -> symbol
    for sym in symbols:
        info = angel_session.get_token(sym)
        if info:
            exchange_tokens.append({"exchange": info[0], "tradingsymbol": sym, "symboltoken": info[1]})
            sym_map[info[1]] = sym

    if not exchange_tokens:
        return {}

    _throttle()
    try:
        resp = client.getMarketData("LTP", exchange_tokens)
        if not resp or resp.get("status") is False:
            return {}
        result: dict[str, float] = {}
        for item in (resp.get("data") or {}).get("fetched", []):
            token = str(item.get("symbolToken", ""))
            ltp = item.get("ltp")
            sym = sym_map.get(token)
            if sym and ltp is not None:
                result[sym] = float(ltp)
        return result
    except Exception as exc:
        logger.debug("Angel batch LTP error: %s", exc)
        return {}


# ── Option LTP (for position monitoring) ─────────────────────────────────────

def get_option_ltp(underlying: str, strike: float, opt_type: str,
                   expiry_hint: str | None = None) -> dict | None:
    """
    Get real-time quote for a specific option.

    Args:
        underlying:   'NIFTY', 'BANKNIFTY', 'TCS', etc.
        strike:       numeric strike price (e.g. 23200)
        opt_type:     'CE' or 'PE'
        expiry_hint:  Angel One expiry string override (e.g. '12Jun2026')
                      If None, _guess_expiry() is used.

    Returns dict with keys: ltp, change, changePct, oi, volume, iv, bid, ask
    Returns None if not found or connection failure.
    """
    client = angel_session.get_client()
    if client is None:
        logger.warning("get_option_ltp: Angel One not connected")
        return None

    expiry_str = expiry_hint or _guess_expiry(underlying)
    _throttle()

    for fmt in [expiry_str, expiry_str.upper()]:
        try:
            resp = client.optionGreek({"name": underlying, "expirydate": fmt})
            if not resp or not resp.get("data"):
                continue
            for row in resp["data"]:
                sp = float(row.get("strikePrice", 0) or 0)
                if abs(sp - strike) > 0.5:
                    continue
                opt = row.get(opt_type) or {}
                ltp = opt.get("lastPrice") or opt.get("ltp")
                if ltp is None:
                    return None    # strike found but no price (market closed / expired)
                return {
                    "underlying": underlying,
                    "strike":     strike,
                    "optType":    opt_type,
                    "expiry":     fmt,
                    "ltp":        float(ltp),
                    "change":     float(opt.get("change") or 0),
                    "changePct":  float(opt.get("pChange") or 0),
                    "oi":         int(opt.get("openInterest") or 0),
                    "oiChange":   int(opt.get("changeinOpenInterest") or 0),
                    "volume":     int(opt.get("totalTradedVolume") or 0),
                    "iv":         float(opt.get("impliedVolatility") or 0),
                    "bid":        float(opt.get("bid") or opt.get("bidPrice") or 0),
                    "ask":        float(opt.get("ask") or opt.get("askPrice") or 0),
                }
        except Exception as exc:
            logger.debug("Angel option LTP [%s] %s %s %s error: %s",
                         fmt, underlying, strike, opt_type, exc)
    return None


# ── Historical daily OHLCV ───────────────────────────────────────────────────

def get_daily_ohlcv(symbol: str, days: int = 400) -> "pd.DataFrame | None":
    """Fetch historical daily OHLCV from Angel One getCandleData (ONE_DAY interval).

    Returns DataFrame indexed by Date with columns Open/High/Low/Close/Volume —
    same format as yfinance so nse.py's get_ohlcv_daily() can use it directly.
    Returns None if Angel One unavailable or call fails.

    days=400 gives 200+ trading days (covers EMA200 + 60-day S/R window with buffer).
    """
    if not _PANDAS:
        return None
    client = angel_session.get_client()
    if client is None:
        return None

    token_info = angel_session.get_token(symbol)
    if not token_info:
        logger.debug("No token for %s — cannot fetch daily OHLCV", symbol)
        return None

    exchange, token = token_info
    today     = date.today()
    from_date = today - timedelta(days=days)

    _throttle()
    try:
        resp = client.getCandleData({
            "exchange":    exchange,
            "symboltoken": token,
            "interval":    "ONE_DAY",
            "fromdate":    from_date.strftime("%Y-%m-%d 09:00"),
            "todate":      today.strftime("%Y-%m-%d 16:00"),
        })
        if not resp or resp.get("status") is False or not resp.get("data"):
            logger.debug("Angel daily OHLCV %s: empty response", symbol)
            return None

        df = pd.DataFrame(
            resp["data"],
            columns=["datetime", "Open", "High", "Low", "Close", "Volume"],
        )
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["Date"] = pd.to_datetime(df["datetime"]).dt.date
        df = df.drop(columns=["datetime"]).set_index("Date")
        df.index = pd.to_datetime(df.index)
        df = df.dropna(subset=["Close"])
        if len(df) < 60:
            logger.debug("Angel daily OHLCV %s: too few rows (%d)", symbol, len(df))
            return None
        logger.debug("Angel daily OHLCV %s: %d rows", symbol, len(df))
        return df

    except Exception as exc:
        logger.debug("Angel daily OHLCV %s failed: %s", symbol, exc)
        return None


# ── F&O universe ──────────────────────────────────────────────────────────────

def get_fo_universe() -> list[str]:
    """Full scan universe — 61 liquid F&O instruments.

    Tier-1 (41): original high-conviction stocks with confirmed option liquidity.
    Tier-2 (20): expanded coverage — liquid F&O stocks added to increase trade opportunities
                 without sacrificing quality (AI shortlist filters to best setups).
    """
    return [
        # Indices (most liquid, always first)
        "NIFTY", "BANKNIFTY",
        # Tier-1: Large-cap (highest option OI and volume)
        "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
        "AXISBANK", "KOTAKBANK", "SBIN", "LT", "WIPRO",
        "BHARTIARTL", "HCLTECH", "BAJFINANCE", "BAJAJFINSV",
        # Tier-1: Mid/large cap with strong F&O activity
        "MARUTI", "SUNPHARMA", "TECHM", "TITAN", "ASIANPAINT",
        "HINDUNILVR", "ULTRACEMCO", "NESTLEIND", "POWERGRID",
        "NTPC", "ONGC", "M&M", "ADANIPORTS", "JSWSTEEL",
        "TATASTEEL", "HINDALCO", "GRASIM", "DRREDDY", "CIPLA",
        "DIVISLAB", "INDUSINDBK", "HDFCLIFE", "EICHERMOT",
        "APOLLOHOSP", "TATACONSUM",
        # Tier-2: Expansion — more sectors, more trade candidates
        "TATAMOTORS", "BAJAJ-AUTO", "HEROMOTOCO",   # auto (broader coverage)
        "BANKBARODA", "PNB",                          # PSU banks (high OI)
        "BPCL", "COALINDIA", "ITC", "TATAPOWER",     # energy + diversified
        "BEL", "DLF",                                 # defense + realty
        "VEDL",                                       # metals (non-ferrous)
        "AUROPHARMA", "BRITANNIA", "GODREJCP",        # pharma + FMCG
        "ZOMATO", "DMART",                            # new-economy consumer
        "LTIM", "PERSISTENT",                         # mid-cap IT
        "CHOLAFIN",                                   # NBFC
    ]


# ── Startup test ──────────────────────────────────────────────────────────────

def test_connection() -> dict:
    """Test Angel One connection. Called at server startup."""
    if not ANGEL_AVAILABLE:
        missing = [k for k, v in {
            "ANGEL_API_KEY":     ANGEL_API_KEY,
            "ANGEL_CLIENT_ID":   ANGEL_CLIENT_ID,
            "ANGEL_PIN":         ANGEL_PIN,
            "ANGEL_TOTP_SECRET": ANGEL_TOTP_SECRET,
        }.items() if not v]
        return {"status": "not_configured", "message": f"Missing: {', '.join(missing)}"}

    if not _SMARTAPI:
        return {"status": "error", "message": "smartapi-python not installed"}
    if not _PYOTP:
        return {"status": "error", "message": "pyotp not installed"}

    ok = angel_session.login()
    if not ok:
        return {"status": "error", "message": "Login failed — check credentials and TOTP secret"}

    return {"status": "ok", "client_id": ANGEL_CLIENT_ID,
            "message": "Connected to Angel One SmartAPI"}
