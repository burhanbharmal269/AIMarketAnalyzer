"""Kite Connect (Zerodha) data source — replaces Angel One.

AUTHENTICATION LIFECYCLE
------------------------
Kite uses a daily OAuth2 flow (regulatory requirement, 6 AM IST expiry):

  1. Call /api/kite/login  → redirected to Zerodha login page
  2. User logs in          → redirected to /api/kite/callback?request_token=XXX
  3. Callback exchanges    → access_token stored in .kite_session.json
  4. On restart same day   → session file reloaded, validated, reused
  5. After 6 AM IST        → token expired; /api/kite/login required again

There is no refresh-token for retail accounts (NSE/BSE regulatory constraint).

INSTRUMENT CACHE
----------------
kite.instruments("NFO") downloads a ~3 MB CSV of all F&O contracts.
We cache it in memory and refresh once per day (at 6 AM when tokens renew).
The cache is the backbone of option chain construction.

OPTION CHAINS
-------------
Kite has no native /option-chain endpoint. We build it from:
  instruments cache → filter by underlying + expiry → batch kite.quote()

IV COMPUTATION
--------------
Kite does not provide Greeks. IV is computed locally via Newton-Raphson
Black-Scholes inversion (no extra dependency, only math + statistics).
"""
from __future__ import annotations

import json
import logging
import math
import os
import statistics
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── Credentials ───────────────────────────────────────────────────────────────
KITE_API_KEY    = os.getenv("KITE_API_KEY",    "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
KITE_AVAILABLE  = bool(KITE_API_KEY and KITE_API_SECRET)

# Session file — survives server restarts within the same trading day
_SESSION_FILE = Path(__file__).resolve().parent.parent.parent / ".kite_session.json"

# Rate-limit: Kite allows ~3 req/s on most endpoints; we throttle to be safe
_RATE_LIMIT_SLEEP = 0.34   # seconds between API calls
_last_call_at     = 0.0
_rate_lock        = threading.Lock()

# ── IST helpers ───────────────────────────────────────────────────────────────

def _now_ist() -> datetime:
    return datetime.now(IST)


def _token_expiry_ist() -> datetime:
    """Kite tokens expire at 6:00 AM IST the following day."""
    now = _now_ist()
    expiry = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= expiry:          # already past 6 AM today → next day
        expiry += timedelta(days=1)
    return expiry


def _token_is_valid(generated_at_iso: str) -> bool:
    """Return True if the stored token was generated after the last 6 AM IST rollover."""
    try:
        generated_at = datetime.fromisoformat(generated_at_iso).astimezone(IST)
    except (ValueError, TypeError):
        return False
    now = _now_ist()
    last_rollover = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now < last_rollover:   # before 6 AM today → rollover was yesterday
        last_rollover -= timedelta(days=1)
    return generated_at >= last_rollover


# ── Rate limiter ──────────────────────────────────────────────────────────────

def _throttle() -> None:
    global _last_call_at
    with _rate_lock:
        now = time.monotonic()
        gap = _RATE_LIMIT_SLEEP - (now - _last_call_at)
        if gap > 0:
            time.sleep(gap)
        _last_call_at = time.monotonic()


# ── Black-Scholes IV (Newton-Raphson, no extra dependencies) ──────────────────

def _bs_price(S: float, K: float, T: float, r: float, sigma: float, flag: str) -> float:
    """Black-Scholes option price."""
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if flag == "CE" else max(0.0, K - S)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    nd1, nd2 = _norm_cdf(d1), _norm_cdf(d2)
    if flag == "CE":
        return S * nd1 - K * math.exp(-r * T) * nd2
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    return S * _norm_pdf(d1) * sqrt_T


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def compute_iv(option_price: float, S: float, K: float, T: float,
               flag: str, r: float = 0.065) -> float:
    """Compute implied volatility via Newton-Raphson. Returns 0.0 on failure."""
    if T <= 1e-8 or option_price <= 0 or S <= 0 or K <= 0:
        return 0.0
    intrinsic = max(0.0, S - K) if flag == "CE" else max(0.0, K - S)
    if option_price < intrinsic:
        return 0.0
    sigma = 0.30  # initial guess
    for _ in range(200):
        price = _bs_price(S, K, T, r, sigma, flag)
        vega  = _bs_vega(S, K, T, r, sigma)
        diff  = option_price - price
        if abs(diff) < 5e-5:
            break
        if vega < 1e-10:
            break
        sigma += diff / vega
        sigma = max(1e-4, min(sigma, 20.0))
    return round(sigma * 100, 2) if 0 < sigma < 20 else 0.0  # return as %


# ── Instrument cache ──────────────────────────────────────────────────────────

class _InstrumentCache:
    """Thread-safe, auto-refreshing cache for NFO instruments."""

    def __init__(self) -> None:
        self._lock      = threading.Lock()
        self._data:   list[dict] = []
        self._loaded_at: datetime | None = None

    def _needs_refresh(self) -> bool:
        if not self._data or self._loaded_at is None:
            return True
        # Refresh after 6 AM IST (daily rollover) or if >4 hours old
        now  = _now_ist()
        last = self._loaded_at.astimezone(IST)
        if (now - last).total_seconds() > 4 * 3600:
            return True
        last_rollover = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now < last_rollover:
            last_rollover -= timedelta(days=1)
        return last < last_rollover

    def get(self, kite) -> list[dict]:
        with self._lock:
            if self._needs_refresh():
                self._refresh(kite)
            return self._data

    def _refresh(self, kite) -> None:
        try:
            logger.info("Refreshing NFO instrument cache...")
            _throttle()
            instruments = kite.instruments("NFO")
            self._data       = instruments
            self._loaded_at  = _now_ist()
            logger.info("NFO instrument cache loaded: %d contracts", len(instruments))
        except Exception as exc:
            logger.error("Failed to load NFO instruments: %s", exc)
            if not self._data:
                self._data = []

    def get_by_symbol(self, kite, underlying: str, expiry: date | None,
                      option_type: str | None) -> list[dict]:
        instruments = self.get(kite)
        result = [
            i for i in instruments
            if i.get("name", "").upper() == underlying.upper()
            and i.get("segment") == "NFO-OPT"
        ]
        if option_type:
            result = [i for i in result if i.get("instrument_type") == option_type.upper()]
        if expiry:
            result = [i for i in result if i.get("expiry") == expiry]
        return result

    def nearest_expiry(self, kite, underlying: str) -> date | None:
        instruments = self.get(kite)
        expiries = sorted({
            i["expiry"] for i in instruments
            if i.get("name", "").upper() == underlying.upper()
            and i.get("segment") == "NFO-OPT"
            and isinstance(i.get("expiry"), date)
            and i["expiry"] >= date.today()
        })
        return expiries[0] if expiries else None

    def token_for(self, kite, underlying: str, strike: float,
                  opt_type: str, expiry: date | None = None) -> int | None:
        candidates = self.get_by_symbol(kite, underlying, expiry, opt_type)
        if not candidates:
            return None
        if expiry is None:
            nearest = self.nearest_expiry(kite, underlying)
            candidates = [c for c in candidates if c.get("expiry") == nearest]
        match = min(
            (c for c in candidates if c.get("strike") is not None),
            key=lambda c: abs(c["strike"] - strike),
            default=None,
        )
        return match["instrument_token"] if match else None


_instrument_cache = _InstrumentCache()


# ── KiteSession ───────────────────────────────────────────────────────────────

class KiteSession:
    """Thread-safe Kite Connect session with file-based token persistence.

    Tokens are stored in .kite_session.json at the project root.
    On server restart within the same trading day, the saved token is reused
    without requiring a browser login.
    """

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key    = api_key
        self._api_secret = api_secret
        self._lock       = threading.Lock()
        self._kite       = None
        self._user_id    = ""
        self._connected  = False
        self._session_info: dict[str, Any] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def get_client(self):
        """Return authenticated KiteConnect instance. Thread-safe."""
        with self._lock:
            if not self._connected or self._kite is None:
                raise RuntimeError(
                    "Kite not authenticated. Visit /api/kite/login to authorise."
                )
            return self._kite

    def ensure_connected(self) -> bool:
        with self._lock:
            return self._connected

    def login_url(self) -> str:
        """Return the Zerodha browser login URL."""
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=self._api_key)
        return kite.login_url()

    def exchange_token(self, request_token: str) -> dict:
        """Exchange a one-time request_token for an access_token. Saves to disk."""
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=self._api_key)
        data = kite.generate_session(request_token, api_secret=self._api_secret)
        access_token = data["access_token"]
        kite.set_access_token(access_token)
        with self._lock:
            self._kite      = kite
            self._user_id   = data.get("user_id", "")
            self._connected = True
            self._session_info = {
                "access_token": access_token,
                "user_id":      self._user_id,
                "login_time":   str(data.get("login_time", "")),
                "generated_at": _now_ist().isoformat(),
            }
            self._save_session(self._session_info)
        logger.info("Kite session established for user %s", self._user_id)
        return data

    def try_load_saved_session(self) -> bool:
        """Load session from disk if still valid. Returns True on success."""
        try:
            if not _SESSION_FILE.exists():
                return False
            info = json.loads(_SESSION_FILE.read_text())
            if not _token_is_valid(info.get("generated_at", "")):
                logger.info("Saved Kite token expired — fresh login required")
                return False
            from kiteconnect import KiteConnect, exceptions as kex
            kite = KiteConnect(api_key=self._api_key)
            kite.set_access_token(info["access_token"])
            # Validate with a lightweight call
            profile = kite.profile()
            with self._lock:
                self._kite         = kite
                self._user_id      = profile.get("user_id", info.get("user_id", ""))
                self._connected    = True
                self._session_info = info
            logger.info("Kite session restored from disk for user %s", self._user_id)
            return True
        except Exception as exc:
            logger.warning("Could not restore saved Kite session: %s", exc)
            return False

    def status(self) -> dict:
        with self._lock:
            if not self._connected:
                return {
                    "connected":  False,
                    "configured": KITE_AVAILABLE,
                    "user_id":    "",
                    "login_url":  self.login_url() if KITE_AVAILABLE else "",
                    "message":    "Login required" if KITE_AVAILABLE else "Credentials missing",
                }
            info   = self._session_info
            gen_at = info.get("generated_at", "")
            expiry = _token_expiry_ist().isoformat() if gen_at else ""
            return {
                "connected":    True,
                "configured":   True,
                "user_id":      self._user_id,
                "generated_at": gen_at,
                "expires_at":   expiry,
                "message":      "Active",
            }

    def invalidate(self) -> None:
        """Mark session as disconnected (called when a TokenException is caught)."""
        with self._lock:
            self._connected = False
            self._kite      = None
        logger.warning("Kite session invalidated — re-login required")

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _save_session(info: dict) -> None:
        try:
            _SESSION_FILE.write_text(json.dumps(info, indent=2))
            _SESSION_FILE.chmod(0o600)   # owner read/write only
        except Exception as exc:
            logger.warning("Could not save Kite session to disk: %s", exc)


# ── Module-level singleton ────────────────────────────────────────────────────

if KITE_AVAILABLE:
    kite_session = KiteSession(KITE_API_KEY, KITE_API_SECRET)
else:
    kite_session = None   # type: ignore[assignment]


def startup_check() -> bool:
    """Called at server startup. Tries to restore saved session.

    Returns True if session is ready; False means user must visit /api/kite/login.
    """
    if not KITE_AVAILABLE:
        logger.warning("STARTUP WARN — Kite not configured (KITE_API_KEY / KITE_API_SECRET missing)")
        return False
    ok = kite_session.try_load_saved_session()
    if ok:
        # Pre-warm instrument cache in background
        threading.Thread(
            target=_warm_instrument_cache,
            daemon=True,
            name="kite-instrument-warm",
        ).start()
    else:
        logger.warning(
            "Kite session not ready — visit /api/kite/login to authorise. "
            "URL: %s", kite_session.login_url()
        )
    return ok


def _warm_instrument_cache() -> None:
    try:
        kite = kite_session.get_client()
        _instrument_cache.get(kite)
    except Exception as exc:
        logger.debug("Instrument cache warm failed: %s", exc)


# ── Token-exception guard ─────────────────────────────────────────────────────

def _with_auth(fn, *args, **kwargs):
    """Call fn(*args, **kwargs), invalidating session on auth failure."""
    from kiteconnect import exceptions as kex
    try:
        return fn(*args, **kwargs)
    except kex.TokenException:
        kite_session.invalidate()
        raise RuntimeError(
            "Kite token expired. Visit /api/kite/login to re-authenticate."
        )


# ── Market data helpers ───────────────────────────────────────────────────────

# Index symbols use "NSE:" prefix; F&O use "NFO:" prefix
_INDEX_KITE_MAP: dict[str, str] = {
    "NIFTY":    "NSE:NIFTY 50",
    "BANKNIFTY":"NSE:NIFTY BANK",
    "FINNIFTY": "NSE:NIFTY FIN SERVICE",
    "MIDCPNIFTY":"NSE:NIFTY MID SELECT",
    "SENSEX":   "BSE:SENSEX",
}
_INDEX_NFO_MAP: dict[str, str] = {
    "NIFTY":     "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "FINNIFTY":  "FINNIFTY",
    "MIDCPNIFTY":"MIDCPNIFTY",
}


def _kite_symbol(symbol: str, exchange: str = "NSE") -> str:
    """Convert plain symbol to exchange:tradingsymbol format."""
    if symbol.upper() in _INDEX_KITE_MAP:
        return _INDEX_KITE_MAP[symbol.upper()]
    return f"{exchange}:{symbol.upper()}"


def get_ltp(symbol: str) -> float | None:
    """Single-symbol LTP."""
    if not KITE_AVAILABLE:
        return None
    kite = kite_session.get_client()
    _throttle()
    key = _kite_symbol(symbol)
    data = _with_auth(kite.ltp, [key])
    q = data.get(key, {})
    return q.get("last_price")


def get_ltp_batch(symbols: list[str]) -> dict[str, float]:
    """Batch LTP for up to 1000 symbols. Returns {symbol: ltp}."""
    if not KITE_AVAILABLE or not symbols:
        return {}
    kite = kite_session.get_client()
    result: dict[str, float] = {}
    # Kite LTP allows 1000/call
    chunk_size = 900
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]
        keys  = [_kite_symbol(s) for s in chunk]
        _throttle()
        try:
            data = _with_auth(kite.ltp, keys)
            for sym, key in zip(chunk, keys):
                ltp = data.get(key, {}).get("last_price")
                if ltp is not None:
                    result[sym] = ltp
        except Exception as exc:
            logger.warning("Batch LTP failed for chunk starting %s: %s", chunk[0], exc)
    return result


def get_india_vix() -> float | None:
    """Fetch India VIX from Kite Connect (NSE:INDIA VIX)."""
    if not KITE_AVAILABLE:
        return None
    try:
        _throttle()
        data = _with_auth(kite_session.get_client().ltp, ["NSE:INDIA VIX"])
        ltp  = (data.get("NSE:INDIA VIX") or {}).get("last_price", 0)
        return round(float(ltp), 2) if ltp else None
    except Exception as exc:
        logger.warning("VIX via Kite failed: %s", exc)
        return None


def get_quote(symbol: str) -> dict | None:
    """Full quote (OHLC + depth + OI) for one symbol."""
    if not KITE_AVAILABLE:
        return None
    kite = kite_session.get_client()
    _throttle()
    key = _kite_symbol(symbol)
    data = _with_auth(kite.quote, [key])
    q    = data.get(key)
    if not q:
        return None
    depth = q.get("depth", {})
    bids  = depth.get("buy",  [{}])
    asks  = depth.get("sell", [{}])
    return {
        "symbol":     symbol,
        "ltp":        q.get("last_price", 0),
        "bid":        bids[0].get("price", 0) if bids else 0,
        "ask":        asks[0].get("price", 0) if asks else 0,
        "volume":     q.get("volume", 0),
        "oi":         q.get("oi", 0),
        "open":       q.get("ohlc", {}).get("open", 0),
        "high":       q.get("ohlc", {}).get("high", 0),
        "low":        q.get("ohlc", {}).get("low", 0),
        "close":      q.get("ohlc", {}).get("close", 0),
        "net_change": q.get("net_change", 0),
    }


def get_option_ltp(underlying: str, strike: float, opt_type: str,
                   expiry_hint: str | None = None) -> float | None:
    """Fast single-strike option LTP — used by the price monitor."""
    if not KITE_AVAILABLE:
        return None
    kite = kite_session.get_client()
    target_expiry: date | None = None
    if expiry_hint:
        try:
            target_expiry = datetime.strptime(expiry_hint, "%d%b%Y").date()
        except ValueError:
            pass
    if target_expiry is None:
        target_expiry = _instrument_cache.nearest_expiry(kite, underlying)
    if target_expiry is None:
        return None
    matches = _instrument_cache.get_by_symbol(kite, underlying, target_expiry, opt_type)
    if not matches:
        return None
    best = min(matches, key=lambda x: abs(x.get("strike", 0) - strike), default=None)
    if not best:
        return None
    key = f"NFO:{best['tradingsymbol']}"
    _throttle()
    try:
        data = _with_auth(kite.ltp, [key])
        return data.get(key, {}).get("last_price")
    except Exception as exc:
        logger.debug("Option LTP failed for %s: %s", key, exc)
        return None


def get_option_chain(symbol: str, expiry: date | None = None) -> dict:
    """Build a full option chain dict from Kite instruments + quotes.

    Returns a dict compatible with the NSE option chain structure that
    downstream scoring code expects.
    """
    if not KITE_AVAILABLE:
        return {}
    kite  = kite_session.get_client()
    name  = _INDEX_NFO_MAP.get(symbol.upper(), symbol.upper())

    if expiry is None:
        expiry = _instrument_cache.nearest_expiry(kite, name)
    if expiry is None:
        logger.warning("No expiry found for %s", symbol)
        return {}

    contracts = _instrument_cache.get_by_symbol(kite, name, expiry, None)
    if not contracts:
        logger.warning("No NFO contracts found for %s expiry %s", symbol, expiry)
        return {}

    # Get underlying LTP for IV computation
    udl_key = _INDEX_KITE_MAP.get(symbol.upper(), f"NSE:{symbol.upper()}")
    _throttle()
    try:
        udl_data = _with_auth(kite.ltp, [udl_key])
        udl_ltp  = udl_data.get(udl_key, {}).get("last_price", 0.0)
    except Exception:
        udl_ltp  = 0.0

    # Batch-quote all CE + PE contracts (up to 500 per call)
    nfo_keys = [f"NFO:{c['tradingsymbol']}" for c in contracts]
    quotes: dict[str, Any] = {}
    chunk_size = 490
    for i in range(0, len(nfo_keys), chunk_size):
        chunk = nfo_keys[i : i + chunk_size]
        _throttle()
        try:
            batch = _with_auth(kite.quote, chunk)
            quotes.update(batch)
        except Exception as exc:
            logger.warning("Quote batch failed: %s", exc)

    # Compute T (years to expiry) for IV
    days_to_expiry = max((expiry - date.today()).days, 0)
    T = days_to_expiry / 365.0

    # Build records in NSE-compatible format
    records_by_strike: dict[float, dict] = {}
    for contract in contracts:
        strike     = contract.get("strike", 0.0)
        itype      = contract.get("instrument_type", "")  # CE or PE
        key        = f"NFO:{contract['tradingsymbol']}"
        q          = quotes.get(key, {})
        ltp        = q.get("last_price",  0.0)
        oi         = q.get("oi",          0)
        oi_chg     = q.get("oi_day_high", 0) - q.get("oi_day_low", 0)
        volume     = q.get("volume",      0)
        depth      = q.get("depth",       {})
        bids       = depth.get("buy",  [{}])
        asks       = depth.get("sell", [{}])
        bid        = bids[0].get("price", 0) if bids else 0
        ask        = asks[0].get("price", 0) if asks else 0
        iv_pct     = compute_iv(ltp, udl_ltp, strike, T, itype) if udl_ltp > 0 and T > 0 else 0.0

        if strike not in records_by_strike:
            records_by_strike[strike] = {"strikePrice": strike, "expiryDate": str(expiry)}

        side_key = "CE" if itype == "CE" else "PE"
        records_by_strike[strike][side_key] = {
            "strikePrice":     strike,
            "expiryDate":      str(expiry),
            "lastPrice":       ltp,
            "openInterest":    oi,
            "changeinOpenInterest": oi_chg,
            "totalTradedVolume": volume,
            "impliedVolatility": iv_pct,
            "bidprice":        bid,
            "askPrice":        ask,
            "change":          q.get("net_change", 0),
            "pChange":         0,
            "lotSize":         contract.get("lot_size", 1),
        }

    records_list = sorted(records_by_strike.values(), key=lambda r: r["strikePrice"])
    return {
        "records": {
            "expiryDates": [str(expiry)],
            "data":        records_list,
            "underlyingValue": udl_ltp,
            "timestamp":   _now_ist().isoformat(),
        },
        "filtered": {
            "data":           records_list,
            "underlyingValue": udl_ltp,
        },
        "_source": "kite",
        "_expiry": str(expiry),
    }


def get_intraday_candles(symbol: str, interval: str = "5minute") -> list[dict]:
    """Fetch today's intraday OHLCV candles.

    interval: "minute", "3minute", "5minute", "10minute", "15minute",
              "30minute", "60minute"
    Returns list of dicts with keys: date, open, high, low, close, volume
    """
    if not KITE_AVAILABLE:
        return []
    kite  = kite_session.get_client()
    name  = _INDEX_NFO_MAP.get(symbol.upper(), symbol.upper())
    # For indices use NSE spot token; for stocks use NSE equity
    instr = _get_equity_instrument_token(kite, symbol)
    if instr is None:
        return []
    today = date.today()
    # Market opens at 9:15 IST
    from_dt = datetime(today.year, today.month, today.day, 9, 15, 0)
    to_dt   = datetime(today.year, today.month, today.day, 15, 30, 0)
    _throttle()
    try:
        candles = _with_auth(
            kite.historical_data,
            instr, from_dt, to_dt, interval, False, True
        )
        return candles
    except Exception as exc:
        logger.warning("Intraday candles failed for %s: %s", symbol, exc)
        return []


def get_daily_ohlcv(symbol: str, days: int = 400) -> list[dict]:
    """Fetch daily OHLCV for technical indicator computation (EMA200 etc.).

    Returns list of dicts: date, open, high, low, close, volume, oi
    """
    if not KITE_AVAILABLE:
        return []
    kite  = kite_session.get_client()
    instr = _get_equity_instrument_token(kite, symbol)
    if instr is None:
        return []
    to_dt   = date.today()
    from_dt = to_dt - timedelta(days=days + 10)   # buffer for weekends
    _throttle()
    try:
        candles = _with_auth(
            kite.historical_data,
            instr, from_dt, to_dt, "day", False, True
        )
        return candles[-days:]
    except Exception as exc:
        logger.warning("Daily OHLCV failed for %s: %s", symbol, exc)
        return []


# ── F&O Universe ──────────────────────────────────────────────────────────────

# Tier-1 liquid F&O universe — same list as Angel One for continuity
_FO_UNIVERSE: list[str] = [
    # Index
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    # Banking & Finance
    "HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK", "SBIN", "INDUSINDBK",
    "BAJFINANCE", "BAJAJFINSV", "HDFCLIFE", "CHOLAFIN",
    # IT
    "INFY", "TCS", "WIPRO", "HCLTECH", "TECHM", "LTIM", "PERSISTENT",
    # Oil & Gas
    "RELIANCE", "ONGC", "BPCL",
    # Telecom
    "BHARTIARTL",
    # Auto
    "MARUTI", "EICHERMOT", "M&M", "TATAMOTORS", "BAJAJ-AUTO",
    # Pharma
    "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "AUROPHARMA",
    # FMCG
    "HINDUNILVR", "NESTLEIND", "TATACONSUM", "ITC", "BRITANNIA",
    # Infra
    "LT", "ADANIPORTS", "POWERGRID", "NTPC",
    # Metals
    "JSWSTEEL", "TATASTEEL", "HINDALCO", "VEDL",
    # Others
    "COALINDIA", "ULTRACEMCO", "ASIANPAINT", "TITAN",
    "APOLLOHOSP", "ZOMATO", "BEL", "DLF",
]


def get_fo_universe() -> list[str]:
    return list(_FO_UNIVERSE)


def get_lot_sizes() -> dict[str, int]:
    """Derive F&O lot sizes from the NFO instrument cache — zero extra API call."""
    if not KITE_AVAILABLE:
        return {}
    try:
        kite  = kite_session.get_client()
        sizes: dict[str, int] = {}
        for inst in _instrument_cache.get(kite):
            name = inst.get("name", "").upper()
            lot  = inst.get("lot_size", 0)
            if name and lot and name not in sizes:
                sizes[name] = int(lot)
        return sizes
    except Exception as exc:
        logger.warning("Lot sizes from Kite failed: %s", exc)
        return {}


# ── Equity instrument token lookup ────────────────────────────────────────────

_INDEX_TOKENS: dict[str, int] = {}   # populated lazily from NSE instruments


def _get_equity_instrument_token(kite, symbol: str) -> int | None:
    """Get instrument_token for an NSE equity or index symbol."""
    global _INDEX_TOKENS
    sym_upper = symbol.upper()
    if sym_upper in _INDEX_TOKENS:
        return _INDEX_TOKENS[sym_upper]
    try:
        _throttle()
        instruments = _with_auth(kite.instruments, "NSE")
        mapping = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}
        _INDEX_TOKENS.update(mapping)
        token = mapping.get(sym_upper)
        # Index aliases
        if token is None:
            alias_map = {
                "NIFTY": "NIFTY 50",
                "BANKNIFTY": "NIFTY BANK",
                "FINNIFTY": "NIFTY FIN SERVICE",
                "MIDCPNIFTY": "NIFTY MID SELECT",
            }
            alias = alias_map.get(sym_upper)
            if alias:
                token = mapping.get(alias)
        return token
    except Exception as exc:
        logger.warning("Could not resolve token for %s: %s", symbol, exc)
        return None


# ── VWAP / POC helpers (same interface as angel.py) ───────────────────────────

def compute_vwap(candles: list[dict]) -> float:
    """Compute VWAP from intraday candles."""
    total_vol = sum(c.get("volume", 0) for c in candles)
    if total_vol == 0:
        return 0.0
    weighted = sum(
        ((c.get("high", 0) + c.get("low", 0) + c.get("close", 0)) / 3)
        * c.get("volume", 0)
        for c in candles
    )
    return round(weighted / total_vol, 2)


def compute_poc_from_candles(candles: list[dict]) -> float:
    """Point of Control — price level with highest traded volume."""
    if not candles:
        return 0.0
    best = max(candles, key=lambda c: c.get("volume", 0))
    return round((best.get("high", 0) + best.get("low", 0) + best.get("close", 0)) / 3, 2)
