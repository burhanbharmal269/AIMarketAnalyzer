import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app.config import settings


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
        now = datetime.now(timezone.utc)
        cutoffs = {
            "dailyLossPct":       (now - timedelta(hours=24)).isoformat(),
            "weeklyDrawdownPct":  (now - timedelta(days=7)).isoformat(),
            "monthlyDrawdownPct": (now - timedelta(days=30)).isoformat(),
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
        return result
    except Exception:
        return _SAFE


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
