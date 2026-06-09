import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date, datetime
from zoneinfo import ZoneInfo

# Max seconds to wait for a single option chain fetch before skipping that symbol.
# At 15s, a 12-symbol scan completes in ≤180s worst-case; most symbols finish in 5-10s.
_OC_TIMEOUT_SECS = 15

import requests

try:
    from jugaad_data.nse import NSELive as _NSELive
    _JUGAAD = True
except ImportError:
    _JUGAAD = False

try:
    import yfinance as yf
    _YFINANCE = True
except ImportError:
    _YFINANCE = False

try:
    import ta
    _TA = True
except ImportError:
    _TA = False

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_NSE_BASE = "https://www.nseindia.com"
_NSE_API = f"{_NSE_BASE}/api"

_HEADERS = {
    "Host": "www.nseindia.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 11.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.6998.166 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=SBIN",
    "X-Requested-With": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "Sec-CH-UA": '"Google Chrome";v="134", "Chromium";v="134", "Not?A_Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "DNT": "1",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}

# Warmup page: visit a working NSE page to acquire session cookies.
# The main homepage (nseindia.com) is Akamai-protected and returns 403 for automated clients.
# The get-quotes page is less restricted and successfully sets nsit/nseappid cookies.
_NSE_WARMUP_URL = "https://www.nseindia.com/get-quotes/equity?symbol=LT"

# yfinance ticker overrides — indices and symbols whose NSE name ≠ Yahoo Finance ticker.
# All other symbols auto-resolve as SYMBOL.NS (e.g. RELIANCE → RELIANCE.NS).
_YF_TICKER_MAP = {
    "NIFTY":       "^NSEI",
    "BANKNIFTY":   "^NSEBANK",
    "FINNIFTY":    "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY":  "NIFTY_MIDCAP_SELECT.NS",
    "NIFTYNXT50":  "NIFTY_NEXT_50.NS",
    # TATAMOTORS demerged Oct 2025
    "TMPV":        "TMPV.NS",   # Tata Motors Passenger Vehicles
    "TMCV":        "TMCV.NS",   # Tata Motors Commercial Vehicles
}

# Fallback lot sizes used when the live bhavcopy CSV is unavailable.
# SEBI revises these quarterly — the live fetch in get_lot_sizes() stays current.
_LOT_SIZE_FALLBACK = {
    # Indices
    "NIFTY": 75, "BANKNIFTY": 15, "FINNIFTY": 40,
    "MIDCPNIFTY": 75, "NIFTYNXT50": 25,
    # Large-cap F&O — most liquid, highest daily option turnover
    "RELIANCE": 250, "HDFCBANK": 550, "ICICIBANK": 700,
    "INFY": 300, "TCS": 150, "AXISBANK": 1200, "SBIN": 1500,
    "KOTAKBANK": 400, "LT": 375, "WIPRO": 2400,
    "BAJFINANCE": 125,
    # TATAMOTORS demerged Oct 2025 → two successor entities
    "TMPV": 1425,   # Tata Motors Passenger Vehicles (JLR + cars)
    "TMCV": 2800,   # Tata Motors Commercial Vehicles
    # Mid/large-cap additions for a wider scan universe
    "BHARTIARTL": 950, "HCLTECH": 700, "TECHM": 600,
    "MARUTI": 15,  "M&M": 700,      "EICHERMOT": 100,
    "SUNPHARMA": 700, "DRREDDY": 125, "CIPLA": 650,
    "HINDUNILVR": 300, "ASIANPAINT": 200, "TITAN": 375,
    "ADANIPORTS": 1250, "ADANIENT": 350,
    "JSWSTEEL": 600, "TATASTEEL": 5500, "HINDALCO": 2150,
    "NTPC": 3000, "POWERGRID": 4800, "ONGC": 3850,
    "ULTRACEMCO": 100, "GRASIM": 475, "INDUSINDBK": 500,
    "BAJAJFINSV": 125, "HDFCLIFE": 1100,
    "DIVISLAB": 200, "PIDILITIND": 200,
}

# Controls jugaad-data routing: index_option_chain vs equities_option_chain.
# FINNIFTY uses index routing but is excluded from _NSE_DEFAULT_SYMBOLS (NSE scraping ~124s).
# MIDCPNIFTY / NIFTYNXT50 can be added to _NSE_DEFAULT_SYMBOLS when verified reliable.
INDEX_SYMBOLS    = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
_FIXED_WATCHLIST = ["NIFTY", "BANKNIFTY"]  # always first in any dynamic list

# URL for NSE's official F&O lot-size file (updated each expiry cycle)
_LOT_SIZE_CSV_URL = "https://archives.nseindia.com/content/fo/fo_mktlots.csv"

# F&O securities index used to rank stocks by liquidity
_FO_INDEX_URL_PARAM = "SECURITIES%20IN%20F%26O"

# NSE chart API index name for each instrument.
# Indices use a different name format than their ticker; stocks use the symbol directly.
_NSE_CHART_INDEX_MAP = {
    "NIFTY":     ("NIFTY50",   True),
    "BANKNIFTY": ("BANKNIFTY", True),
    "FINNIFTY":  ("FINNIFTY",  True),
    # All F&O stocks → (symbol, False) — resolved dynamically in the method
}


# NSE intraday cumulative volume distribution (minutes from 9:15 → fraction of daily vol).
# Indian markets follow a pronounced U-shape: heavy at open (discovery, gap-fills) and
# close (institutional rebalancing), thin midday.  Using a uniform linear projection at
# 11 AM would assume only 32% of daily vol has been traded; the real figure is ~43%.
# Correcting for this prevents false-high RelVol readings in the opening hour and
# false-low readings during midday thin periods.
_VOL_PROFILE = [
    (0,   0.00),   # 9:15 market open
    (45,  0.28),   # 10:00 — first 45 min: ~28% of daily vol (discovery + gap fills)
    (105, 0.43),   # 11:00
    (165, 0.53),   # 12:00
    (225, 0.61),   # 13:00
    (285, 0.69),   # 14:00
    (315, 0.78),   # 14:30
    (375, 1.00),   # 15:30 close
]


def _expected_vol_fraction(elapsed_minutes: float) -> float:
    """Interpolate expected cumulative volume fraction from NSE U-shape profile."""
    elapsed_minutes = max(1.0, min(375.0, elapsed_minutes))
    for i in range(len(_VOL_PROFILE) - 1):
        t0, f0 = _VOL_PROFILE[i]
        t1, f1 = _VOL_PROFILE[i + 1]
        if t0 <= elapsed_minutes <= t1:
            return max(f0 + (f1 - f0) * (elapsed_minutes - t0) / (t1 - t0), 0.01)
    return 1.0


def _bs_greeks(spot: float, strike: float, dte: float, iv_pct: float, opt_type: str) -> dict:
    """Black-Scholes Delta, Theta (per calendar day), Vega (per 1% IV move).
    Uses only the standard library — no scipy dependency.
    """
    _SAFE = {"delta": 0.0, "theta": 0.0, "vega": 0.0}
    try:
        T     = max(float(dte), 0.01) / 365.0
        sigma = max(float(iv_pct), 0.1) / 100.0
        S, K  = float(spot), float(strike)
        r     = 0.07  # India 10-yr bond proxy

        if S <= 0 or K <= 0:
            return _SAFE

        sqrt_T = math.sqrt(T)
        d1     = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
        d2     = d1 - sigma * sqrt_T

        def _cdf(x):
            return 0.5 * (1 + math.erf(x / math.sqrt(2)))

        def _pdf(x):
            return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

        Nd1 = _cdf(d1)
        Nd2 = _cdf(d2)
        nd1 = _pdf(d1)

        if opt_type == "CE":
            delta = Nd1
            theta = (-(S * nd1 * sigma) / (2 * sqrt_T) - r * K * math.exp(-r * T) * Nd2) / 365
        else:  # PE
            delta = Nd1 - 1
            theta = (-(S * nd1 * sigma) / (2 * sqrt_T) + r * K * math.exp(-r * T) * (1 - Nd2)) / 365

        vega = S * nd1 * sqrt_T / 100  # per 1% IV change

        return {
            "delta": round(delta, 3),
            "theta": round(theta, 2),
            "vega":  round(vega, 2),
        }
    except Exception:
        return _SAFE


def _supertrend_direction(high, low, close, period: int = 7, multiplier: float = 3.0):
    """Returns 1 (bullish), -1 (bearish), or None if data is insufficient/errored."""
    if not _TA or len(close) < period + 2:
        return None
    try:
        atr = ta.volatility.AverageTrueRange(high, low, close, window=period).average_true_range().values
        hl2 = ((high + low) / 2).values
        c   = close.values
        raw_upper = hl2 + multiplier * atr
        raw_lower = hl2 - multiplier * atr
        fu = raw_upper.copy()
        fl = raw_lower.copy()
        st = [1] * len(c)
        for i in range(1, len(c)):
            fu[i] = raw_upper[i] if (raw_upper[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
            fl[i] = raw_lower[i] if (raw_lower[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]
            if   c[i] > fu[i-1]: st[i] = 1
            elif c[i] < fl[i-1]: st[i] = -1
            else:                 st[i] = st[i-1]
        return int(st[-1])
    except Exception:
        return None


class NSEDataSource:
    """Live NSE data source with 5-minute cache and graceful fallbacks."""

    # Cache TTLs — option chain prices move every second; indicators are slow
    _TTL_OPTION_CHAIN = 60    # 60 sec — serial NSE fetches; fresh enough for manual scans
    _TTL_INTRADAY     = 60    # 60 sec — real-time closes for RSI/MACD
    _TTL_SLOW         = 300   # 5 min  — VIX, breadth, lot sizes, watchlist

    def __init__(self):
        self._session: requests.Session | None = None
        self._session_at: float = 0.0
        self._cache: dict = {}
        self._session_lock = threading.Lock()
        self._jugaad: object | None = None
        self.last_nifty_direction: str | None = None  # set by get_live_candidates each scan

    # ── HTTP session ──────────────────────────────────────────────────────────

    def _get_session(self) -> requests.Session:
        # Double-checked locking: skip lock acquisition when session is already fresh.
        if self._session is not None and time.time() - self._session_at <= 1800:
            return self._session
        with self._session_lock:
            if self._session is None or time.time() - self._session_at > 1800:
                s = requests.Session()
                s.headers.update(_HEADERS)
                try:
                    # Warmup via get-quotes page (sets nsit/nseappid cookies).
                    # The main homepage returns 403 for automated clients (Akamai-protected).
                    s.get(_NSE_WARMUP_URL, timeout=15)
                    time.sleep(0.3)
                except Exception as exc:
                    logger.debug("NSE session warmup failed: %s", exc)
                self._session = s
                self._session_at = time.time()
        return self._session

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        url = f"{_NSE_API}/{path}"
        for attempt in range(2):
            try:
                resp = self._get_session().get(url, params=params, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.debug("NSE %s attempt %d failed: %s", path, attempt + 1, exc)
                self._session = None
        return None

    def _cached(self, key: str, fetch_fn, ttl: int | None = None):
        effective_ttl = ttl if ttl is not None else self._TTL_SLOW
        entry = self._cache.get(key)
        if entry and time.time() - entry["ts"] < effective_ttl:
            return entry["data"]
        data = fetch_fn()
        if data is not None:
            self._cache[key] = {"data": data, "ts": time.time()}
        return data

    # ── live market data ──────────────────────────────────────────────────────

    def get_india_vix(self) -> float:
        def fetch():
            data = self._get("allIndices")
            if not data:
                return None
            for item in data.get("data", []):
                if "INDIA VIX" in item.get("index", "").upper():
                    try:
                        return round(float(item["last"]), 2)
                    except (ValueError, TypeError):
                        pass
            return None

        result = self._cached("vix", fetch)
        return result if result is not None else 14.5

    def get_option_chain(self, symbol: str) -> dict | None:
        nse_symbol = "NIFTY FIN SERVICE" if symbol == "FINNIFTY" else symbol

        def fetch():
            # ── Primary: Angel One SmartAPI (fast, no Akamai blocking) ────────
            try:
                from app.data_sources.angel import (
                    get_option_chain as angel_oc, ANGEL_AVAILABLE,
                )
                if ANGEL_AVAILABLE:
                    data = angel_oc(symbol)
                    if data and data.get("records", {}).get("data"):
                        logger.debug("OC source: Angel One [%s]", symbol)
                        return data
            except Exception as exc:
                logger.debug("Angel One OC %s failed, falling back to NSE: %s", symbol, exc)

            # ── Fallback: jugaad-data (NSE scraping via Akamai workaround) ────
            if _JUGAAD:
                try:
                    if self._jugaad is None:
                        self._jugaad = _NSELive()
                    client = self._jugaad
                    if symbol in INDEX_SYMBOLS:
                        data = client.index_option_chain(symbol)
                    else:
                        data = client.equities_option_chain(symbol)
                    if data and data.get("records", {}).get("data"):
                        logger.debug("OC source: jugaad-data [%s]", symbol)
                        return data
                    self._jugaad = None
                except Exception as exc:
                    logger.debug("jugaad-data OC %s failed: %s", symbol, exc)
                    self._jugaad = None

            # ── Last resort: direct NSE API ────────────────────────────────────
            endpoint = "option-chain-indices" if symbol in INDEX_SYMBOLS else "option-chain-equities"
            return self._get(endpoint, params={"symbol": nse_symbol})

        return self._cached(f"oc_{symbol}", fetch, ttl=self._TTL_OPTION_CHAIN)

    # ── dynamic helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _to_yf_symbol(symbol: str) -> str:
        """Convert NSE symbol to yfinance ticker.
        Indices need explicit mapping; all F&O stocks are simply SYMBOL.NS."""
        return _YF_TICKER_MAP.get(symbol, f"{symbol}.NS")

    @staticmethod
    def _nearest_expiry(expiries: list[str]) -> str:
        """Return the nearest expiry that is today or later.
        Falls back to expiries[0] if none parse or all are in the past."""
        today = _date.today()
        for exp in expiries:
            try:
                if datetime.strptime(exp, "%d-%b-%Y").date() >= today:
                    return exp
            except ValueError:
                continue
        return expiries[0]

    def _fetch_index_constituents(self) -> list[str]:
        """Pull stock symbols from major NSE index pages.
        Returns a ranked list ordered by totalTradedValue (highest first)."""
        indices = [
            "NIFTY 50", "NIFTY BANK", "NIFTY FINANCIAL SERVICES",
            "NIFTY MIDCAP SELECT", "NIFTY IT", "NIFTY NEXT 50",
        ]
        seen: set[str] = set()
        ranked: list[tuple[float, str]] = []
        for idx_name in indices:
            try:
                data = self._get("equity-stockIndices", params={"index": idx_name})
                if not data:
                    continue
                for row in data.get("data", []):
                    sym = row.get("symbol", "")
                    if not sym or sym in seen or sym in INDEX_SYMBOLS:
                        continue
                    # Skip index summary rows (no series or priceBand field)
                    if not row.get("series") and not row.get("priceBand"):
                        continue
                    seen.add(sym)
                    val = float(row.get("totalTradedValue") or 0)
                    ranked.append((val, sym))
            except Exception as exc:
                logger.debug("Index constituent fetch [%s] failed: %s", idx_name, exc)

        if ranked:
            ranked.sort(reverse=True)
            logger.info("Index constituents fetched: %d symbols", len(ranked))
            return [sym for _, sym in ranked]
        return []

    def _fetch_most_active_fo(self) -> list[str]:
        """Return F&O symbols ranked by current OI build-up (OI spurts).
        Uses NSE live-analysis endpoint; gracefully returns [] on failure."""
        symbols: list[str] = []
        for index_type in ("OPTSTK", "OPTIDX"):
            try:
                data = self._get(
                    "live-analysis-variations",
                    params={"index": index_type, "type": "OiGainers"},
                )
                if not data:
                    continue
                for row in (data.get("data") or []):
                    sym = row.get("symbol") or row.get("underlying", "")
                    if sym and sym not in symbols:
                        symbols.append(sym)
            except Exception as exc:
                logger.debug("Most-active F&O [%s] failed: %s", index_type, exc)
        return symbols

    def _build_dynamic_universe(self, max_symbols: int = 12) -> list[str]:
        """Build live scan universe: index constituents + OI spurts + lot-size filter.
        Falls back to _NSE_DEFAULT_SYMBOLS when NSE endpoints are unreachable."""
        def fetch():
            lot_syms = set(self.get_lot_sizes().keys())

            # Pull OI-spurt symbols first (highest conviction for a scanner)
            oi_active = [s for s in self._fetch_most_active_fo() if s in lot_syms]

            # Pull index constituents ranked by traded value
            constituents = [s for s in self._fetch_index_constituents() if s in lot_syms]

            # Merge: OI-spurt symbols first, then highest-value constituents
            seen: set[str] = set()
            merged: list[str] = []
            for s in oi_active + constituents:
                if s not in seen:
                    seen.add(s)
                    merged.append(s)

            if merged:
                # Always pin NIFTY + BANKNIFTY first, then top stocks by traded value
                stocks = [s for s in merged if s not in set(_FIXED_WATCHLIST)]
                n_stocks = max_symbols - len(_FIXED_WATCHLIST)
                return _FIXED_WATCHLIST + stocks[:n_stocks]
            return None

        result = self._cached("dynamic_universe", fetch, ttl=1800)  # refresh every 30 min
        if result:
            return result

        logger.info("Dynamic universe unavailable — using static fallback (%d symbols)",
                    len(self._NSE_DEFAULT_SYMBOLS))
        return list(self._NSE_DEFAULT_SYMBOLS[:max_symbols])

    def get_lot_sizes(self) -> dict[str, int]:
        """Fetch current F&O lot sizes from the NSE bhavcopy CSV.
        Falls back to _LOT_SIZE_FALLBACK if the file is unreachable."""
        def fetch():
            try:
                resp = requests.get(_LOT_SIZE_CSV_URL, timeout=15, headers={"User-Agent": _HEADERS["User-Agent"]})
                resp.raise_for_status()
                sizes: dict[str, int] = {}
                lines = resp.text.splitlines()
                # Skip header rows until we find a line starting with a known symbol
                for line in lines:
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 3:
                        continue
                    sym = parts[0].strip().upper()
                    if not sym or sym in ("SYMBOL", ""):
                        continue
                    # Most recent lot size is in the last non-empty numeric column
                    for val in reversed(parts[2:]):
                        val = val.strip()
                        if val.isdigit() and int(val) > 0:
                            sizes[sym] = int(val)
                            break
                return sizes if sizes else None
            except Exception as exc:
                logger.debug("Lot size CSV fetch failed: %s", exc)
                return None

        result = self._cached("lot_sizes", fetch)
        if not result:
            return _LOT_SIZE_FALLBACK.copy()
        # Merge: live data wins; fallback fills any gaps
        merged = _LOT_SIZE_FALLBACK.copy()
        merged.update(result)
        return merged

    def get_fo_watchlist(self, top_n: int = 20) -> list[str]:
        """Return top_n most-liquid F&O stocks (by traded value) plus the 3 main indices.
        Falls back to the fallback lot-size keys if NSE is offline."""
        def fetch():
            data = self._get("equity-stockIndices", params={"index": "SECURITIES IN F&O"})
            if not data:
                return None
            rows = data.get("data", [])
            # Sort by totalTradedValue descending; exclude index rows (no series field or series != "EQ")
            stocks = [
                r for r in rows
                if r.get("series", "EQ") == "EQ" and r.get("symbol")
            ]
            stocks.sort(key=lambda r: float(r.get("totalTradedValue") or 0), reverse=True)
            return [r["symbol"] for r in stocks[:top_n]]

        top_stocks = self._cached(f"fo_watchlist_{top_n}", fetch)
        if not top_stocks:
            # Offline fallback: use whatever symbols are in the lot-size table
            top_stocks = [s for s in _LOT_SIZE_FALLBACK if s not in INDEX_SYMBOLS][:top_n]

        # Indices always lead; deduplicate while preserving order
        seen: set[str] = set(_FIXED_WATCHLIST)
        ordered = list(_FIXED_WATCHLIST)
        for sym in top_stocks:
            if sym not in seen:
                seen.add(sym)
                ordered.append(sym)
        return ordered

    def get_ohlcv_daily(self, symbol: str, period: str = "1y"):
        """200-day daily OHLCV — SQLite cache first, yfinance as fetch source.

        Cache hit:  instant, zero network calls, safe on rate-limited days.
        Cache miss: fetch from yfinance with exponential backoff, store result.
        Stale fallback: if yfinance fails but cache is <10 days old, serve it
                        so a temporary Yahoo outage doesn't break every scan.
        """
        import time
        import pandas as pd
        from app.services.storage import get_ohlcv_cache, set_ohlcv_cache

        def _rows_to_df(rows: list[dict]) -> pd.DataFrame:
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            return df.set_index("date").rename_axis("Date")

        # ── 1. Fresh cache ───────────────────────────────────────────────────
        cached = get_ohlcv_cache(symbol)
        if cached is not None:
            logger.debug("OHLCV cache hit: %s (%d rows)", symbol, len(cached))
            return _rows_to_df(cached)

        # ── 2. Angel One daily OHLCV (primary — no rate limit issues, no yfinance) ─
        try:
            from app.data_sources.angel import get_daily_ohlcv as _angel_daily, ANGEL_AVAILABLE
            if ANGEL_AVAILABLE:
                df_angel = _angel_daily(symbol, days=400)
                if df_angel is not None and len(df_angel) >= 60:
                    rows = [
                        {
                            "date":   str(idx.date()),
                            "Open":   float(row["Open"]),
                            "High":   float(row["High"]),
                            "Low":    float(row["Low"]),
                            "Close":  float(row["Close"]),
                            "Volume": float(row["Volume"]),
                        }
                        for idx, row in df_angel.iterrows()
                    ]
                    set_ohlcv_cache(symbol, rows)
                    logger.debug("OHLCV cached from Angel One: %s (%d rows)", symbol, len(rows))
                    return df_angel
        except Exception as exc:
            logger.debug("Angel One daily OHLCV failed for %s: %s — falling back to yfinance", symbol, exc)

        # ── 3. Fetch from yfinance with retry/backoff ────────────────────────
        if not _YFINANCE:
            return None

        yf_sym = self._to_yf_symbol(symbol)
        df = None
        for attempt in range(3):
            try:
                df = yf.Ticker(yf_sym).history(period=period, interval="1d")
                if not df.empty and len(df) >= 60:
                    break
                df = None
                break
            except Exception as exc:
                msg = str(exc)
                if ("429" in msg or "Too Many Requests" in msg or "Rate limit" in msg) and attempt < 2:
                    wait = 2 ** (attempt + 1)   # 2s → 4s
                    logger.debug("yfinance rate-limited for %s, retry in %ds", symbol, wait)
                    time.sleep(wait)
                    continue
                logger.debug("yfinance daily %s failed: %s", symbol, exc)
                # ── 3. Serve stale cache as emergency fallback ───────────────
                stale = get_ohlcv_cache(symbol, min_rows=60, max_stale_days=10)
                if stale:
                    logger.warning("OHLCV stale cache fallback: %s", symbol)
                    return _rows_to_df(stale)
                return None

        if df is None or df.empty:
            return None

        # ── 4. Persist to cache ──────────────────────────────────────────────
        rows = [
            {
                "date":   str(idx.date()),
                "Open":   float(row["Open"]),
                "High":   float(row["High"]),
                "Low":    float(row["Low"]),
                "Close":  float(row["Close"]),
                "Volume": float(row["Volume"]),
            }
            for idx, row in df.iterrows()
        ]
        set_ohlcv_cache(symbol, rows)
        logger.debug("OHLCV cached: %s (%d rows)", symbol, len(rows))
        return df

    def get_intraday_closes(self, symbol: str):
        """Real-time intraday 1-min close prices from NSE chart API.
        Used for EMA20/50, RSI, MACD — indicators that must reflect the live market.
        Falls back to None if NSE chart is unreachable (caller uses daily as fallback)."""
        chart_name, is_index = _NSE_CHART_INDEX_MAP.get(symbol, (symbol, False))

        def fetch():
            try:
                resp = self._get_session().get(
                    f"{_NSE_API}/chart-databyindex",
                    params={"index": chart_name, "indices": str(is_index).lower()},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                points = data.get("grapthData") or data.get("graphData") or []
                if not points:
                    return None
                import pandas as pd
                closes = pd.Series(
                    [float(p[1]) for p in points],
                    index=pd.to_datetime([p[0] for p in points], unit="ms", utc=True),
                    name="Close",
                )
                return closes.dropna() if not closes.empty else None
            except Exception as exc:
                logger.debug("NSE chart %s failed: %s", symbol, exc)
                return None

        return self._cached(f"intraday_{symbol}", fetch, ttl=self._TTL_INTRADAY)

    # ── technical indicators ──────────────────────────────────────────────────

    def _compute_indicators(self, df_daily, intraday_closes=None,
                            symbol: str = "", angel_candles=None) -> dict | None:
        """Compute all technical indicators using a 3-timeframe model.

        Timeframe model (research: 15-min is optimal for Indian F&O signals):
          5-min  (Angel candles)  → VWAP, spot price, gap detection
          15-min (resampled)      → RSI(14), MACD(12,26,9), EMA20, tf15 confluence
          30-min (resampled)      → EMA5/EMA10 trend gate (tf30)
          Daily  (yfinance cache) → EMA50, EMA200, ADX, ATR, Supertrend, RelVol

        Why 15-min for RSI/MACD:
          5-min RSI(14)  = 70-min lookback  → too noisy, extremely reactive
          15-min RSI(14) = 210-min lookback → stable, institutional-grade signal
          15-min MACD has ~3x fewer false crossovers than 5-min MACD

        Single getCandleData(FIVE_MINUTE) call covers all 3 intraday timeframes
        via pandas resample — no extra API calls.
        """
        if not _TA or df_daily is None or len(df_daily) < 52:
            return None
        try:
            import pandas as pd

            close_d = df_daily["Close"]
            high_d  = df_daily["High"]
            low_d   = df_daily["Low"]

            # ── Slow indicators — always from daily OHLCV ─────────────────────
            ema200 = (
                ta.trend.EMAIndicator(close_d, window=200).ema_indicator().iloc[-1]
                if len(close_d) >= 200 else
                ta.trend.EMAIndicator(close_d, window=50).ema_indicator().iloc[-1]
            )
            ema50_d     = ta.trend.EMAIndicator(close_d, window=50).ema_indicator().iloc[-1]
            _adx_s      = ta.trend.ADXIndicator(high_d, low_d, close_d).adx().dropna()
            adx_val     = float(_adx_s.iloc[-1]) if len(_adx_s) > 0 else 0.0
            adx_rising  = len(_adx_s) >= 2 and float(_adx_s.iloc[-1]) > float(_adx_s.iloc[-2])
            atr_val     = ta.volatility.AverageTrueRange(high_d, low_d, close_d).average_true_range().iloc[-1]
            avg_vol_raw = df_daily["Volume"].rolling(20).mean().iloc[-1]
            avg_vol     = float(avg_vol_raw) if pd.notna(avg_vol_raw) else 0.0
            last_vol    = float(df_daily["Volume"].iloc[-1])
            # rel_vol computed here as daily fallback; overwritten below when
            # angel_candles are available (today's intraday volume is more relevant).
            rel_vol     = round(last_vol / avg_vol, 2) if avg_vol > 0 and pd.notna(last_vol) else 1.0
            prev_high   = float(high_d.iloc[-2]) if len(high_d) >= 2 else float(high_d.iloc[-1])
            prev_low    = float(low_d.iloc[-2])  if len(low_d)  >= 2 else float(low_d.iloc[-1])
            prev_close  = float(close_d.iloc[-2]) if len(close_d) >= 2 else float(close_d.iloc[-1])
            st_dir      = _supertrend_direction(high_d, low_d, close_d)

            # ── Multi-timeframe intraday analysis ─────────────────────────────
            # All three intraday timeframes derived from a single 5-min Angel fetch.
            data_age  = "daily-fallback"
            spot      = float(close_d.iloc[-1])
            ema20     = float(ema50_d)   # placeholder — overwritten below
            rsi       = 50.0
            macd_val  = 0.0
            macd_sig  = 0.0
            macd_hist           = 0.0
            macd_hist_expanding = False
            st15_dir  = None
            or_high   = None
            or_low    = None
            tf5_bull  = False
            tf5_bear  = False
            rsi5      = 50.0
            tf10_bull = False
            tf10_bear = False
            rsi10     = 50.0
            tf15_bull = False
            tf15_bear = False
            tf30_bull = False
            tf30_bear = False
            vwap      = None
            vwap_bullish = None
            today_open   = None
            gap_up = gap_down = False
            gap_pct = 0.0

            if angel_candles is not None and len(angel_candles) >= 10:
                # ── 5-min base ───────────────────────────────────────────────
                c5 = angel_candles.copy()
                if "datetime" in c5.columns:
                    c5 = c5.set_index("datetime")
                c5.index = pd.to_datetime(c5.index)

                spot   = float(c5["close"].iloc[-1])
                close5 = c5["close"]

                # Today's volume vs 20-day average — U-shape corrected.
                # NSE volume follows a U-shape (heavy open/close, thin midday).
                # Linear projection (vol/hours * 6.25) overestimates in the morning
                # and underestimates at midday.  Instead, divide by the expected
                # cumulative fraction at the current time-of-day using _VOL_PROFILE.
                # A "normal volume day" always returns rel_vol ≈ 1.0 regardless of
                # when during the session the scan runs.
                if avg_vol > 0:
                    intra_vol     = float(c5["volume"].sum())
                    now_ist_local = datetime.now(IST)
                    market_open   = now_ist_local.replace(hour=9, minute=15, second=0, microsecond=0)
                    elapsed_min   = max((now_ist_local - market_open).total_seconds() / 60, 1.0)
                    expected_frac = _expected_vol_fraction(elapsed_min)
                    projected_vol = intra_vol / expected_frac
                    rel_vol       = round(projected_vol / avg_vol, 2)

                # 5-min EMA9/21 — captured for research comparison vs 15-min
                tf5_bull = False
                tf5_bear = False
                if len(close5) >= 9:
                    e9_5  = float(close5.ewm(span=9,  adjust=False).mean().iloc[-1])
                    e21_5 = float(close5.ewm(span=21, adjust=False).mean().iloc[-1])
                    tf5_bull = e9_5 > e21_5
                    tf5_bear = e9_5 < e21_5
                rsi5 = 50.0
                if len(close5) >= 15:
                    rsi5_s = ta.momentum.RSIIndicator(close5, window=14).rsi().dropna()
                    rsi5   = float(rsi5_s.iloc[-1]) if len(rsi5_s) > 0 else 50.0

                # ── 15-min resample — primary signal timeframe ────────────────
                # RSI and MACD are computed here (not on noisy 5-min data).
                # 15-min gives ~25 bars/session — enough for RSI(14) and MACD(12,26).
                try:
                    c15 = c5.resample("15min").agg(
                        {"open": "first", "high": "max", "low": "min",
                         "close": "last", "volume": "sum"}
                    ).dropna(subset=["close"])
                except Exception:
                    c15 = c5

                close15 = c15["close"]
                data_age = "angel-15min"

                # EMA20 from 15-min (20 bars = 300 min, spans full session)
                ema20 = float(ta.trend.EMAIndicator(close15, window=min(20, len(close15))).ema_indicator().iloc[-1])

                # RSI(14) from 15-min — 210 min lookback, stable institutional signal
                if len(close15) >= 15:
                    rsi_series = ta.momentum.RSIIndicator(close15, window=14).rsi().dropna()
                    rsi = float(rsi_series.iloc[-1]) if len(rsi_series) > 0 else 50.0
                elif len(close15) >= 5:
                    rsi_series = ta.momentum.RSIIndicator(close15, window=len(close15) - 1).rsi().dropna()
                    rsi = float(rsi_series.iloc[-1]) if len(rsi_series) > 0 else 50.0

                # MACD(12,26,9) from 15-min — fewer false crossovers than 5-min
                # Falls back to shorter periods early in session when < 26 bars available
                if len(close15) >= 26:
                    macd_i   = ta.trend.MACD(close15, window_fast=12, window_slow=26, window_sign=9)
                elif len(close15) >= 9:
                    macd_i   = ta.trend.MACD(close15, window_fast=6,  window_slow=13, window_sign=5)
                else:
                    macd_i = None

                macd_hist          = 0.0
                macd_hist_expanding = False
                if macd_i is not None:
                    m_series = macd_i.macd().dropna()
                    s_series = macd_i.macd_signal().dropna()
                    if len(m_series) > 0:
                        macd_val = float(m_series.iloc[-1])
                    if len(s_series) > 0:
                        macd_sig = float(s_series.iloc[-1])
                    # Histogram = MACD − Signal. Expanding = momentum building.
                    # Compare last two bars to detect expansion vs contraction.
                    if len(m_series) >= 2 and len(s_series) >= 2:
                        macd_hist      = macd_val - macd_sig
                        prev_hist      = float(m_series.iloc[-2]) - float(s_series.iloc[-2])
                        macd_hist_expanding = abs(macd_hist) > abs(prev_hist)

                # tf15: EMA9 vs EMA21 on 15-min bars (intraday trend confluence)
                if len(close15) >= 9:
                    ema9_15   = float(close15.ewm(span=9,  adjust=False).mean().iloc[-1])
                    ema21_15  = float(close15.ewm(span=21, adjust=False).mean().iloc[-1])
                    tf15_bull = ema9_15 > ema21_15
                    tf15_bear = ema9_15 < ema21_15

                # Supertrend on 15-min — intraday trend confirmation.
                # Uses same period/multiplier as daily ST but on intraday structure.
                # Data source: Angel One 5-min candles resampled to 15-min.
                st15_dir = _supertrend_direction(c15["high"], c15["low"], close15) if len(c15) >= 10 else None

                # ── 10-min resample — intermediate TF (research data) ────────
                # Not used for scoring but captured in tfData for strategy research.
                # Lets us compare which TF best predicted signal outcome over time.
                tf10_bull = False
                tf10_bear = False
                rsi10     = 50.0
                try:
                    c10 = c5.resample("10min").agg(
                        {"open": "first", "high": "max", "low": "min",
                         "close": "last", "volume": "sum"}
                    ).dropna(subset=["close"])
                    close10 = c10["close"]
                    if len(close10) >= 9:
                        e9_10  = float(close10.ewm(span=9,  adjust=False).mean().iloc[-1])
                        e21_10 = float(close10.ewm(span=21, adjust=False).mean().iloc[-1])
                        tf10_bull = e9_10 > e21_10
                        tf10_bear = e9_10 < e21_10
                    if len(close10) >= 15:
                        rsi10_s = ta.momentum.RSIIndicator(close10, window=14).rsi().dropna()
                        rsi10   = float(rsi10_s.iloc[-1]) if len(rsi10_s) > 0 else 50.0
                except Exception as exc:
                    logger.debug("10-min resample failed [%s]: %s", symbol, exc)

                # ── 30-min resample — macro trend gate ───────────────────────
                # EMA5 vs EMA10 on 30-min confirms broader intraday direction.
                # Prevents entries that fight the 2-hour trend cycle.
                try:
                    c30 = c5.resample("30min").agg(
                        {"open": "first", "high": "max", "low": "min",
                         "close": "last", "volume": "sum"}
                    ).dropna(subset=["close"])
                    close30 = c30["close"]
                    if len(close30) >= 5:
                        ema5_30  = float(close30.ewm(span=5,  adjust=False).mean().iloc[-1])
                        ema10_30 = float(close30.ewm(span=10, adjust=False).mean().iloc[-1])
                        tf30_bull = ema5_30 > ema10_30
                        tf30_bear = ema5_30 < ema10_30
                except Exception as exc:
                    logger.debug("30-min resample failed [%s]: %s", symbol, exc)

                # ── VWAP from 5-min — most accurate (78 bars/session) ────────
                try:
                    from app.data_sources.angel import compute_vwap
                    vwap = compute_vwap(angel_candles)
                    if vwap and spot > 0:
                        vwap_bullish = spot > vwap
                except Exception as exc:
                    logger.debug("VWAP compute failed [%s]: %s", symbol, exc)

                # ── Opening gap from first 5-min candle ──────────────────────
                try:
                    today_open = float(angel_candles["open"].iloc[0])
                    if today_open and prev_close:
                        gap_pct  = round((today_open - prev_close) / prev_close * 100, 2)
                        gap_up   = gap_pct >= 0.5
                        gap_down = gap_pct <= -0.5
                except Exception:
                    pass

                # ── Opening Range (OR): 9:15–9:30 first 3 bars of 5-min ──────
                # OR defines the auction range for the first 15 minutes.
                # Spot above OR high = breakout (bullish); below OR low = breakdown.
                # Data from Angel One getCandleData(FIVE_MINUTE).
                try:
                    mkt_open_ts = c5.index[0].replace(hour=9, minute=15,
                                                       second=0, microsecond=0)
                    or_end_ts   = c5.index[0].replace(hour=9, minute=30,
                                                       second=0, microsecond=0)
                    or_bars = c5[(c5.index >= mkt_open_ts) & (c5.index <= or_end_ts)]
                    if len(or_bars) >= 1:
                        or_high = float(or_bars["high"].max())
                        or_low  = float(or_bars["low"].min())
                except Exception as exc:
                    logger.debug("OR compute failed [%s]: %s", symbol, exc)

            elif intraday_closes is not None and len(intraday_closes) >= 26:
                # ── Fallback: NSE 1-min closes (resample to 15-min) ──────────
                data_age = "nse-15min"
                try:
                    c15_fb = intraday_closes.resample("15min").last().dropna()
                    close15_fb = c15_fb if len(c15_fb) >= 9 else intraday_closes
                except Exception:
                    close15_fb = intraday_closes

                spot  = float(intraday_closes.iloc[-1])
                ema20 = float(ta.trend.EMAIndicator(close15_fb, window=min(20, len(close15_fb))).ema_indicator().iloc[-1])
                if len(close15_fb) >= 15:
                    rsi_s = ta.momentum.RSIIndicator(close15_fb, window=14).rsi().dropna()
                    rsi   = float(rsi_s.iloc[-1]) if len(rsi_s) > 0 else 50.0
                if len(close15_fb) >= 13:
                    mi    = ta.trend.MACD(close15_fb)
                    ms    = mi.macd().dropna()
                    ss    = mi.macd_signal().dropna()
                    macd_val = float(ms.iloc[-1]) if len(ms) > 0 else 0.0
                    macd_sig = float(ss.iloc[-1]) if len(ss) > 0 else 0.0
                if len(close15_fb) >= 9:
                    e9  = float(close15_fb.ewm(span=9,  adjust=False).mean().iloc[-1])
                    e21 = float(close15_fb.ewm(span=21, adjust=False).mean().iloc[-1])
                    tf15_bull = e9 > e21
                    tf15_bear = e9 < e21

            else:
                # ── Daily fallback: all intraday signals unavailable ──────────
                data_age = "daily-fallback"
                spot  = float(close_d.iloc[-1])
                ema20 = float(ta.trend.EMAIndicator(close_d, window=20).ema_indicator().iloc[-1])
                rsi_s = ta.momentum.RSIIndicator(close_d, window=14).rsi().dropna()
                rsi   = float(rsi_s.iloc[-1]) if len(rsi_s) > 0 else 50.0
                mi    = ta.trend.MACD(close_d)
                ms    = mi.macd().dropna()
                ss    = mi.macd_signal().dropna()
                macd_val = float(ms.iloc[-1]) if len(ms) > 0 else 0.0
                macd_sig = float(ss.iloc[-1]) if len(ss) > 0 else 0.0
                today_open = float(df_daily["Open"].iloc[-1])
                if today_open and prev_close:
                    gap_pct  = round((today_open - prev_close) / prev_close * 100, 2)
                    gap_up   = gap_pct >= 0.5
                    gap_down = gap_pct <= -0.5

            # EMA50 always from daily (needs 50+ days — intraday can't provide this)
            # EMA200 always from daily (200+ days)
            if st_dir is None:
                st_dir = 1 if ema20 > float(ema200) else -1

            logger.debug("Indicators [%s] source=%s spot=%.0f rsi=%.1f tf15=%s tf30=%s",
                         symbol, data_age, spot, rsi,
                         "bull" if tf15_bull else ("bear" if tf15_bear else "flat"),
                         "bull" if tf30_bull else ("bear" if tf30_bear else "flat"))

            return {
                "ema20":             round(ema20, 2),
                "ema50":             round(float(ema50_d), 2),
                "ema200":            round(float(ema200), 2),
                "rsi":               round(rsi, 1),
                "macd":              round(macd_val, 4),
                "macdSignal":        round(macd_sig, 4),
                "macdHistogram":     round(macd_hist, 4),
                "macdHistExpanding": macd_hist_expanding,
                "adx":               round(float(adx_val), 1),
                "adxRising":         adx_rising,
                "atr":               round(float(atr_val), 4),
                "relativeVolume":    rel_vol,
                "spotPrice":         round(spot, 2),
                "dataAge":           data_age,
                "supertrendBullish": st_dir == 1,
                "st15Bullish":       (st15_dir == 1) if st15_dir is not None else None,
                "prevDayHigh":       round(prev_high, 2),
                "prevDayLow":        round(prev_low, 2),
                "prevClose":         round(prev_close, 2),
                "todayOpen":         round(today_open, 2) if today_open else None,
                "gapUp":             gap_up,
                "gapDown":           gap_down,
                "gapPct":            gap_pct,
                "tf15Bull":          tf15_bull,
                "tf15Bear":          tf15_bear,
                "tf30Bull":          tf30_bull,
                "tf30Bear":          tf30_bear,
                "vwap":              vwap,
                "vwapBullish":       vwap_bullish,
                "orHigh":            round(or_high, 2) if or_high else None,
                "orLow":             round(or_low, 2)  if or_low  else None,
                # Research dataset: all intraday TFs captured per signal.
                # Stored in signal_log so we can later compare which TF combination
                # best predicted actual outcome (win rate analysis over time).
                "tfData": {
                    "tf5":  {"ema9_bull": tf5_bull,  "ema9_bear": tf5_bear,
                             "rsi": round(rsi5, 1)},
                    "tf10": {"ema9_bull": tf10_bull,  "ema9_bear": tf10_bear,
                             "rsi": round(rsi10, 1)},
                    "tf15": {"ema9_bull": tf15_bull,  "ema9_bear": tf15_bear,
                             "rsi": round(rsi, 1), "macd_bull": macd_val > macd_sig},
                    "tf30": {"ema5_bull": tf30_bull,  "ema5_bear": tf30_bear},
                },
            }
        except Exception as exc:
            logger.warning("Indicator computation failed [%s]: %s", symbol, exc)
            return None

    def _compute_support_resistance(self, df_daily, spot: float) -> dict:
        """Find S/R levels using swing highs/lows from last 60 trading days.

        Quality filter: prefer levels tested 2+ times within a 0.5% price band
        (multi-touch = institutional memory). Falls back to single-touch when
        no multi-touch level exists on that side of spot.
        """
        _empty = {"resistance": None, "support": None,
                  "nearResistance": False, "nearSupport": False, "srBreakout": False,
                  "resistanceTouches": 0, "supportTouches": 0}
        if df_daily is None or len(df_daily) < 15 or spot <= 0:
            return _empty
        try:
            highs  = df_daily["High"].tail(60).values
            lows   = df_daily["Low"].tail(60).values
            window = 5

            swing_highs, swing_lows = [], []
            for i in range(window, len(highs) - window):
                if highs[i] == max(highs[i - window: i + window + 1]):
                    swing_highs.append(round(highs[i], 1))
                if lows[i]  == min(lows[i  - window: i + window + 1]):
                    swing_lows.append(round(lows[i], 1))

            # Count bars that tested each level within ±0.5%.
            # A level touched 2+ times shows institutional memory (repeated rejection/support).
            all_prices = list(highs) + list(lows)

            def _touches(level: float) -> int:
                return sum(1 for p in all_prices if level > 0 and abs(p - level) / level <= 0.005)

            def _ranked_levels(candidates: list, above_spot: bool):
                scored = [(lvl, _touches(lvl)) for lvl in candidates]
                for min_t in (2, 1):   # try multi-touch first, fall back to single
                    side = [
                        (lvl, t) for lvl, t in scored
                        if t >= min_t and (lvl > spot * 1.001 if above_spot else lvl < spot * 0.999)
                    ]
                    if side:
                        side.sort(key=lambda x: x[0] if above_spot else -x[0])
                        return side
                return []

            r_ranked = _ranked_levels(swing_highs, above_spot=True)
            s_ranked = _ranked_levels(swing_lows,  above_spot=False)

            nearest_r, r_touches = (r_ranked[0][0], r_ranked[0][1]) if r_ranked else (None, 0)
            nearest_s, s_touches = (s_ranked[0][0], s_ranked[0][1]) if s_ranked else (None, 0)

            near_r = bool(nearest_r and (nearest_r - spot) / spot <= 0.0075)
            near_s = bool(nearest_s and (spot - nearest_s) / spot <= 0.0075)

            former_resist = [h for h in swing_highs if spot * 0.999 <= h <= spot * 1.01]
            sr_breakout   = len(former_resist) > 0

            logger.debug("S/R [spot=%.0f] R=%.0f(%dt) S=%.0f(%dt)",
                         spot, nearest_r or 0, r_touches, nearest_s or 0, s_touches)

            return {
                "resistance":        nearest_r,
                "support":           nearest_s,
                "nearResistance":    near_r,
                "nearSupport":       near_s,
                "srBreakout":        sr_breakout,
                "resistanceTouches": r_touches,
                "supportTouches":    s_touches,
            }
        except Exception as exc:
            logger.debug("S/R computation failed: %s", exc)
            return _empty

    # ── option chain parsing ──────────────────────────────────────────────────

    def _parse_option_chain(self, oc_data: dict, symbol: str, direction: str, spot: float,
                            strike_offset: int = 0) -> dict | None:
        """Parse option chain and return data for one strike.

        strike_offset positions relative to ATM in the sorted strikes array:
          For CE (bullish):  -1 = ITM (lower strike),  0 = ATM,  +1 = OTM (higher strike)
          For PE (bearish):  +1 = ITM (higher strike), 0 = ATM,  -1 = OTM (lower strike)
        """
        try:
            records    = oc_data.get("records", {})
            expiries   = records.get("expiryDates", [])
            if not expiries:
                return None
            nearest    = self._nearest_expiry(expiries)
            # jugaad-data uses "expiryDates" (plural) per row; NSE direct API uses "expiryDate"
            rows = [
                r for r in records.get("data", [])
                if r.get("expiryDate") == nearest or r.get("expiryDates") == nearest
            ]
            if not rows:
                return None

            # Compute strike interval and ATM
            all_strikes = sorted({r.get("strikePrice", 0) for r in rows if r.get("strikePrice")})
            if len(all_strikes) >= 2:
                interval = all_strikes[1] - all_strikes[0]
            else:
                interval = 50
            atm_strike = round(spot / interval) * interval

            # Apply strike offset — clamp to available strikes
            atm_idx    = min(range(len(all_strikes)), key=lambda i: abs(all_strikes[i] - atm_strike))
            target_idx = max(0, min(len(all_strikes) - 1, atm_idx + strike_offset))
            target_strike = all_strikes[target_idx]

            # PCR from all OI rows of the nearest expiry.
            # More accurate than the "filtered" totals which include all expiries
            # and can be distorted by far-month OI accumulation.
            tot_ce_oi = sum(float((r.get("CE") or {}).get("openInterest") or 0) for r in rows)
            tot_pe_oi = sum(float((r.get("PE") or {}).get("openInterest") or 0) for r in rows)
            if tot_ce_oi == 0:
                # Fallback to filtered totals when row-level OI is absent
                filt      = oc_data.get("filtered", {})
                tot_ce_oi = filt.get("CE", {}).get("totOI", 1) or 1
                tot_pe_oi = filt.get("PE", {}).get("totOI", 0)
            pcr = round(tot_pe_oi / max(tot_ce_oi, 1), 2)

            # Target strike row
            target_row = min(rows, key=lambda r: abs(r.get("strikePrice", 0) - target_strike))
            # Use target_strike as atm_strike downstream (for max-pain distance calc)
            atm_strike = target_strike
            opt_key    = "CE" if direction == "BUY" else "PE"
            opt        = target_row.get(opt_key) or {}

            last_price  = float(opt.get("lastPrice") or 0)
            bid         = float(opt.get("bidprice") or last_price * 0.995)
            ask         = float(opt.get("askPrice") or last_price * 1.005)
            spread_pct  = round(abs(ask - bid) / last_price * 100, 2) if last_price > 0 else 3.0
            opt_volume  = int(opt.get("totalTradedVolume") or 0)
            oi_chg_pct  = float(opt.get("pchangeinOpenInterest") or 0)
            atm_iv      = float(opt.get("impliedVolatility") or 0)

            # Max pain: two-pass algorithm.
            # Pass 1 — collect all OI data.
            # Pass 2 — for every candidate expiry price X, sum the total in-the-money
            #          value across all strikes.  The price that MINIMISES total ITM
            #          value is max pain (most options expire worthless there).
            oi_map: dict = {}
            for row in rows:
                sp = row.get("strikePrice", 0)
                if sp:
                    oi_map[sp] = {
                        "c": float((row.get("CE") or {}).get("openInterest") or 0),
                        "p": float((row.get("PE") or {}).get("openInterest") or 0),
                    }
            pain: dict = {}
            for x in oi_map:
                total = 0.0
                for sp2, d in oi_map.items():
                    total += max(0.0, x - sp2) * d["c"]   # call at sp2 ITM at price x
                    total += max(0.0, sp2 - x) * d["p"]   # put  at sp2 ITM at price x
                pain[x] = total
            max_pain_strike = min(pain, key=pain.get) if pain else atm_strike
            max_pain_dist   = round(abs(spot - max_pain_strike) / spot * 100, 2) if spot > 0 else 0.0

            # Days-to-expiry — used for signal validity and same-day blocking
            try:
                expiry_date = datetime.strptime(nearest, "%d-%b-%Y").date()
                dte = max((expiry_date - _date.today()).days, 0)
            except Exception:
                dte = 7  # safe fallback

            return {
                "atm_strike":        atm_strike,
                "entry":             round(last_price, 1) if last_price > 1 else None,
                "bid":               round(bid, 1),
                "ask":               round(ask, 1),
                "spreadPct":         spread_pct,
                "optionVolume":      opt_volume,
                "oiChangePct":       round(oi_chg_pct, 1),
                "pcr":               pcr,
                "maxPainDistancePct": max_pain_dist,
                "expiry":            nearest,
                "atmIV":             round(atm_iv, 1),
                "dte":               dte,
                "optType":           opt_key,   # "CE" or "PE"
                "strikeOffset":      strike_offset,
            }
        except Exception as exc:
            logger.warning("Option chain parse failed [%s]: %s", symbol, exc)
            return None

    # ── candidate builder ─────────────────────────────────────────────────────

    def _build_candidate(self, symbol: str, ind: dict, opt: dict, vix: float, lot_size: int = 50,
                         strike_type: str = "ATM", sr: dict | None = None) -> dict | None:
        ema20, ema50, ema200 = ind["ema20"], ind["ema50"], ind["ema200"]
        bullish = ema20 > ema50 > ema200
        bearish = ema20 < ema50 < ema200
        if not (bullish or bearish):
            return None  # mixed trend — skip

        opt_type  = opt.get("optType", "CE" if bullish else "PE")
        direction = "BUY" if bullish else "SELL"   # BUY=bullish CE, SELL=bearish PE (buying puts)
        strike    = opt["atm_strike"]
        instrument = f"{symbol} {int(strike)} {opt_type}"

        # ── DTE-based guards ────────────────────────────────────────────────
        dte     = opt.get("dte", 7)
        now_ist = datetime.now(IST)
        if dte == 0 and now_ist.hour >= 14:
            return None  # zero-DTE after 14:00 — theta is catastrophic on failed moves

        if dte == 0:
            signal_valid = 20
        elif dte <= 3:
            signal_valid = 40
        else:
            signal_valid = 45 if symbol in INDEX_SYMBOLS else 60

        # NIFTY weekly ≤ 7 DTE (every Tue); stock/BANKNIFTY monthly ≥ 14 DTE (last Tue)
        expiry_label = "Monthly" if dte > 8 else "Weekly"

        entry = opt.get("entry")
        if not entry or entry < 1:
            return None

        # ── VIX-adjusted stop distance ───────────────────────────────────────
        # On high-VIX days normal candle noise is wider, so a tight SL gets
        # clipped by random wicks before the trade can work.  We widen both
        # the ATR fraction and the minimum premium % with the VIX level.
        # A wider SL → higher per-unit risk → fewer lots (position_sizing auto-
        # adjusts) → lower RR → signals that no longer meet RR ≥ 2 are rejected.
        # This means high-VIX days naturally produce fewer, more conservative signals.
        if vix < 15:
            atr_mult, pct_floor = 0.35, 0.15   # calm — tight SL, max lots
        elif vix < 18:
            atr_mult, pct_floor = 0.45, 0.18   # normal
        elif vix < 20:
            atr_mult, pct_floor = 0.55, 0.22   # elevated — SL breathing room
        else:                                   # 20–22 (above 22 = hard gate)
            atr_mult, pct_floor = 0.65, 0.26   # high — wide SL, far fewer signals pass

        underlying_atr   = ind.get("atr", 0)
        option_stop_dist = round(max(underlying_atr * atr_mult, entry * pct_floor), 1)
        stop_loss        = round(entry - option_stop_dist, 1)
        if stop_loss < 1:
            stop_loss        = round(entry * (1 - pct_floor), 1)
            option_stop_dist = entry - stop_loss

        _risk = max(abs(entry - stop_loss), 0.1)

        # ── Black-Scholes Greeks (needed for delta-based target calc) ─────────
        greeks = _bs_greeks(
            spot=ind["spotPrice"],
            strike=float(strike),
            dte=max(dte, 0.5),
            iv_pct=opt.get("atmIV", 15.0),
            opt_type=opt_type,
        )

        # ── S/R-anchored targets ──────────────────────────────────────────────
        # T1 = nearest resistance (BUY) or support (SELL) converted to option
        # premium move via delta. This grounds targets in actual price structure
        # rather than arbitrary multiples of the stop.
        # T2 / T3 are 1.8× and 2.8× of the T1 distance (next S/R or breakout).
        # If no S/R data, fall back to 1.5× risk (still better than 1×).
        delta_abs = abs(greeks["delta"]) if greeks.get("delta") else 0.35
        spot_now  = ind["spotPrice"]

        if bullish and sr and sr.get("resistance"):
            spot_target_dist = max(sr["resistance"] - spot_now, 0)
        elif bearish and sr and sr.get("support"):
            spot_target_dist = max(spot_now - sr["support"], 0)
        else:
            spot_target_dist = 0.0

        if spot_target_dist > 0 and delta_abs > 0.05:
            option_t1_dist = round(spot_target_dist * delta_abs, 1)
            # Bound: T1 must be at least 1:1 and at most 3:1 vs the stop
            option_t1_dist = max(option_t1_dist, _risk * 1.0)
            option_t1_dist = min(option_t1_dist, _risk * 3.0)
        else:
            option_t1_dist = round(_risk * 1.5, 1)   # fallback: 1.5:1

        t1 = round(entry + option_t1_dist,           1)
        t2 = round(entry + option_t1_dist * 1.8,     1)
        t3 = round(entry + option_t1_dist * 2.8,     1)
        rr = round(option_t1_dist / _risk, 2)

        # ── price action description ─────────────────────────────────────────
        rsi = ind["rsi"]
        adx = ind["adx"]
        if bullish:
            if rsi > 60 and adx >= 22:
                price_action = "Strong bullish trend with momentum confirmation"
            elif ema20 > ema50 * 1.002:
                price_action = "EMA20 above EMA50 — bullish momentum continuation"
            else:
                price_action = "Bullish EMA alignment with moderate trend strength"
        else:
            if rsi < 40 and adx >= 22:
                price_action = "Strong bearish trend with momentum confirmation"
            else:
                price_action = "Bearish EMA alignment with downward pressure"

        # Volume spike — 2× 20-day average = unusual institutional participation
        volume_spike = ind.get("relativeVolume", 0) >= 2.0

        vix_penalty      = 0 if vix <= 16 else (2 if vix <= 20 else 4)
        market_sentiment = max(0, min(10, (7 if bullish else 6) - vix_penalty))

        # ── IV rank (built up over time from stored daily readings) ──────────
        from app.services.storage import store_iv_reading, get_iv_rank
        atm_iv = opt.get("atmIV", 0)
        store_iv_reading(symbol, atm_iv)
        iv_rank = get_iv_rank(symbol)   # None until 20+ days of history

        # ── Timeframe confluence flags ────────────────────────────────────────
        # tf15: 15-min EMA9 vs EMA21 — primary intraday signal (resampled from 5-min)
        tf15_aligned = (
            (bullish and ind.get("tf15Bull", False)) or
            (bearish and ind.get("tf15Bear", False))
        )
        # tf30: 30-min EMA5 vs EMA10 — macro intraday trend confirmation
        tf30_aligned = (
            (bullish and ind.get("tf30Bull", False)) or
            (bearish and ind.get("tf30Bear", False))
        )

        # ── VWAP alignment ────────────────────────────────────────────────────
        # spot above VWAP = intraday buyers in control (BUY confirmation)
        # spot below VWAP = intraday sellers in control (SELL confirmation)
        vwap = ind.get("vwap")
        vwap_confirmed = False
        if vwap is not None and vwap > 0:
            if bullish and ind["spotPrice"] > vwap:
                vwap_confirmed = True
            elif bearish and ind["spotPrice"] < vwap:
                vwap_confirmed = True

        return {
            "id":               f"{symbol.lower()}-{opt_type.lower()}-{strike}-{strike_type.lower()}",
            "instrument":       instrument,
            "strikeType":       strike_type,   # ITM / ATM / OTM
            "underlying":       symbol,
            "direction":        direction,
            "style":            "Intraday trend continuation" if adx >= 20 else "Intraday momentum setup",
            "expiry":           expiry_label,
            "dte":              dte,
            "signalValidMinutes": signal_valid,
            "entry":            entry,
            "stopLoss":         stop_loss,
            "targets":          [t1, t2, t3],
            "lotSize":          lot_size,
            "optionVolume":     opt["optionVolume"],
            "bid":              opt["bid"],
            "ask":              opt["ask"],
            "spreadPct":        opt["spreadPct"],
            "priceAction":      price_action,
            "ema20":            ema20,
            "ema50":            ema50,
            "ema200":           ema200,
            "rsi":              round(rsi, 1),
            "macd":             ind["macd"],
            "macdSignal":       ind["macdSignal"],
            "macdHistogram":    ind.get("macdHistogram", 0),
            "macdHistExpanding": ind.get("macdHistExpanding", False),
            "adx":              round(adx, 1),
            "adxRising":        ind.get("adxRising", False),
            "relativeVolume":   ind["relativeVolume"],
            "oiChangePct":      opt["oiChangePct"],
            "pcr":              opt["pcr"],
            "maxPainDistancePct": opt["maxPainDistancePct"],
            "marketSentiment":  market_sentiment,
            "rr":               rr,
            "eventRisk":        self._has_upcoming_earnings(symbol),
            "supertrendBullish": ind.get("supertrendBullish", True),
            "st15Bullish":       ind.get("st15Bullish", None),
            "pdBreakout": (
                (bullish and ind["spotPrice"] > ind.get("prevDayHigh", ind["spotPrice"]))
                or
                (bearish and ind["spotPrice"] < ind.get("prevDayLow",  ind["spotPrice"]))
            ),
            "atmIV":         opt.get("atmIV", 0),
            "ivRank":        iv_rank,
            "tf15Aligned":   tf15_aligned,
            "tf30Aligned":   tf30_aligned,
            "volumeSpike":   volume_spike,
            "vwap":          vwap,
            "vwapConfirmed": vwap_confirmed,
            "delta":         greeks["delta"],
            "theta":         greeks["theta"],
            "vega":          greeks["vega"],
            # S/R levels from swing-high/low analysis of last 60 daily bars
            "resistance":           (sr or {}).get("resistance"),
            "support":              (sr or {}).get("support"),
            "nearResistance":       (sr or {}).get("nearResistance", False),
            "nearSupport":          (sr or {}).get("nearSupport", False),
            "srBreakout":           (sr or {}).get("srBreakout", False),
            "resistanceTouches":    (sr or {}).get("resistanceTouches", 0),
            "supportTouches":       (sr or {}).get("supportTouches", 0),
            # Opening gap vs previous day close
            "prevClose":         ind.get("prevClose"),
            "todayOpen":         ind.get("todayOpen"),
            "gapUp":             ind.get("gapUp", False),
            "gapDown":           ind.get("gapDown", False),
            "gapPct":            ind.get("gapPct", 0.0),
            # Opening Range (9:15–9:30) from Angel One 5-min candles
            "orHigh":            ind.get("orHigh"),
            "orLow":             ind.get("orLow"),
            "orbBreakout":       (
                (bullish and ind.get("orHigh") is not None and ind["spotPrice"] > ind["orHigh"])
                or
                (bearish and ind.get("orLow")  is not None and ind["spotPrice"] < ind["orLow"])
            ),
            "orbAgainst":        (
                (bullish and ind.get("orLow")  is not None and ind["spotPrice"] < ind["orLow"])
                or
                (bearish and ind.get("orHigh") is not None and ind["spotPrice"] > ind["orHigh"])
            ),
            "notes":         [f"Live data. Spot: {ind['spotPrice']:.0f}. DTE: {dte}."
                              + (f" VWAP: {vwap:.0f}." if vwap else "")],
            # Multi-TF research data: 5/10/15/30-min indicators stored for
            # post-trade accuracy analysis. Tells us which TF was most predictive.
            "tfData":        ind.get("tfData", {}),
            "dataAge":       ind.get("dataAge", "unknown"),
        }

    # ── public API ────────────────────────────────────────────────────────────

    def _fetch_candidate(self, symbol: str, vix: float, lot_sizes: dict) -> list[dict]:
        """Build ITM / ATM / OTM candidates for one symbol. Runs inside a thread-pool worker."""
        try:
            df_d = self.get_ohlcv_daily(symbol)

            # Fetch Angel One 5-min candles — single call covers all timeframes:
            # 5-min (VWAP) + resampled 10-min + 15-min (RSI/MACD/EMA) + 30-min (trend).
            # Falls back to NSE 1-min chart API when Angel One is not configured.
            angel_candles = None
            intraday      = None
            try:
                from app.data_sources.angel import (
                    get_intraday_candles as _angel_candles, ANGEL_AVAILABLE,
                )
                if ANGEL_AVAILABLE:
                    angel_candles = _angel_candles(symbol, interval="FIVE_MINUTE")
                else:
                    intraday = self.get_intraday_closes(symbol)
            except Exception as exc:
                logger.debug("Angel candles unavailable [%s]: %s — falling back to NSE chart", symbol, exc)
                intraday = self.get_intraday_closes(symbol)

            ind = self._compute_indicators(df_d, intraday, symbol=symbol, angel_candles=angel_candles)
            if not ind:
                logger.info("Skip %s: indicators unavailable", symbol)
                return []

            sr = self._compute_support_resistance(df_d, ind["spotPrice"])

            # Direction determines offset semantics:
            # BUY (CE): -1=ITM, 0=ATM, +1=OTM   (lower strike = deeper ITM for calls)
            # SELL (PE): +1=ITM, 0=ATM, -1=OTM   (higher strike = deeper ITM for puts)
            direction = "BUY" if ind["ema20"] > ind["ema200"] else "SELL"
            offsets   = [(-1, "ITM"), (0, "ATM"), (1, "OTM")] if direction == "BUY" \
                        else [(1, "ITM"), (0, "ATM"), (-1, "OTM")]

            oc = self.get_option_chain(symbol)
            if not oc:
                logger.info("Skip %s: option chain unavailable", symbol)
                return []

            lot        = lot_sizes.get(symbol, 50)
            candidates = []
            for offset, strike_type in offsets:
                opt = self._parse_option_chain(oc, symbol, direction, ind["spotPrice"],
                                               strike_offset=offset)
                if not opt:
                    continue
                cand = self._build_candidate(symbol, ind, opt, vix, lot_size=lot,
                                             strike_type=strike_type, sr=sr)
                if cand:
                    candidates.append(cand)
            return candidates
        except Exception as exc:
            logger.warning("Candidate build failed [%s]: %s", symbol, exc)
            return []

    # Static fallback scan list — used when NSE index constituent endpoints are unreachable.
    # Angel One expands this to the full 40-symbol universe without Akamai blocking.
    _NSE_DEFAULT_SYMBOLS = [
        # Indices — always pinned, most liquid
        "NIFTY", "BANKNIFTY",
        # Large-cap F&O — highest option OI and turnover
        "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
        "AXISBANK", "KOTAKBANK", "SBIN", "LT", "WIPRO",
        "BHARTIARTL", "HCLTECH", "BAJFINANCE", "BAJAJFINSV",
        # Mid/large cap with strong F&O activity
        "MARUTI", "SUNPHARMA", "TECHM", "TITAN", "ASIANPAINT",
        "HINDUNILVR", "ULTRACEMCO", "NESTLEIND", "POWERGRID",
        "NTPC", "ONGC", "M&M", "ADANIPORTS", "JSWSTEEL",
        "TATASTEEL", "HINDALCO", "GRASIM", "DRREDDY", "CIPLA",
        "DIVISLAB", "INDUSINDBK", "HDFCLIFE", "EICHERMOT",
        "APOLLOHOSP", "TATACONSUM",
    ]

    def get_live_candidates(self, scan_list: list[str] | None = None, top_n: int = 40) -> list[dict]:
        """Scan F&O instruments and return all valid option candidates.

        Workflow:
          Phase 0 — Build universe.
                    Angel One available: use full 40-symbol list directly.
                    Angel One unavailable: dynamic NSE constituents + OI spurts, or static fallback.
          Phase 1 — Option chain pre-fetch.
                    Angel One: parallel fetch (no Akamai blocking, no per-symbol timeout needed).
                    NSE scraping: serial fetch with 15s timeout per symbol.
          Phase 2 — Parallel OHLCV + indicator + VWAP computation.
        """
        from app.data_sources.angel import ANGEL_AVAILABLE, get_fo_universe

        if scan_list:
            instruments = scan_list
        elif ANGEL_AVAILABLE:
            instruments = get_fo_universe()
            logger.info("Universe source: Angel One (%d symbols)", len(instruments))
        else:
            instruments = self._build_dynamic_universe(max_symbols=top_n)

        vix       = self.get_india_vix()
        lot_sizes = self.get_lot_sizes()

        # ── Phase 1: option chain pre-fetch ──────────────────────────────────────
        if ANGEL_AVAILABLE:
            # Angel One: parallel fetch — no Akamai, no serial bottleneck
            logger.info("Phase 1: parallel OC fetch via Angel One (%d symbols)", len(instruments))
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(self.get_option_chain, sym): sym for sym in instruments}
                fetched = sum(1 for fut in as_completed(futures) if fut.result() is not None)
            logger.info("Phase 1 complete: %d/%d option chains fetched (Angel One)", fetched, len(instruments))
        else:
            # NSE scraping: serial with per-symbol timeout (Akamai blocking requires fresh sessions)
            logger.info("Phase 1: serial OC fetch via NSE jugaad-data (%d symbols)", len(instruments))
            fetched = 0
            for sym in instruments:
                self._jugaad = None
                _ev = threading.Event()

                def _oc_task(s=sym, ev=_ev):
                    try:
                        self.get_option_chain(s)
                    except Exception as exc:
                        logger.warning("OC pre-fetch failed [%s]: %s", s, exc)
                    finally:
                        ev.set()

                _t = threading.Thread(target=_oc_task, daemon=True)
                _t.start()
                if _ev.wait(timeout=_OC_TIMEOUT_SECS):
                    fetched += 1
                else:
                    logger.warning("OC pre-fetch timeout [%s] after %ds — skipping", sym, _OC_TIMEOUT_SECS)
                self._jugaad = None

            logger.info("Phase 1 complete: %d/%d option chains fetched (NSE jugaad)", fetched, len(instruments))

        # ── Phase 1.5: NIFTY direction as master market filter ───────────────────
        # When NIFTY indicators are computable, stock signals that trade against
        # the NIFTY trend are suppressed — they fight the macro tape.
        # Indices are still scanned independently (their own option signals).
        nifty_direction: str | None = None
        try:
            df_nifty = self.get_ohlcv_daily("NIFTY")
            intraday_nifty = None
            angel_nifty    = None
            try:
                from app.data_sources.angel import get_intraday_candles as _ac, ANGEL_AVAILABLE
                if ANGEL_AVAILABLE:
                    angel_nifty = _ac("NIFTY", interval="FIVE_MINUTE")
                else:
                    intraday_nifty = self.get_intraday_closes("NIFTY")
            except Exception:
                intraday_nifty = self.get_intraday_closes("NIFTY")
            ind_nifty = self._compute_indicators(df_nifty, intraday_nifty,
                                                  symbol="NIFTY", angel_candles=angel_nifty)
            if ind_nifty:
                nifty_direction = "BUY" if ind_nifty["ema20"] > ind_nifty["ema200"] else "SELL"
                self.last_nifty_direction = nifty_direction
                logger.info("NIFTY master direction: %s (EMA20=%.0f EMA200=%.0f)",
                            nifty_direction, ind_nifty["ema20"], ind_nifty["ema200"])
        except Exception as exc:
            logger.debug("NIFTY master filter unavailable: %s", exc)

        # ── Phase 2: indicators + VWAP + candidate build (parallel) ──────────────
        candidates: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(len(instruments), 8)) as pool:
            futures = {
                pool.submit(self._fetch_candidate, sym, vix, lot_sizes): sym
                for sym in instruments
            }
            for fut in as_completed(futures):
                candidates.extend(fut.result())

        # ── Phase 2.5: suppress stock signals against NIFTY macro direction ─────
        # Indices (NIFTY, BANKNIFTY, FINNIFTY, etc.) are never filtered — they own
        # their direction. Only stocks get screened against the master tape.
        if nifty_direction is not None:
            pre_filter = len(candidates)
            candidates = [
                c for c in candidates
                if c.get("underlying") in INDEX_SYMBOLS
                or c.get("direction") == nifty_direction
            ]
            suppressed = pre_filter - len(candidates)
            if suppressed:
                logger.info("NIFTY filter: suppressed %d contra-trend stock signal(s)", suppressed)

        logger.info("Scan complete: %d instruments -> %d candidates", len(instruments), len(candidates))
        return candidates

    # ── event calendar ────────────────────────────────────────────────────────

    def _build_event_calendar(self) -> list[dict]:
        """Return upcoming high-impact events as [{name, severity, minutesAway}].

        Sources:
          - NSE weekly/monthly option expiries (computed algorithmically)
          - RBI MPC announcement dates (hardcoded for 2025–2026)
          - NSE trading holidays (hardcoded for 2025–2026)
        Only events within the next 5 trading days are returned.
        """
        from datetime import date, timedelta as td
        now_ist  = datetime.now(IST)
        today    = now_ist.date()
        events: list[dict] = []

        def _minutes_away(event_date: date, hour: int = 9, minute: int = 15) -> float:
            event_dt = datetime(event_date.year, event_date.month, event_date.day,
                                hour, minute, tzinfo=IST)
            return (event_dt - now_ist).total_seconds() / 60.0

        def _last_tuesday(year: int, month: int) -> date:
            """Last Tuesday of the given month.
            NSE moved all F&O expiry from Thursday to Tuesday on 2025-09-01."""
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            d = date(year, month, last_day)
            # weekday(): Monday=0, Tuesday=1
            offset = (d.weekday() - 1) % 7
            return d - td(days=offset)

        def _add(name: str, event_date: date, severity: str, hour: int = 9, minute: int = 15):
            mins = _minutes_away(event_date, hour, minute)
            if -30 <= mins <= 5 * 24 * 60:   # from 30 min ago to 5 days ahead
                events.append({"name": name, "severity": severity, "minutesAway": round(mins)})

        # ── NSE market holidays 2025 ──────────────────────────────────────────
        _NSE_HOLIDAYS_2025 = [
            date(2025, 1, 26),   # Republic Day
            date(2025, 2, 26),   # Mahashivratri
            date(2025, 3, 14),   # Holi
            date(2025, 4, 14),   # Dr. Ambedkar Jayanti
            date(2025, 4, 18),   # Good Friday
            date(2025, 5, 1),    # Maharashtra Day
            date(2025, 8, 15),   # Independence Day
            date(2025, 10, 2),   # Gandhi Jayanti
            date(2025, 10, 24),  # Diwali Laxmi Puja
            date(2025, 11, 5),   # Diwali Balipratipada
            date(2025, 11, 15),  # Gurunanak Jayanti
            date(2025, 12, 25),  # Christmas
        ]
        _NSE_HOLIDAYS_2026 = [
            date(2026, 1, 26),   # Republic Day
            date(2026, 3, 3),    # Maha Shivratri (estimated)
            date(2026, 3, 20),   # Holi (estimated)
            date(2026, 4, 3),    # Good Friday (estimated)
            date(2026, 4, 14),   # Dr. Ambedkar Jayanti
            date(2026, 5, 1),    # Maharashtra Day
            date(2026, 8, 15),   # Independence Day
            date(2026, 10, 2),   # Gandhi Jayanti
            date(2026, 10, 21),  # Diwali (estimated)
            date(2026, 11, 5),   # Gurunanak Jayanti (estimated)
            date(2026, 12, 25),  # Christmas
        ]
        _ALL_HOLIDAYS = set(_NSE_HOLIDAYS_2025 + _NSE_HOLIDAYS_2026)

        # ── RBI MPC announcement dates (decision day = 3rd day of meeting) ──
        _RBI_MPC_DATES = [
            date(2025, 4, 9),
            date(2025, 6, 6),
            date(2025, 8, 7),
            date(2025, 10, 8),
            date(2025, 12, 5),
            date(2026, 2, 6),
            date(2026, 4, 3),
            date(2026, 6, 5),
            date(2026, 8, 6),
            date(2026, 10, 7),
            date(2026, 12, 4),
        ]

        # Check holidays
        for h in _ALL_HOLIDAYS:
            _add("NSE Market Holiday", h, "high", hour=0, minute=0)

        # Check RBI MPC days
        for mpc in _RBI_MPC_DATES:
            _add("RBI MPC Announcement", mpc, "high", hour=10, minute=0)

        # Expiries: read actual dates from cached NIFTY option chain (authoritative).
        # NSE has changed expiry days multiple times; hardcoding Thursday is unreliable.
        # Falls back to Thursday-based search only if the option chain isn't cached yet.
        live_expiries: list[date] = []
        oc_cached = self._cache.get("oc_NIFTY")
        if oc_cached:
            raw_dates = oc_cached["data"].get("records", {}).get("expiryDates", [])
            for raw in raw_dates:
                try:
                    d = datetime.strptime(raw, "%d-%b-%Y").date()
                    if d >= today:
                        live_expiries.append(d)
                except ValueError:
                    pass

        if live_expiries:
            # Use real expiry dates from NSE. Within 5 days = weekly, beyond = monthly.
            shown = 0
            for exp_date in sorted(live_expiries):
                days_away = (exp_date - today).days
                if days_away > 8:   # >8 days away = not the nearest weekly
                    label, sev = "NSE Monthly Expiry", "high"
                else:
                    label, sev = "NSE Weekly Expiry", "medium"
                _add(label, exp_date, sev, hour=15, minute=30)
                shown += 1
                if shown >= 3:   # show at most 3 upcoming expiries
                    break
        else:
            # Fallback: scan forward for next Tuesday (NSE expiry day since 2025-09-01)
            # Only used before the NIFTY option chain is cached (cold boot).
            d = today
            if today.weekday() == 1 and now_ist.hour >= 15 and now_ist.minute >= 30:
                d = today + td(days=1)
            for _ in range(10):
                if d.weekday() == 1 and d not in _ALL_HOLIDAYS:
                    _add("NSE Weekly Expiry", d, "medium", hour=15, minute=30)
                    break
                d += td(days=1)
            # Monthly fallback: last Tuesday of upcoming months
            for delta_months in range(3):
                month = (today.month - 1 + delta_months) % 12 + 1
                year  = today.year + (today.month - 1 + delta_months) // 12
                lt = _last_tuesday(year, month)
                if lt >= today:
                    _add("NSE Monthly Expiry", lt, "high", hour=15, minute=30)

        # Sort by minutesAway ascending
        events.sort(key=lambda e: e["minutesAway"])
        return events

    def get_market_snapshot(self) -> dict:
        vix = self.get_india_vix()

        if vix <= 14:
            regime = "Low volatility trending market"
            bias   = "Favourable conditions for directional option trades"
        elif vix <= 18:
            regime = "Moderate volatility with directional bias"
            bias   = "Selective participation with strict stop discipline"
        elif vix <= 22:
            regime = "Elevated volatility — higher premium environment"
            bias   = "Caution advised; reduce position sizes"
        else:
            regime = "High volatility environment"
            bias   = "No directional buying recommended — VIX exceeds safe threshold"

        breadth = self._get_breadth()

        return {
            "timestamp":      datetime.now(IST).isoformat(),
            "regime":         regime,
            "bias":           bias,
            "indiaVix":       vix,
            "breadth":        breadth,
            "globalSentiment": "Neutral",
            "eventCalendar":  self._build_event_calendar(),
            "earningsCalendar": {s: d.isoformat() for s, d in self.get_earnings_calendar().items()},
            "news": [
                f"India VIX at {vix}. {'Calm conditions support directional trades.' if vix < 16 else 'Elevated volatility — use tighter stops.'}",
                "Live market data sourced from NSE.",
            ],
        }

    # Key sectoral indices used for breadth when stock-level data is unavailable.
    # These 12 sectors represent the breadth of the Indian equity market and
    # move semi-independently — A/D among them is a meaningful regime signal.
    _SECTORAL_INDICES = {
        "NIFTY 50", "NIFTY BANK", "NIFTY IT", "NIFTY PHARMA",
        "NIFTY AUTO", "NIFTY FMCG", "NIFTY METAL", "NIFTY REALTY",
        "NIFTY ENERGY", "NIFTY INFRA", "NIFTY MEDIA", "NIFTY PSU BANK",
        "NIFTY FINANCIAL SERVICES", "NIFTY OIL AND GAS",
    }

    def _get_breadth(self) -> float:
        """Advance/decline ratio as a broad-market regime signal.

        Source priority:
          1. equity-stockIndices 'SECURITIES IN F&O' — ~180 stocks, best signal
          2. equity-stockIndices 'NIFTY 50'           — 50 stocks, good fallback
          3. allIndices filtered to 14 key sectors    — always available, reliable
          4. 1.0 neutral if all sources fail

        allIndices as primary was wrong — it returned 68:1 by counting all
        sub-index variants. Now it's last-resort and filtered to named sectors.
        """
        def fetch():
            # ── Stock-level (best) ────────────────────────────────────────────
            for index_name in ("SECURITIES IN F&O", "NIFTY 50"):
                try:
                    data = self._get("equity-stockIndices",
                                     params={"index": index_name})
                    if not data:
                        continue
                    rows = [
                        r for r in data.get("data", [])
                        if r.get("symbol") and (
                            r.get("series") == "EQ"
                            or (r.get("priceBand") and r.get("series"))
                        )
                    ]
                    if len(rows) < 10:
                        continue
                    advances = sum(
                        1 for r in rows
                        if float(r.get("pChange") or r.get("percentChange") or 0) > 0
                    )
                    declines = sum(
                        1 for r in rows
                        if float(r.get("pChange") or r.get("percentChange") or 0) < 0
                    )
                    if declines == 0:
                        declines = max(len(rows) - advances, 1)
                    ratio = round(min(advances / declines, 5.0), 2)
                    logger.debug("Breadth [%s]: %d adv / %d dec = %.2f",
                                 index_name, advances, declines, ratio)
                    return ratio
                except Exception as exc:
                    logger.debug("Breadth stock-level [%s] failed: %s", index_name, exc)

            # ── Sectoral fallback via allIndices ──────────────────────────────
            # Filter to the 14 named sectors so we don't count VIX / sub-index
            # variants. A/D among these sectors is genuinely meaningful.
            try:
                data = self._get("allIndices")
                if data:
                    rows = [
                        d for d in data.get("data", [])
                        if d.get("index", "").upper() in
                           {s.upper() for s in self._SECTORAL_INDICES}
                    ]
                    if len(rows) >= 5:
                        advances = sum(
                            1 for r in rows
                            if float(r.get("percentChange") or 0) > 0
                        )
                        declines = sum(
                            1 for r in rows
                            if float(r.get("percentChange") or 0) < 0
                        )
                        if declines == 0:
                            declines = max(len(rows) - advances, 1)
                        ratio = round(min(advances / declines, 5.0), 2)
                        logger.debug("Breadth [sectoral]: %d adv / %d dec = %.2f",
                                     advances, declines, ratio)
                        return ratio
            except Exception as exc:
                logger.debug("Breadth sectoral fallback failed: %s", exc)

            return None

        result = self._cached("breadth", fetch)
        return result if result is not None else 1.0

    def _has_upcoming_earnings(self, symbol: str, days: int = 2) -> bool:
        """True if symbol has a financial-results board meeting within `days` calendar days."""
        try:
            cal = self.get_earnings_calendar()
            meeting = cal.get(symbol)
            if meeting is None:
                return False
            today = datetime.now(IST).date()
            return 0 <= (meeting - today).days <= days
        except Exception:
            return False

    def get_earnings_calendar(self) -> dict[str, "_date"]:
        """Return upcoming financial-results board meetings for all NSE-listed companies.

        Source: /api/event-calendar (upcoming board meetings, ~14-day lookahead).
        Filters for meetings whose purpose contains 'result' (Financial Results,
        Quarterly Results, Annual Results). Cached for 4 hours — board meetings
        are filed days or weeks ahead and don't change intraday.

        Returns {symbol: meeting_date} for meetings within the next 7 calendar days.
        """
        _TTL_EARNINGS = 4 * 3600

        def fetch():
            data = self._get("event-calendar")
            if not data:
                return {}
            rows = data if isinstance(data, list) else data.get("data", [])
            today = datetime.now(IST).date()
            cutoff = today.toordinal() + 7
            result: dict[str, "_date"] = {}
            for row in rows:
                purpose = (row.get("purpose") or row.get("bm_purpose") or "").lower()
                if "result" not in purpose and "financial" not in purpose and "quarterly" not in purpose:
                    continue
                symbol = row.get("symbol") or row.get("bm_symbol")
                raw_dt = row.get("date") or row.get("bm_date")
                if not symbol or not raw_dt:
                    continue
                try:
                    meeting_date = datetime.strptime(raw_dt.strip(), "%d-%b-%Y").date()
                except ValueError:
                    continue
                if today <= meeting_date and meeting_date.toordinal() <= cutoff:
                    result[symbol] = meeting_date
            return result

        cached = self._cached("earnings_calendar", fetch, ttl=_TTL_EARNINGS)
        return cached or {}

    def is_available(self) -> bool:
        """Quick check whether NSE API is reachable."""
        try:
            data = self._get("allIndices")
            return data is not None
        except Exception:
            return False


# Module-level singleton used by main.py and scheduler
nse_data = NSEDataSource()
