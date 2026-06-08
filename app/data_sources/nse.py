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
        self._session_lock = threading.Lock()  # one thread initialises the session at a time
        self._jugaad: object | None = None      # jugaad-data NSELive client (lazy init)

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
            # jugaad-data handles Akamai session cookies that plain requests can't obtain.
            # Uses original NSE ticker (e.g. "FINNIFTY"), not the API display name.
            # Caller resets self._jugaad = None before each symbol so every call
            # gets a fresh session — prevents NSE from flagging a shared session.
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
                        return data
                    self._jugaad = None
                except Exception as exc:
                    logger.debug("jugaad-data option chain %s failed: %s", symbol, exc)
                    self._jugaad = None
            # Fallback: direct requests (works if NSE session cookies are valid)
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

        # ── 2. Fetch from yfinance with retry/backoff ────────────────────────
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

    def _compute_indicators(self, df_daily, intraday_closes=None) -> dict | None:
        """Blend real-time NSE intraday closes with daily OHLCV.

        Split rationale:
          Real-time (NSE chart API) → EMA20, EMA50, RSI, MACD, spot price
          Daily / slow (yfinance)   → EMA200, ADX, ATR, relative volume
        EMA200 and ADX move so slowly that a 15-min lag on daily data is irrelevant.
        RSI and MACD on intraday closes must be current or signals are stale.
        """
        if not _TA or df_daily is None or len(df_daily) < 52:
            return None
        try:
            import pandas as pd

            close_d = df_daily["Close"]
            high_d  = df_daily["High"]
            low_d   = df_daily["Low"]

            # ── slow indicators from daily OHLCV (yfinance, fine with 15-min lag) ──
            ema200  = (
                ta.trend.EMAIndicator(close_d, window=200).ema_indicator().iloc[-1]
                if len(close_d) >= 200 else
                ta.trend.EMAIndicator(close_d, window=50).ema_indicator().iloc[-1]
            )
            adx_val = ta.trend.ADXIndicator(high_d, low_d, close_d).adx().iloc[-1]
            atr_val = ta.volatility.AverageTrueRange(high_d, low_d, close_d).average_true_range().iloc[-1]
            avg_vol_raw = df_daily["Volume"].rolling(20).mean().iloc[-1]
            avg_vol = float(avg_vol_raw) if pd.notna(avg_vol_raw) else 0.0
            last_vol = float(df_daily["Volume"].iloc[-1])
            rel_vol = round(last_vol / avg_vol, 2) if avg_vol > 0 and pd.notna(last_vol) else 1.0

            # ── Previous day high / low for breakout detection ──
            prev_high = float(high_d.iloc[-2]) if len(high_d) >= 2 else float(high_d.iloc[-1])
            prev_low  = float(low_d.iloc[-2])  if len(low_d)  >= 2 else float(low_d.iloc[-1])

            # ── fast indicators from real-time NSE intraday closes ──
            # Use intraday if we have ≥ 26 bars (enough for MACD slow EMA); else fall back to daily.
            if intraday_closes is not None and len(intraday_closes) >= 26:
                fast_src = intraday_closes
                data_age = "real-time"
            else:
                fast_src = close_d
                data_age = "daily-fallback"

            ema20    = ta.trend.EMAIndicator(fast_src, window=20).ema_indicator().iloc[-1]
            ema50_   = ta.trend.EMAIndicator(fast_src, window=50).ema_indicator().iloc[-1] if len(fast_src) >= 50 else ema20

            # ── Supertrend direction from daily data (period=7, multiplier=3) ──
            # Falls back to EMA direction — ema20 must be computed first.
            st_dir = _supertrend_direction(high_d, low_d, close_d)
            if st_dir is None:
                st_dir = 1 if float(ema20) > float(ema200) else -1
            rsi      = ta.momentum.RSIIndicator(fast_src, window=14).rsi().iloc[-1]
            macd_i   = ta.trend.MACD(fast_src)
            macd_val = macd_i.macd().iloc[-1]
            macd_sig = macd_i.macd_signal().iloc[-1]
            spot     = float(fast_src.iloc[-1])

            if data_age == "real-time":
                logger.debug("Indicators for spot=%s use real-time NSE chart data", round(spot))
            else:
                logger.debug("Indicators using daily fallback (NSE chart unavailable)")

            # ── 15-min confluence — resample 1-min data, no extra API call ──
            tf15_bull = False
            tf15_bear = False
            if intraday_closes is not None and len(intraday_closes) >= 30:
                try:
                    import pandas as pd
                    c15 = intraday_closes.resample("15min").last().dropna()
                    if len(c15) >= 9:
                        ema9_15  = c15.ewm(span=9,  adjust=False).mean()
                        ema21_15 = c15.ewm(span=21, adjust=False).mean()
                        tf15_bull = float(ema9_15.iloc[-1]) > float(ema21_15.iloc[-1])
                        tf15_bear = float(ema9_15.iloc[-1]) < float(ema21_15.iloc[-1])
                except Exception as exc:
                    logger.debug("15-min resample failed: %s", exc)

            return {
                "ema20":             round(float(ema20), 2),
                "ema50":             round(float(ema50_), 2),
                "ema200":            round(float(ema200), 2),
                "rsi":               round(float(rsi), 1),
                "macd":              round(float(macd_val), 4),
                "macdSignal":        round(float(macd_sig), 4),
                "adx":               round(float(adx_val), 1),
                "atr":               round(float(atr_val), 4),
                "relativeVolume":    rel_vol,
                "spotPrice":         round(spot, 2),
                "dataAge":           data_age,
                "supertrendBullish": st_dir == 1,
                "prevDayHigh":       round(prev_high, 2),
                "prevDayLow":        round(prev_low, 2),
                "tf15Bull":          tf15_bull,
                "tf15Bear":          tf15_bear,
            }
        except Exception as exc:
            logger.warning("Indicator computation failed: %s", exc)
            return None

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

            # PCR from filtered totals
            filt       = oc_data.get("filtered", {})
            tot_ce_oi  = filt.get("CE", {}).get("totOI", 1) or 1
            tot_pe_oi  = filt.get("PE", {}).get("totOI", 0)
            pcr        = round(tot_pe_oi / tot_ce_oi, 2)

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
                         strike_type: str = "ATM") -> dict | None:
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

        expiry_label = "Monthly" if dte > 14 else "Weekly"

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

        t1 = round(entry + option_stop_dist,     1)
        t2 = round(entry + 2 * option_stop_dist, 1)
        t3 = round(entry + 3 * option_stop_dist, 1)
        _risk = abs(entry - stop_loss)
        rr = round(abs(t2 - entry) / _risk, 2) if _risk > 0 else 2.0

        # ── Black-Scholes Greeks ──────────────────────────────────────────────
        greeks = _bs_greeks(
            spot=ind["spotPrice"],
            strike=float(strike),
            dte=max(dte, 0.5),
            iv_pct=opt.get("atmIV", 15.0),
            opt_type=opt_type,
        )

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

        vix_penalty      = 0 if vix <= 16 else (2 if vix <= 20 else 4)
        market_sentiment = max(0, min(10, (7 if bullish else 6) - vix_penalty))

        # ── IV rank (built up over time from stored daily readings) ──────────
        from app.services.storage import store_iv_reading, get_iv_rank
        atm_iv = opt.get("atmIV", 0)
        store_iv_reading(symbol, atm_iv)
        iv_rank = get_iv_rank(symbol)   # None until 20+ days of history

        # ── 15-min confluence flag ────────────────────────────────────────────
        tf15_aligned = (
            (bullish and ind.get("tf15Bull", False)) or
            (bearish and ind.get("tf15Bear", False))
        )

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
            "adx":              round(adx, 1),
            "relativeVolume":   ind["relativeVolume"],
            "oiChangePct":      opt["oiChangePct"],
            "pcr":              opt["pcr"],
            "maxPainDistancePct": opt["maxPainDistancePct"],
            "marketSentiment":  market_sentiment,
            "rr":               rr,
            "eventRisk":        False,
            "supertrendBullish": ind.get("supertrendBullish", True),
            "pdBreakout": (
                (bullish and ind["spotPrice"] > ind.get("prevDayHigh", ind["spotPrice"]))
                or
                (bearish and ind["spotPrice"] < ind.get("prevDayLow",  ind["spotPrice"]))
            ),
            "atmIV":        opt.get("atmIV", 0),
            "ivRank":       iv_rank,
            "tf15Aligned":  tf15_aligned,
            "delta":        greeks["delta"],
            "theta":        greeks["theta"],
            "vega":         greeks["vega"],
            "notes":        [f"Live NSE data. Spot: {ind['spotPrice']:.0f}. DTE: {dte}."],
        }

    # ── public API ────────────────────────────────────────────────────────────

    def _fetch_candidate(self, symbol: str, vix: float, lot_sizes: dict) -> list[dict]:
        """Build ITM / ATM / OTM candidates for one symbol. Runs inside a thread-pool worker."""
        try:
            df_d     = self.get_ohlcv_daily(symbol)
            intraday = self.get_intraday_closes(symbol)
            ind      = self._compute_indicators(df_d, intraday)
            if not ind:
                logger.info("Skip %s: indicators unavailable", symbol)
                return []

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
                                             strike_type=strike_type)
                if cand:
                    candidates.append(cand)
            return candidates
        except Exception as exc:
            logger.warning("Candidate build failed [%s]: %s", symbol, exc)
            return []

    # Static fallback scan list — used when NSE index constituent endpoints are unreachable.
    # Ordered by historical reliability with jugaad-data. Angel One expands this to full F&O.
    _NSE_DEFAULT_SYMBOLS = [
        # Indices — always pinned, most liquid
        "NIFTY", "BANKNIFTY",
        # Large-cap F&O stocks — highest option turnover, reliable session
        "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
        "AXISBANK", "KOTAKBANK", "SBIN", "LT",
        # Mid/large additions — included if within per-symbol timeout
        "WIPRO", "BHARTIARTL", "HCLTECH", "BAJFINANCE",
    ]

    def get_live_candidates(self, scan_list: list[str] | None = None, top_n: int = 14) -> list[dict]:
        """Scan F&O instruments and return all valid option candidates.

        Workflow:
          Phase 0 — Build universe: dynamic (NSE index constituents + OI spurts) or static fallback.
          Phase 1 — Serial option-chain pre-fetch (one at a time, fresh jugaad session per symbol,
                    with a per-symbol timeout so slow symbols don't stall the scan).
          Phase 2 — Parallel OHLCV + indicator computation (reads SQLite cache, never hits NSE).

        Angel One SmartAPI integration will replace Phase 0-1 with a single fast API call,
        unlocking the full 150+ F&O universe once credentials are available.
        """
        if scan_list:
            instruments = scan_list
        else:
            instruments = self._build_dynamic_universe(max_symbols=top_n)

        vix       = self.get_india_vix()
        lot_sizes = self.get_lot_sizes()

        # ── Phase 1: serial option-chain pre-fetch with per-symbol timeout ──────
        # jugaad-data NSELive session is reset before each symbol (prevents session
        # flagging when NSE detects multiple rapid requests from the same client).
        # If a symbol's option chain doesn't arrive within _OC_TIMEOUT_SECS, we skip
        # it for this scan — it's excluded from Phase 2 naturally (cache miss → None).
        fetched = 0
        for sym in instruments:
            self._jugaad = None          # fresh jugaad session per symbol
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
            self._jugaad = None          # clean slate for next symbol regardless

        logger.info("Phase 1 complete: %d/%d option chains fetched", fetched, len(instruments))

        # ── Phase 2: indicators + candidate build (parallel, reads cache only) ──
        # Each symbol produces up to 3 candidates (ITM / ATM / OTM). Flatten all.
        candidates: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(len(instruments), 6)) as pool:
            futures = {
                pool.submit(self._fetch_candidate, sym, vix, lot_sizes): sym
                for sym in instruments
            }
            for fut in as_completed(futures):
                candidates.extend(fut.result())

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

        def _last_thursday(year: int, month: int) -> date:
            """Last Thursday of the given month."""
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            d = date(year, month, last_day)
            # weekday(): Monday=0 … Thursday=3
            offset = (d.weekday() - 3) % 7
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

        # Weekly expiry: next Thursday (or nearest non-holiday).
        # Start from tomorrow if today's Thursday has already expired (after 15:30 IST)
        # so the calendar always shows a future expiry, not an already-passed one.
        thursday_start = today
        if today.weekday() == 3 and now_ist.hour >= 15 and now_ist.minute >= 30:
            thursday_start = today + td(days=1)
        d = thursday_start
        for _ in range(10):
            if d.weekday() == 3 and d not in _ALL_HOLIDAYS:
                _add("NSE Weekly Expiry", d, "medium", hour=15, minute=30)
                break
            d += td(days=1)

        # Monthly expiry: last Thursday of each upcoming month
        for delta_months in range(3):
            month = (today.month - 1 + delta_months) % 12 + 1
            year  = today.year + (today.month - 1 + delta_months) // 12
            lt = _last_thursday(year, month)
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
            "news": [
                f"India VIX at {vix}. {'Calm conditions support directional trades.' if vix < 16 else 'Elevated volatility — use tighter stops.'}",
                "Live market data sourced from NSE.",
            ],
        }

    def _get_breadth(self) -> float:
        def fetch():
            data = self._get("allIndices")
            if not data:
                return None
            advances = sum(
                1 for d in data.get("data", [])
                if float(d.get("percentChange") or 0) > 0
            )
            declines = sum(
                1 for d in data.get("data", [])
                if float(d.get("percentChange") or 0) < 0
            )
            return round(advances / declines, 2) if declines > 0 else 1.5

        result = self._cached("breadth", fetch)
        return result if result is not None else 1.2

    def is_available(self) -> bool:
        """Quick check whether NSE API is reachable."""
        try:
            data = self._get("allIndices")
            return data is not None
        except Exception:
            return False


# Module-level singleton used by main.py and scheduler
nse_data = NSEDataSource()
