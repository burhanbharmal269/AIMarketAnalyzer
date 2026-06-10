"""Initial schema — mirrors existing SQLite tables.

Revision ID: 001
Revises:
Create Date: 2026-06-10

This migration creates all tables from the new SQLAlchemy 2.0 ORM models.
If you're migrating from existing SQLite, run:
    alembic upgrade head

The models already match the existing SQLite schema, so no data loss occurs.
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scan_audit",
        sa.Column("id",               sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("approved_count",   sa.Integer(), nullable=False, default=0),
        sa.Column("rejected_count",   sa.Integer(), nullable=False, default=0),
        sa.Column("no_trade",         sa.Boolean(), nullable=False, default=False),
        sa.Column("scan_duration_ms", sa.Integer(), nullable=True),
        sa.Column("data_source",      sa.String(32), nullable=True),
        sa.Column("payload",          sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scan_audit_created_at", "scan_audit", ["created_at"])

    op.create_table(
        "signal_log",
        sa.Column("id",             sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at",     sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("scan_id",        sa.BigInteger(), sa.ForeignKey("scan_audit.id", ondelete="SET NULL"), nullable=True),
        sa.Column("journal_id",     sa.BigInteger(), nullable=True),
        sa.Column("instrument",     sa.String(64), nullable=False),
        sa.Column("underlying",     sa.String(32), nullable=True),
        sa.Column("direction",      sa.String(8), nullable=False),
        sa.Column("setup_type",     sa.String(32), nullable=True),
        sa.Column("strike_type",    sa.String(8), nullable=True),
        sa.Column("expiry_type",    sa.String(16), nullable=True),
        sa.Column("dte",            sa.Integer(), nullable=True),
        sa.Column("entry",          sa.Float(), nullable=False, default=0.0),
        sa.Column("stop_loss",      sa.Float(), nullable=False, default=0.0),
        sa.Column("target_1",       sa.Float(), nullable=False, default=0.0),
        sa.Column("target_2",       sa.Float(), nullable=False, default=0.0),
        sa.Column("target_3",       sa.Float(), nullable=False, default=0.0),
        sa.Column("rr",             sa.Float(), nullable=False, default=0.0),
        sa.Column("lots",           sa.Integer(), nullable=False, default=1),
        sa.Column("score_total",    sa.Float(), nullable=False, default=0.0),
        sa.Column("score_trend",    sa.Float(), nullable=False, default=0.0),
        sa.Column("score_momentum", sa.Float(), nullable=False, default=0.0),
        sa.Column("score_volume",   sa.Float(), nullable=False, default=0.0),
        sa.Column("score_oc",       sa.Float(), nullable=False, default=0.0),
        sa.Column("score_sentiment",sa.Float(), nullable=False, default=0.0),
        sa.Column("score_rr",       sa.Float(), nullable=False, default=0.0),
        sa.Column("score_news",     sa.Float(), nullable=False, default=0.0),
        sa.Column("ai_score",       sa.Float(), nullable=True),
        sa.Column("spot_price",     sa.Float(), nullable=False, default=0.0),
        sa.Column("ema20",          sa.Float(), nullable=False, default=0.0),
        sa.Column("ema50",          sa.Float(), nullable=False, default=0.0),
        sa.Column("ema200",         sa.Float(), nullable=False, default=0.0),
        sa.Column("rsi",            sa.Float(), nullable=False, default=0.0),
        sa.Column("adx",            sa.Float(), nullable=False, default=0.0),
        sa.Column("atr",            sa.Float(), nullable=False, default=0.0),
        sa.Column("vwap",           sa.Float(), nullable=False, default=0.0),
        sa.Column("rel_volume",     sa.Float(), nullable=False, default=0.0),
        sa.Column("atm_iv",         sa.Float(), nullable=False, default=0.0),
        sa.Column("iv_rank",        sa.Float(), nullable=True),
        sa.Column("pcr",            sa.Float(), nullable=False, default=0.0),
        sa.Column("oi_change_pct",  sa.Float(), nullable=False, default=0.0),
        sa.Column("option_volume",  sa.Float(), nullable=False, default=0.0),
        sa.Column("spread_pct",     sa.Float(), nullable=False, default=0.0),
        sa.Column("max_pain_dist",  sa.Float(), nullable=False, default=0.0),
        sa.Column("india_vix",      sa.Float(), nullable=False, default=0.0),
        sa.Column("market_regime",  sa.String(32), nullable=True),
        sa.Column("outcome",        sa.String(16), nullable=True),
        sa.Column("exit_price",     sa.Float(), nullable=True),
        sa.Column("pnl_r",          sa.Float(), nullable=True),
        sa.Column("exit_at",        sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_reason",    sa.String(32), nullable=True),
        sa.Column("payload",        sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signal_log_created_at", "signal_log", ["created_at"])
    op.create_index("ix_signal_log_underlying",  "signal_log", ["underlying"])
    op.create_index("ix_signal_log_outcome",     "signal_log", ["outcome"])

    op.create_table(
        "trade_journal",
        sa.Column("id",               sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("instrument",       sa.String(64), nullable=False),
        sa.Column("underlying",       sa.String(32), nullable=True),
        sa.Column("direction",        sa.String(8), nullable=False, default="BUY"),
        sa.Column("entry",            sa.Float(), nullable=False),
        sa.Column("stop_loss",        sa.Float(), nullable=False),
        sa.Column("target_1",         sa.Float(), nullable=False, default=0.0),
        sa.Column("target_2",         sa.Float(), nullable=False, default=0.0),
        sa.Column("target_3",         sa.Float(), nullable=False, default=0.0),
        sa.Column("lots",             sa.Integer(), nullable=False, default=1),
        sa.Column("quantity",         sa.Integer(), nullable=False, default=0),
        sa.Column("confidence_score", sa.Float(), nullable=False, default=0.0),
        sa.Column("status",           sa.String(16), nullable=False, default="paper"),
        sa.Column("notes",            sa.Text(), nullable=True),
        sa.Column("exit_price",       sa.Float(), nullable=True),
        sa.Column("pnl_r",            sa.Float(), nullable=True),
        sa.Column("pnl_inr",          sa.Float(), nullable=True),
        sa.Column("exit_at",          sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_reason",      sa.String(32), nullable=True),
        sa.Column("signal_id",        sa.BigInteger(), sa.ForeignKey("signal_log.id", ondelete="SET NULL"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_journal_created_at", "trade_journal", ["created_at"])
    op.create_index("ix_trade_journal_status",     "trade_journal", ["status"])
    op.create_index("ix_trade_journal_underlying", "trade_journal", ["underlying"])

    op.create_table(
        "iv_history",
        sa.Column("id",     sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("date",   sa.String(10), nullable=False),
        sa.Column("iv_pct", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "date", name="uq_iv_history_symbol_date"),
    )

    op.create_table(
        "daily_ohlcv",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("date",   sa.String(10), nullable=False),
        sa.Column("open",   sa.Float(), nullable=False),
        sa.Column("high",   sa.Float(), nullable=False),
        sa.Column("low",    sa.Float(), nullable=False),
        sa.Column("close",  sa.Float(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "date"),
    )

    op.create_table(
        "pending_alerts",
        sa.Column("id",         sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("message",    sa.Text(), nullable=False),
        sa.Column("channel",    sa.String(32), nullable=False, default="telegram"),
        sa.Column("attempts",   sa.Integer(), nullable=False, default=0),
        sa.Column("next_retry", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status",     sa.String(16), nullable=False, default="pending"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pending_alerts_status_retry", "pending_alerts", ["status", "next_retry"])


def downgrade() -> None:
    op.drop_table("pending_alerts")
    op.drop_table("daily_ohlcv")
    op.drop_table("iv_history")
    op.drop_table("trade_journal")
    op.drop_table("signal_log")
    op.drop_table("scan_audit")
