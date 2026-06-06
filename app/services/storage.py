import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
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


def _add_column_if_missing(conn, table: str, column: str, col_def: str):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


# ── scan audit ────────────────────────────────────────────────────────────────

def record_scan(scan: dict):
    init_db()
    with get_connection() as conn:
        conn.execute(
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
        paper_pnls = [float(r["pnl_r"]) for r in rows if r["status"] in ("paper", "closed") and r["pnl_r"] is not None]
        live_pnls  = [float(r["pnl_r"]) for r in rows if r["status"] == "open"]

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
