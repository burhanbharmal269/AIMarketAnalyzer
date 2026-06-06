import logging
import statistics

import pandas as pd

from app.sample_data import sample_backtest

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    import ta
    _BACKTEST_DEPS = True
except ImportError:
    _BACKTEST_DEPS = False

# Instruments → yfinance tickers (indices + top liquid F&O stocks)
_BT_SYMBOLS = {
    "NIFTY":     "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY":  "NIFTY_FIN_SERVICE.NS",
    "RELIANCE":  "RELIANCE.NS",
    "HDFCBANK":  "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "INFY":      "INFY.NS",
    "SBIN":      "SBIN.NS",
}

_LOOKBACK = "180d"
_INTERVAL = "1d"
# ATR multiplier for stop: 1.5× ATR is a realistic ATM option stop distance
_ATR_STOP_MULT = 1.5


# ── data loading ──────────────────────────────────────────────────────────────

def _load(yf_sym: str):
    try:
        df = yf.Ticker(yf_sym).history(period=_LOOKBACK, interval=_INTERVAL)
        return df if not df.empty and len(df) >= 60 else None
    except Exception as exc:
        logger.warning("yfinance %s failed: %s", yf_sym, exc)
        return None


def _add_indicators(df):
    df = df.copy()
    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]

    df["ema20"]       = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    df["ema50"]       = ta.trend.EMAIndicator(close, window=50).ema_indicator()
    df["ema200"]      = ta.trend.EMAIndicator(close, window=200).ema_indicator()
    df["rsi"]         = ta.momentum.RSIIndicator(close, window=14).rsi()

    macd_i            = ta.trend.MACD(close)
    df["macd"]        = macd_i.macd()
    df["macd_signal"] = macd_i.macd_signal()
    df["adx"]         = ta.trend.ADXIndicator(high, low, close).adx()
    df["atr"]         = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

    avg_vol           = df["Volume"].rolling(20).mean()
    df["rel_vol"]     = df["Volume"] / avg_vol.replace(0, float("nan"))

    return df.dropna(subset=["ema200", "rsi", "adx", "macd", "atr"])


# ── signal detection (simplified, mirrors scanner rules) ─────────────────────

def _detect_signal(row) -> str | None:
    bullish = row["ema20"] > row["ema50"] > row["ema200"]
    bearish = row["ema20"] < row["ema50"] < row["ema200"]
    if not (bullish or bearish):
        return None

    rsi      = row["rsi"]
    macd_ok  = (row["macd"] > row["macd_signal"]) if bullish else (row["macd"] < row["macd_signal"])
    adx_ok   = row["adx"] >= 18
    vol_ok   = False if pd.isna(row["rel_vol"]) else row["rel_vol"] >= 1.1

    if bullish and 50 < rsi < 75 and macd_ok and adx_ok and vol_ok:
        return "BUY_CE"
    if bearish and 25 < rsi < 50 and macd_ok and adx_ok and vol_ok:
        return "BUY_PE"
    return None


# ── trade simulation ──────────────────────────────────────────────────────────

def _simulate(df, entry_idx: int, direction: str) -> float | None:
    """Simulate trade outcome as R-multiple using ATR-based stops.

    Stop distance = ATR × 1.5 (realistic for Indian ATM options).
    This is more accurate than a fixed % of underlying price because
    ATR reflects actual daily volatility — a ₹50 move means very
    different things on NIFTY vs a ₹500 stock.
    SL is checked before targets on each bar (conservative assumption).
    """
    row     = df.iloc[entry_idx]
    price   = row["Close"]
    atr     = row["atr"]
    stop_d  = atr * _ATR_STOP_MULT

    if stop_d <= 0:
        return None

    if direction == "BUY_CE":
        sl, t1, t2, t3 = price - stop_d, price + stop_d, price + 2*stop_d, price + 3*stop_d
        def sl_hit(low, _h): return low  <= sl
        def t1_hit(_l, high): return high >= t1
        def t2_hit(_l, high): return high >= t2
        def t3_hit(_l, high): return high >= t3
    else:
        sl, t1, t2, t3 = price + stop_d, price - stop_d, price - 2*stop_d, price - 3*stop_d
        def sl_hit(_l, high): return high >= sl
        def t1_hit(low, _h):  return low  <= t1
        def t2_hit(low, _h):  return low  <= t2
        def t3_hit(low, _h):  return low  <= t3

    for i in range(entry_idx + 1, min(entry_idx + 11, len(df))):
        bar  = df.iloc[i]
        low  = bar["Low"]
        high = bar["High"]
        if sl_hit(low, high): return -1.0   # SL checked first — conservative
        if t3_hit(low, high): return  3.0
        if t2_hit(low, high): return  2.0
        if t1_hit(low, high): return  1.0

    return 0.0  # expired without hitting any level


# ── metrics ───────────────────────────────────────────────────────────────────

def _compute_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {}
    wins   = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] < 0]

    win_rate      = round(len(wins) / len(trades) * 100, 1)
    gross_profit  = sum(t["r"] for t in wins)
    gross_loss    = abs(sum(t["r"] for t in losses))
    # gross_loss=0 means zero losing trades — perfect record, not a zero score
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

    # Equity-curve drawdown in R
    equity = peak = max_dd = 0.0
    for t in trades:
        equity += t["r"]
        peak    = max(peak, equity)
        max_dd  = max(max_dd, peak - equity)

    # Sharpe proxy: mean / stdev of R outcomes
    returns = [t["r"] for t in trades]
    try:
        sharpe = round(statistics.mean(returns) / statistics.stdev(returns), 2) if len(returns) > 1 else 0.0
    except statistics.StatisticsError:
        sharpe = 0.0

    avg_win_r = round(sum(t["r"] for t in wins)  / len(wins),   2) if wins   else 0.0

    return {
        "totalTrades":  len(trades),
        "wins":         len(wins),
        "losses":       len(losses),
        "winRate":      win_rate,
        "profitFactor": profit_factor,
        "maxDrawdownR": round(max_dd, 2),
        "sharpeProxy":  sharpe,
        "avgWinR":      avg_win_r,
    }


# ── public API ────────────────────────────────────────────────────────────────

def run_backtest() -> dict:
    """Run real historical backtest using yfinance data. Falls back to sample."""
    if not _BACKTEST_DEPS:
        return _sample_result()

    all_trades: list[dict] = []
    strategy_rows: list[dict] = []

    for symbol, yf_sym in _BT_SYMBOLS.items():
        df = _load(yf_sym)
        if df is None:
            logger.info("Backtest: no data for %s, skipping", symbol)
            continue

        df = _add_indicators(df)
        trades: list[dict] = []
        last_signal_i = -5  # minimum gap between signals

        for i in range(len(df) - 10):
            if i - last_signal_i < 3:
                continue  # avoid consecutive signals on same setup
            direction = _detect_signal(df.iloc[i])
            if direction:
                r = _simulate(df, i, direction)
                if r is not None:
                    trades.append({"symbol": symbol, "direction": direction, "r": r})
                    last_signal_i = i

        all_trades.extend(trades)
        m = _compute_metrics(trades)
        if m:
            strategy_rows.append({
                "name":     f"{symbol} trend continuation",
                "trades":   m["totalTrades"],
                "winRate":  m["winRate"],
                "avgRr":    m["avgWinR"],
                "status":   "Live historical data" if m["totalTrades"] >= 10 else "Insufficient data",
            })

    if not all_trades:
        return _sample_result()

    overall = _compute_metrics(all_trades)
    # Treat 1 R ≈ 1% account risk for drawdown %
    max_dd_pct = round(overall.get("maxDrawdownR", 0) * 1.0, 1)

    return {
        "metrics": {
            "winRate":        overall.get("winRate", 0),
            "profitFactor":   overall.get("profitFactor", 0),
            "maxDrawdownPct": max_dd_pct,
            "sharpeProxy":    overall.get("sharpeProxy", 0),
            "totalTrades":    overall.get("totalTrades", 0),
            "dataSource":     "live",
        },
        "strategies": strategy_rows,
    }


def _sample_result() -> dict:
    s          = sample_backtest()
    win_rate   = (s["wins"] / s["totalTrades"]) * 100 if s["totalTrades"] else 0
    pf         = s["grossProfitR"] / s["grossLossR"] if s["grossLossR"] else 0
    return {
        "metrics": {
            "winRate":        round(win_rate, 1),
            "profitFactor":   round(pf, 2),
            "maxDrawdownPct": s["maxDrawdownPct"],
            "sharpeProxy":    s["sharpeProxy"],
            "totalTrades":    s["totalTrades"],
            "dataSource":     "sample",
        },
        "strategies": s["strategies"],
    }


def backtest_snapshot() -> dict:
    return run_backtest()
