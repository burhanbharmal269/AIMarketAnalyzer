import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

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
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

# Only indices need a special yfinance mapping; all stocks auto-resolve as SYMBOL.NS
_INDEX_YF_MAP = {
    "NIFTY":     "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY":  "NIFTY_FIN_SERVICE.NS",
}

# Fallback lot sizes used when the live bhavcopy CSV is unavailable.
# SEBI revises these quarterly — the live fetch in get_lot_sizes() stays current.
_LOT_SIZE_FALLBACK = {
    # Indices
    "NIFTY": 75, "BANKNIFTY": 15, "FINNIFTY": 40,
    # Large-cap F&O — most liquid, highest daily option turnover
    "RELIANCE": 250, "HDFCBANK": 550, "ICICIBANK": 700,
    "INFY": 300, "TCS": 150, "AXISBANK": 1200, "SBIN": 1500,
    "KOTAKBANK": 400, "LT": 375, "WIPRO": 2400,
    "BAJFINANCE": 125, "TATAMOTORS": 1425,
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

# Always included regardless of live ranking (highest liquidity indices)
INDEX_SYMBOLS    = {"NIFTY", "BANKNIFTY", "FINNIFTY"}
_FIXED_WATCHLIST = list(INDEX_SYMBOLS)  # prepended to any dynamic list

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

    def __init__(self):
        self._session: requests.Session | None = None
        self._session_at: float = 0.0
        self._cache: dict = {}
        self._ttl: int = 300  # 5 minutes
        self._session_lock = threading.Lock()  # one thread initialises the session at a time

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
                    s.get(_NSE_BASE, timeout=10)
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

    def _cached(self, key: str, fetch_fn):
        entry = self._cache.get(key)
        if entry and time.time() - entry["ts"] < self._ttl:
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
            endpoint = "option-chain-indices" if symbol in INDEX_SYMBOLS else "option-chain-equities"
            return self._get(endpoint, params={"symbol": nse_symbol})

        return self._cached(f"oc_{symbol}", fetch)

    # ── dynamic helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _to_yf_symbol(symbol: str) -> str:
        """Convert NSE symbol to yfinance ticker.
        Indices need explicit mapping; all F&O stocks are simply SYMBOL.NS."""
        return _INDEX_YF_MAP.get(symbol, f"{symbol}.NS")

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

    def get_ohlcv_daily(self, symbol: str, period: str = "200d"):
        """200-day daily OHLCV from yfinance — used for EMA200, ADX, ATR, relative volume.
        Daily data is fine with a 15-min delay because these indicators barely move intraday."""
        if not _YFINANCE:
            return None
        yf_sym = self._to_yf_symbol(symbol)
        try:
            df = yf.Ticker(yf_sym).history(period=period, interval="1d")
            return df if not df.empty else None
        except Exception as exc:
            logger.debug("yfinance daily %s failed: %s", symbol, exc)
            return None

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

        return self._cached(f"intraday_{symbol}", fetch)

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
            }
        except Exception as exc:
            logger.warning("Indicator computation failed: %s", exc)
            return None

    # ── option chain parsing ──────────────────────────────────────────────────

    def _parse_option_chain(self, oc_data: dict, symbol: str, direction: str, spot: float) -> dict | None:
        try:
            records    = oc_data.get("records", {})
            expiries   = records.get("expiryDates", [])
            if not expiries:
                return None
            nearest    = expiries[0]
            rows       = [r for r in records.get("data", []) if r.get("expiryDate") == nearest]
            if not rows:
                return None

            # Compute strike interval from the live chain (difference between consecutive strikes)
            all_strikes = sorted({r.get("strikePrice", 0) for r in rows if r.get("strikePrice")})
            if len(all_strikes) >= 2:
                interval = all_strikes[1] - all_strikes[0]
            else:
                interval = 50  # safe fallback if chain has only one strike
            atm_strike = round(spot / interval) * interval

            # PCR from filtered totals
            filt       = oc_data.get("filtered", {})
            tot_ce_oi  = filt.get("CE", {}).get("totOI", 1) or 1
            tot_pe_oi  = filt.get("PE", {}).get("totOI", 0)
            pcr        = round(tot_pe_oi / tot_ce_oi, 2)

            # ATM row
            atm_row    = min(rows, key=lambda r: abs(r.get("strikePrice", 0) - atm_strike))
            opt_key    = "CE" if direction == "BUY" else "PE"
            opt        = atm_row.get(opt_key) or {}

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
            }
        except Exception as exc:
            logger.warning("Option chain parse failed [%s]: %s", symbol, exc)
            return None

    # ── candidate builder ─────────────────────────────────────────────────────

    def _build_candidate(self, symbol: str, ind: dict, opt: dict, vix: float, lot_size: int = 50) -> dict | None:
        ema20, ema50, ema200 = ind["ema20"], ind["ema50"], ind["ema200"]
        bullish = ema20 > ema50 > ema200
        bearish = ema20 < ema50 < ema200
        if not (bullish or bearish):
            return None  # mixed trend — skip

        opt_type   = "CE" if bullish else "PE"
        direction  = "BUY"
        strike     = opt["atm_strike"]
        instrument = f"{symbol} {strike} {opt_type}"

        entry = opt.get("entry")
        if not entry or entry < 1:
            return None

        # ATR-based stop/targets on option premium (ATM delta ≈ 0.5)
        underlying_atr  = ind.get("atr", 0)
        option_stop_dist = round(max(underlying_atr * 0.35, entry * 0.15), 1)
        stop_loss        = round(entry - option_stop_dist, 1)
        if stop_loss < 1:
            stop_loss        = round(entry * 0.80, 1)
            option_stop_dist = entry - stop_loss

        t1 = round(entry + option_stop_dist,       1)
        t2 = round(entry + 2 * option_stop_dist,   1)
        t3 = round(entry + 3 * option_stop_dist,   1)
        _risk = abs(entry - stop_loss)
        rr = round(abs(t2 - entry) / _risk, 2) if _risk > 0 else 2.0

        # Price action description
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

        # Sentiment (0–10) — reduced by high VIX
        vix_penalty      = 0 if vix <= 16 else (2 if vix <= 20 else 4)
        market_sentiment = max(0, min(10, (7 if bullish else 6) - vix_penalty))

        expiry_raw   = opt.get("expiry", "")
        # NSE format is "DD-MMM-YYYY"; check the DAY (index 0), not the year (index -1)
        try:
            expiry_day = int(expiry_raw.split("-")[0]) if expiry_raw else 0
        except (ValueError, IndexError):
            expiry_day = 0
        expiry_label = "Monthly" if expiry_day > 25 else "Weekly"

        return {
            "id":               f"{symbol.lower()}-{opt_type.lower()}-{strike}",
            "instrument":       instrument,
            "underlying":       symbol,
            "direction":        direction,
            "style":            "Intraday trend continuation" if adx >= 20 else "Intraday momentum setup",
            "expiry":           expiry_label,
            "signalValidMinutes": 45 if symbol in INDEX_SYMBOLS else 60,
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
                (bearish and ind["spotPrice"] < ind.get("prevDayLow", ind["spotPrice"]))
            ),
            "atmIV":            opt.get("atmIV", 0),
            "notes":            [f"Live NSE data. Underlying spot: {ind['spotPrice']:.0f}."],
        }

    # ── public API ────────────────────────────────────────────────────────────

    def _fetch_candidate(self, symbol: str, vix: float, lot_sizes: dict) -> dict | None:
        """Build one candidate. Runs inside a thread-pool worker."""
        try:
            df_d     = self.get_ohlcv_daily(symbol)
            intraday = self.get_intraday_closes(symbol)
            ind      = self._compute_indicators(df_d, intraday)
            if not ind:
                logger.info("Skip %s: indicators unavailable", symbol)
                return None

            direction = "BUY" if ind["ema20"] > ind["ema200"] else "SELL"
            oc = self.get_option_chain(symbol)
            if not oc:
                logger.info("Skip %s: option chain unavailable", symbol)
                return None

            opt = self._parse_option_chain(oc, symbol, direction, ind["spotPrice"])
            if not opt:
                return None

            lot = lot_sizes.get(symbol, 50)
            return self._build_candidate(symbol, ind, opt, vix, lot_size=lot)
        except Exception as exc:
            logger.warning("Candidate build failed [%s]: %s", symbol, exc)
            return None

    def get_live_candidates(self, scan_list: list[str] | None = None, top_n: int = 40) -> list[dict]:
        """Scan top_n F&O instruments in parallel and return all valid candidates.

        Uses a ThreadPoolExecutor so I/O for different symbols (yfinance + NSE)
        overlaps rather than running sequentially. 6 workers = ~6× faster vs serial.
        Hard gate filtering and scoring happen in scanner.py, not here.
        """
        instruments = scan_list if scan_list else self.get_fo_watchlist(top_n=top_n)
        vix         = self.get_india_vix()
        lot_sizes   = self.get_lot_sizes()

        candidates: list[dict] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                pool.submit(self._fetch_candidate, sym, vix, lot_sizes): sym
                for sym in instruments
            }
            for fut in as_completed(futures):
                result = fut.result()
                if result:
                    candidates.append(result)

        logger.info("Scanned %d instruments → %d candidates", len(instruments), len(candidates))
        return candidates

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
            "eventCalendar":  [],
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
