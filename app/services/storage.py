import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import settings

IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger(__name__)


def get_connection():
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_audit (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at     TEXT    NOT NULL,
                approved_count INTEGER NOT NULL,
                rejected_count INTEGER NOT NULL,
                no_trade       INTEGER NOT NULL,
                payload_json   TEXT    NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_journal (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at       TEXT    NOT NULL,
                instrument       TEXT    NOT NULL,
                direction        TEXT    NOT NULL,
                entry            REAL    NOT NULL,
                stop_loss        REAL    NOT NULL,
                target_1         REAL    NOT NULL,
                target_2         REAL    NOT NULL,
                target_3         REAL    NOT NULL,
                confidence_score INTEGER NOT NULL DEFAULT 0,
                status           TEXT    NOT NULL DEFAULT 'paper',
                notes            TEXT    NOT NULL DEFAULT '',
                exit_price       REAL,
                outcome          TEXT,
                pnl_r            REAL
            )
            """
        )
        # Migrate existing trade_journal tables that lack the new columns
        _add_column_if_missing(conn, "trade_journal", "notes",      "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "trade_journal", "exit_price", "REAL")
        _add_column_if_missing(conn, "trade_journal", "outcome",    "TEXT")
        _add_column_if_missing(conn, "trade_journal", "pnl_r",      "REAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_alerts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT    NOT NULL,
                message    TEXT    NOT NULL,
                attempts   INTEGER NOT NULL DEFAULT 0,
                next_retry TEXT    NOT NULL,
                status     TEXT    NOT NULL DEFAULT 'pending'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS iv_history (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol  TEXT    NOT NULL,
                date    TEXT    NOT NULL,
                iv_pct  REAL    NOT NULL,
                UNIQUE(symbol, date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_ohlcv (
                symbol  TEXT NOT NULL,
                date    TEXT NOT NULL,
                open    REAL, high REAL, low REAL, close REAL, volume REAL,
                PRIMARY KEY (symbol, date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at       TEXT    NOT NULL,
                scan_id          INTEGER,          -- FK to scan_audit
                journal_id       INTEGER,          -- FK to trade_journal (set when auto-journaled)

                -- Instrument
                instrument       TEXT    NOT NULL,
                underlying       TEXT    NOT NULL DEFAULT '',
                direction        TEXT    NOT NULL,
                strike_type      TEXT,             -- ITM / ATM / OTM
                expiry           TEXT,             -- Weekly / Monthly
                dte              INTEGER,

                -- Entry plan
                entry            REAL    NOT NULL,
                stop_loss        REAL    NOT NULL,
                target_1         REAL    NOT NULL DEFAULT 0,
                target_2         REAL    NOT NULL DEFAULT 0,
                target_3         REAL    NOT NULL DEFAULT 0,
                rr               REAL,
                lot_size         INTEGER,

                -- Scores
                score_total      INTEGER NOT NULL,
                score_trend      INTEGER,
                score_momentum   INTEGER,
                score_volume     INTEGER,
                score_oc         INTEGER,
                score_sentiment  INTEGER,
                score_rr         INTEGER,

                -- Technical context at signal time
                spot_price       REAL,
                ema20            REAL,
                ema50            REAL,
                ema200           REAL,
                rsi              REAL,
                adx              REAL,
                macd             REAL,
                macd_signal      REAL,
                atr              REAL,
                rel_volume       REAL,
                data_age         TEXT,             -- angel-5min / nse-1min / daily-fallback
                supertrend_bull  INTEGER,          -- 0 / 1
                pd_breakout      INTEGER,
                tf15_aligned     INTEGER,
                vwap             REAL,
                vwap_confirmed   INTEGER,
                gap_up           INTEGER,
                gap_down         INTEGER,
                gap_pct          REAL,
                sr_breakout      INTEGER,
                near_resistance  INTEGER,
                near_support     INTEGER,
                resistance       REAL,
                support          REAL,

                -- Option chain context
                atm_iv           REAL,
                iv_rank          REAL,
                pcr              REAL,
                oi_change_pct    REAL,
                option_volume    INTEGER,
                spread_pct       REAL,
                max_pain_dist    REAL,

                -- Greeks
                delta            REAL,
                theta            REAL,
                vega             REAL,

                -- Market context at signal time
                india_vix        REAL,
                market_regime    TEXT,
                breadth          REAL,
                nifty_direction  TEXT,             -- BUY / SELL (NIFTY master direction)

                -- Outcome (filled in later by monitor or manually)
                outcome          TEXT,             -- win / loss / breakeven / expired
                exit_price       REAL,
                pnl_r            REAL,
                exit_at          TEXT,
                exit_reason      TEXT              -- sl_hit / t1_hit / t2_hit / t3_hit / manual / expired
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signal_log_created ON signal_log(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signal_log_instrument ON signal_log(instrument)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signal_log_outcome ON signal_log(outcome)"
        )
        # Migrate trade_journal to add signal_id link if not present
        _add_column_if_missing(conn, "trade_journal", "signal_id", "INTEGER")


def _add_column_if_missing(conn, table: str, column: str, col_def: str):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


# ── scan audit ────────────────────────────────────────────────────────────────

def record_scan(scan: dict) -> int:
    """Insert scan summary and return the new scan_audit row id."""
    init_db()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO scan_audit
                (created_at, approved_count, rejected_count, no_trade, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                len(scan["approved"]),
                len(scan["rejected"]),
                1 if scan["noTrade"] else 0,
                json.dumps(scan),
            ),
        )
        return cursor.lastrowid


def recent_scans(limit: int = 10) -> list[dict]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, approved_count, rejected_count, no_trade
            FROM scan_audit
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


# ── trade journal ─────────────────────────────────────────────────────────────

def add_journal_entry(entry: dict) -> int:
    init_db()
    targets = entry.get("targets", [0, 0, 0])
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO trade_journal
                (created_at, instrument, direction, entry, stop_loss,
                 target_1, target_2, target_3, confidence_score, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                entry["instrument"],
                entry["direction"],
                float(entry["entry"]),
                float(entry["stopLoss"]),
                float(targets[0]) if len(targets) > 0 else 0.0,
                float(targets[1]) if len(targets) > 1 else 0.0,
                float(targets[2]) if len(targets) > 2 else 0.0,
                int(entry.get("confidenceScore", 0)),
                entry.get("status", "paper"),
                entry.get("notes", ""),
            ),
        )
        return cursor.lastrowid


def get_journal_entries(limit: int = 50, status_filter: str | None = None) -> list[dict]:
    init_db()
    with get_connection() as conn:
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM trade_journal WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status_filter, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trade_journal ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def compute_risk_state(risk_pct: float = 2.0) -> dict:
    """Compute real drawdown percentages from closed journal trades.

    Converts R-multiples to account % using risk_pct (e.g. 2% risk per trade →
    1R loss = 2% of capital).  Only rows with pnl_r < 0 are counted.
    Falls back to zero drawdown if the DB is empty or unavailable.
    """
    _SAFE = {"dailyLossPct": 0.0, "weeklyDrawdownPct": 0.0, "monthlyDrawdownPct": 0.0}
    try:
        now_ist = datetime.now(IST)
        # Daily: start of today's IST calendar day (midnight IST), not rolling 24h
        today_ist_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoffs = {
            "dailyLossPct":       today_ist_start.astimezone(timezone.utc).isoformat(),
            "weeklyDrawdownPct":  (now_ist - timedelta(days=7)).astimezone(timezone.utc).isoformat(),
            "monthlyDrawdownPct": (now_ist - timedelta(days=30)).astimezone(timezone.utc).isoformat(),
        }
        result = {}
        with get_connection() as conn:
            for key, since in cutoffs.items():
                row = conn.execute(
                    "SELECT COALESCE(SUM(pnl_r), 0.0) FROM trade_journal "
                    "WHERE pnl_r < 0 AND created_at >= ?",
                    (since,),
                ).fetchone()
                total_loss_r = abs(float(row[0])) if row and row[0] else 0.0
                result[key] = round(total_loss_r * risk_pct, 2)

            # Auto-compute consecutive loss streak from most recent closed trades
            recent = conn.execute(
                "SELECT outcome FROM trade_journal "
                "WHERE outcome IS NOT NULL ORDER BY id DESC LIMIT 10"
            ).fetchall()
            streak = 0
            for r in recent:
                if r["outcome"] == "loss":
                    streak += 1
                else:
                    break
            result["lossStreak"] = streak

        return result
    except Exception:
        return _SAFE


def _calc_analytics(pnls: list[float]) -> dict:
    """Compute analytics dict from a list of pnl_r values."""
    if not pnls:
        return {"totalTrades": 0, "winRate": 0.0, "totalR": 0.0,
                "profitFactor": 0.0, "avgWinR": 0.0, "avgLossR": 0.0,
                "bestTrade": 0.0, "worstTrade": 0.0, "equity": []}
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    equity: list[float] = []
    running = 0.0
    for p in pnls:
        running += p
        equity.append(round(running, 2))
    gross_win  = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "totalTrades":  len(pnls),
        "winRate":      round(len(wins) / len(pnls) * 100, 1),
        "totalR":       round(running, 2),
        "profitFactor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0,
        "avgWinR":      round(gross_win  / len(wins),   2) if wins   else 0.0,
        "avgLossR":     round(sum(losses) / len(losses), 2) if losses else 0.0,
        "bestTrade":    round(max(pnls), 2),
        "worstTrade":   round(min(pnls), 2),
        "equity":       equity,
    }


def get_journal_analytics() -> dict:
    """Aggregate closed-trade P&L stats split by paper vs live trades."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT pnl_r, status FROM trade_journal WHERE pnl_r IS NOT NULL ORDER BY id ASC"
            ).fetchall()

        all_pnls   = [float(r["pnl_r"]) for r in rows]
        paper_pnls = [float(r["pnl_r"]) for r in rows if r["status"] == "paper"]
        # "open" = live trade still active (no pnl_r yet); closed live trades keep status "open"
        # so we identify closed live trades by status=="open" AND pnl_r present
        live_pnls  = [float(r["pnl_r"]) for r in rows if r["status"] == "open" and r["pnl_r"] is not None]

        return {
            **_calc_analytics(all_pnls),
            "paper": _calc_analytics(paper_pnls),
            "live":  _calc_analytics(live_pnls),
        }
    except Exception as exc:
        logger.warning("Journal analytics error: %s", exc)
        empty = _calc_analytics([])
        return {**empty, "paper": empty, "live": empty}


# ── IV rank history ───────────────────────────────────────────────────────────

def store_iv_reading(symbol: str, iv_pct: float) -> None:
    """Upsert today's ATM IV for a symbol (one row per symbol per IST calendar day)."""
    if iv_pct <= 0:
        return
    init_db()
    today = datetime.now(IST).date().isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO iv_history (symbol, date, iv_pct) VALUES (?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET iv_pct = excluded.iv_pct""",
            (symbol, today, round(iv_pct, 2)),
        )


def get_iv_rank(symbol: str) -> float | None:
    """IV percentile rank 0-100 using stored history.
    Returns None when fewer than 20 readings exist (rank would be unreliable).
    """
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT iv_pct FROM iv_history WHERE symbol = ? ORDER BY date ASC",
            (symbol,),
        ).fetchall()
    if len(rows) < 20:
        return None
    ivs     = [float(r[0]) for r in rows]
    current = ivs[-1]
    lo, hi  = min(ivs), max(ivs)
    if hi == lo:
        return 50.0
    return round((current - lo) / (hi - lo) * 100, 1)


# ── telegram retry queue ──────────────────────────────────────────────────────

def enqueue_alert(message: str) -> None:
    """Write a pending alert to the retry queue."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO pending_alerts (created_at, message, attempts, next_retry, status) VALUES (?, ?, 0, ?, 'pending')",
            (now, message, now),
        )


def pop_due_alerts(limit: int = 20) -> list[dict]:
    """Fetch pending alerts whose next_retry is due now or overdue."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_alerts WHERE status = 'pending' AND next_retry <= ? ORDER BY id ASC LIMIT ?",
            (now, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def update_alert_status(alert_id: int, status: str, attempts: int, next_retry: str | None = None) -> None:
    init_db()
    with get_connection() as conn:
        conn.execute(
            "UPDATE pending_alerts SET status = ?, attempts = ?, next_retry = COALESCE(?, next_retry) WHERE id = ?",
            (status, attempts, next_retry, alert_id),
        )


# ── Daily OHLCV cache ─────────────────────────────────────────────────────────

def get_ohlcv_cache(symbol: str, min_rows: int = 150, max_stale_days: int = 5) -> list[dict] | None:
    """Return cached daily OHLCV rows for symbol, or None if missing/stale.

    Freshness rule: last stored date must be within max_stale_days of today IST.
    Covers weekends (2 days) + public holidays (1–2 days) with room to spare.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume "
            "FROM daily_ohlcv WHERE symbol = ? ORDER BY date ASC",
            (symbol,),
        ).fetchall()

    if not rows or len(rows) < min_rows:
        return None

    last_date = date.fromisoformat(rows[-1]["date"])
    staleness = (datetime.now(IST).date() - last_date).days
    if staleness > max_stale_days:
        return None

    return [
        {
            "date":   r["date"],
            "Open":   r["open"],
            "High":   r["high"],
            "Low":    r["low"],
            "Close":  r["close"],
            "Volume": r["volume"],
        }
        for r in rows
    ]


def invalidate_ohlcv_today() -> int:
    """Delete today's IST daily_ohlcv rows so next scan fetches the completed EOD candle.

    Called at 16:05 IST after market close. yfinance usually publishes the
    day's complete candle within 30 min of 15:30 close.
    """
    today = datetime.now(IST).date().isoformat()
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM daily_ohlcv WHERE date = ?", (today,))
        deleted = cursor.rowcount
    if deleted:
        logger.info("daily_ohlcv invalidated %d rows for %s (EOD refresh)", deleted, today)
    return deleted


def set_ohlcv_cache(symbol: str, rows: list[dict]) -> None:
    """Upsert daily OHLCV rows for a symbol. Each row must have keys:
    date (YYYY-MM-DD), Open, High, Low, Close, Volume."""
    if not rows:
        return
    with get_connection() as conn:
        conn.executemany(
            """INSERT INTO daily_ohlcv (symbol, date, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, date) DO UPDATE SET
                 open=excluded.open, high=excluded.high, low=excluded.low,
                 close=excluded.close, volume=excluded.volume""",
            [
                (symbol, r["date"], r["Open"], r["High"], r["Low"], r["Close"], r["Volume"])
                for r in rows
            ],
        )


# ── signal log ────────────────────────────────────────────────────────────────

def record_approved_signals(scan_id: int | None, approved: list[dict], market: dict) -> list[int]:
    """Insert one row per approved signal into signal_log. Returns list of inserted IDs.

    Called immediately after record_scan() so every approved signal has a permanent
    record with full technical context. Outcomes are filled in later by the monitor.
    """
    init_db()
    inserted_ids: list[int] = []
    nifty_dir   = market.get("niftyDirection")
    # Today's IST midnight in UTC — for deduplication check
    today_utc = (
        datetime.now(IST)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
        .isoformat()
    )

    for item in approved:
        c  = item.get("candidate", {})
        sc = item.get("score", {})
        scores = sc.get("scores", {})
        targets = c.get("targets", [0, 0, 0])
        instrument = c.get("instrument", "")

        try:
            with get_connection() as conn:
                # Skip duplicate: same instrument already logged today without an outcome
                existing = conn.execute(
                    "SELECT id FROM signal_log WHERE instrument = ? AND created_at >= ? AND outcome IS NULL",
                    (instrument, today_utc),
                ).fetchone()
                if existing:
                    inserted_ids.append(existing["id"])
                    logger.debug("signal_log dedup: %s already logged today (id=%s)", instrument, existing["id"])
                    continue

                cursor = conn.execute(
                    """
                    INSERT INTO signal_log (
                        created_at, scan_id,
                        instrument, underlying, direction, strike_type, expiry, dte,
                        entry, stop_loss, target_1, target_2, target_3, rr, lot_size,
                        score_total, score_trend, score_momentum, score_volume,
                        score_oc, score_sentiment, score_rr,
                        spot_price, ema20, ema50, ema200, rsi, adx, macd, macd_signal,
                        atr, rel_volume, data_age, supertrend_bull,
                        pd_breakout, tf15_aligned, vwap, vwap_confirmed,
                        gap_up, gap_down, gap_pct,
                        sr_breakout, near_resistance, near_support, resistance, support,
                        atm_iv, iv_rank, pcr, oi_change_pct, option_volume, spread_pct,
                        max_pain_dist, delta, theta, vega,
                        india_vix, market_regime, breadth, nifty_direction
                    ) VALUES (
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        datetime.now(timezone.utc).isoformat(),
                        scan_id,
                        c.get("instrument", ""),
                        c.get("underlying", ""),
                        c.get("direction", ""),
                        c.get("strikeType"),
                        c.get("expiry"),
                        c.get("dte"),
                        float(c.get("entry") or 0),
                        float(c.get("stopLoss") or 0),
                        float(targets[0]) if len(targets) > 0 else 0.0,
                        float(targets[1]) if len(targets) > 1 else 0.0,
                        float(targets[2]) if len(targets) > 2 else 0.0,
                        c.get("rr"),
                        c.get("lotSize"),
                        sc.get("total", 0),
                        scores.get("trend"),
                        scores.get("momentum"),
                        scores.get("volume"),
                        scores.get("optionChain"),
                        scores.get("sentiment"),
                        scores.get("riskReward"),
                        c.get("spotPrice") or c.get("ema20"),
                        c.get("ema20"),
                        c.get("ema50"),
                        c.get("ema200"),
                        c.get("rsi"),
                        c.get("adx"),
                        c.get("macd"),
                        c.get("macdSignal"),
                        c.get("atr"),
                        c.get("relativeVolume"),
                        c.get("dataAge"),
                        1 if c.get("supertrendBullish") else 0,
                        1 if c.get("pdBreakout") else 0,
                        1 if c.get("tf15Aligned") else 0,
                        c.get("vwap"),
                        1 if c.get("vwapConfirmed") else 0,
                        1 if c.get("gapUp") else 0,
                        1 if c.get("gapDown") else 0,
                        c.get("gapPct"),
                        1 if c.get("srBreakout") else 0,
                        1 if c.get("nearResistance") else 0,
                        1 if c.get("nearSupport") else 0,
                        c.get("resistance"),
                        c.get("support"),
                        c.get("atmIV"),
                        c.get("ivRank"),
                        c.get("pcr"),
                        c.get("oiChangePct"),
                        c.get("optionVolume"),
                        c.get("spreadPct"),
                        c.get("maxPainDistancePct"),
                        c.get("delta"),
                        c.get("theta"),
                        c.get("vega"),
                        market.get("indiaVix"),
                        market.get("regime"),
                        market.get("breadth"),
                        nifty_dir,
                    ),
                )
                inserted_ids.append(cursor.lastrowid)
        except Exception as exc:
            logger.warning("signal_log insert failed [%s]: %s", instrument, exc)

    return inserted_ids


def update_signal_outcome(
    signal_id: int,
    outcome: str,
    exit_price: float,
    pnl_r: float,
    exit_reason: str,
) -> None:
    """Fill in the outcome for a signal. Called by the price monitor on SL/T1/T2/T3 hit."""
    init_db()
    with get_connection() as conn:
        conn.execute(
            """UPDATE signal_log
               SET outcome = ?, exit_price = ?, pnl_r = ?, exit_at = ?, exit_reason = ?
               WHERE id = ? AND outcome IS NULL""",
            (
                outcome,
                exit_price,
                pnl_r,
                datetime.now(timezone.utc).isoformat(),
                exit_reason,
                signal_id,
            ),
        )


def link_signal_to_journal(signal_id: int, journal_id: int) -> None:
    """Link a signal_log row to its trade_journal row (and vice versa)."""
    init_db()
    with get_connection() as conn:
        conn.execute("UPDATE signal_log SET journal_id = ? WHERE id = ?", (journal_id, signal_id))
        conn.execute("UPDATE trade_journal SET signal_id = ? WHERE id = ?", (signal_id, journal_id))


def get_signal_analytics() -> dict:
    """Aggregate signal_log into analysis-ready stats.

    Slices outcome data by: score bucket, VIX regime, data_age,
    direction, strike_type, gap/vwap/sr flags, expiry type.
    Only rows with a filled outcome are included.
    """
    init_db()
    try:
        with get_connection() as conn:
            # ── Overall stats ────────────────────────────────────────────────
            total = conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0]
            closed = conn.execute(
                "SELECT COUNT(*) FROM signal_log WHERE outcome IS NOT NULL"
            ).fetchone()[0]
            wins = conn.execute(
                "SELECT COUNT(*) FROM signal_log WHERE outcome = 'win'"
            ).fetchone()[0]
            avg_pnl = conn.execute(
                "SELECT ROUND(AVG(pnl_r),2) FROM signal_log WHERE pnl_r IS NOT NULL"
            ).fetchone()[0]
            best = conn.execute(
                "SELECT ROUND(MAX(pnl_r),2) FROM signal_log WHERE pnl_r IS NOT NULL"
            ).fetchone()[0]
            worst = conn.execute(
                "SELECT ROUND(MIN(pnl_r),2) FROM signal_log WHERE pnl_r IS NOT NULL"
            ).fetchone()[0]

            # ── By score bucket (70-74, 75-79, 80-84, 85-89, 90+) ──────────
            score_rows = conn.execute(
                """
                SELECT
                    CASE
                        WHEN score_total >= 90 THEN '90+'
                        WHEN score_total >= 85 THEN '85-89'
                        WHEN score_total >= 80 THEN '80-84'
                        WHEN score_total >= 75 THEN '75-79'
                        ELSE '70-74'
                    END AS bucket,
                    COUNT(*)                           AS total,
                    SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(pnl_r), 2)               AS avg_pnl
                FROM signal_log WHERE outcome IS NOT NULL
                GROUP BY bucket ORDER BY bucket DESC
                """
            ).fetchall()

            # ── By VIX regime ────────────────────────────────────────────────
            vix_rows = conn.execute(
                """
                SELECT
                    CASE
                        WHEN india_vix < 14  THEN '<14 calm'
                        WHEN india_vix < 18  THEN '14-18 normal'
                        WHEN india_vix < 22  THEN '18-22 elevated'
                        ELSE '22+ high'
                    END AS regime,
                    COUNT(*) AS total,
                    SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(pnl_r),2) AS avg_pnl
                FROM signal_log WHERE outcome IS NOT NULL AND india_vix IS NOT NULL
                GROUP BY regime
                """
            ).fetchall()

            # ── By data source (angel-5min vs daily-fallback) ─────────────
            age_rows = conn.execute(
                """
                SELECT data_age,
                    COUNT(*) AS total,
                    SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(pnl_r),2) AS avg_pnl
                FROM signal_log WHERE outcome IS NOT NULL AND data_age IS NOT NULL
                GROUP BY data_age
                """
            ).fetchall()

            # ── By condition flags ────────────────────────────────────────
            def _flag_stats(flag_col: str) -> list[dict]:
                rows = conn.execute(
                    f"""
                    SELECT {flag_col} AS flag,
                        COUNT(*) AS total,
                        SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins,
                        ROUND(AVG(pnl_r),2) AS avg_pnl
                    FROM signal_log WHERE outcome IS NOT NULL AND {flag_col} IS NOT NULL
                    GROUP BY {flag_col}
                    """
                ).fetchall()
                return [{"flag": bool(r[0]), "total": r[1], "wins": r[2], "avg_pnl": r[3]}
                        for r in rows]

            # ── By expiry type ────────────────────────────────────────────
            expiry_rows = conn.execute(
                """
                SELECT expiry,
                    COUNT(*) AS total,
                    SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(pnl_r),2) AS avg_pnl
                FROM signal_log WHERE outcome IS NOT NULL AND expiry IS NOT NULL
                GROUP BY expiry
                """
            ).fetchall()

            # ── By strike type ────────────────────────────────────────────
            strike_rows = conn.execute(
                """
                SELECT strike_type,
                    COUNT(*) AS total,
                    SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins,
                    ROUND(AVG(pnl_r),2) AS avg_pnl
                FROM signal_log WHERE outcome IS NOT NULL AND strike_type IS NOT NULL
                GROUP BY strike_type
                """
            ).fetchall()

            # ── Exit reason breakdown ─────────────────────────────────────
            exit_rows = conn.execute(
                """
                SELECT exit_reason,
                    COUNT(*) AS total,
                    ROUND(AVG(pnl_r),2) AS avg_pnl
                FROM signal_log WHERE exit_reason IS NOT NULL
                GROUP BY exit_reason ORDER BY total DESC
                """
            ).fetchall()

            def _rows(rs) -> list[dict]:
                return [dict(r) for r in rs]

        return {
            "overview": {
                "totalSignals":  total,
                "closedSignals": closed,
                "openSignals":   total - closed,
                "wins":          wins,
                "losses":        closed - wins,
                "winRate":       round(wins / closed * 100, 1) if closed > 0 else None,
                "avgPnlR":       avg_pnl,
                "bestTradeR":    best,
                "worstTradeR":   worst,
            },
            "byScoreBucket":   _rows(score_rows),
            "byVixRegime":     _rows(vix_rows),
            "byDataAge":       _rows(age_rows),
            "byVwapConfirmed": _flag_stats("vwap_confirmed"),
            "byGapSignal":     _flag_stats("gap_up"),
            "bySrBreakout":    _flag_stats("sr_breakout"),
            "byTf15Aligned":   _flag_stats("tf15_aligned"),
            "byPdBreakout":    _flag_stats("pd_breakout"),
            "byExpiryType":    _rows(expiry_rows),
            "byStrikeType":    _rows(strike_rows),
            "byExitReason":    _rows(exit_rows),
        }
    except Exception as exc:
        logger.warning("signal_analytics error: %s", exc)
        return {"overview": {}, "error": str(exc)}


def prune_scan_audit(keep_days: int = 30) -> int:
    """Delete scan_audit rows older than keep_days. Returns number of rows deleted."""
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM scan_audit WHERE created_at < ?", (cutoff,)
        )
        deleted = cursor.rowcount
    if deleted:
        logger.info("scan_audit pruned: %d rows older than %d days removed", deleted, keep_days)
    return deleted


def get_recent_signals(limit: int = 50, outcome_filter: str | None = None) -> list[dict]:
    """Return recent signal_log rows, newest first."""
    init_db()
    with get_connection() as conn:
        if outcome_filter:
            rows = conn.execute(
                "SELECT * FROM signal_log WHERE outcome = ? ORDER BY id DESC LIMIT ?",
                (outcome_filter, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM signal_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


_JOURNAL_UPDATABLE = {"exit_price", "outcome", "pnl_r", "status", "notes"}


def update_journal_entry(entry_id: int, updates: dict) -> bool:
    fields = {k: v for k, v in updates.items() if k in _JOURNAL_UPDATABLE and v is not None}
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values     = list(fields.values()) + [entry_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE trade_journal SET {set_clause} WHERE id = ?", values)
    return True
