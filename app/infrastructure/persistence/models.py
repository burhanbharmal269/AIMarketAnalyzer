"""SQLAlchemy 2.0 ORM models — PostgreSQL-ready, SQLite-compatible.

Mirrors the existing SQLite schema so Alembic can generate a clean migration.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, Index, Integer,
    String, Text, UniqueConstraint, ForeignKey, JSON,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ScanAudit(Base):
    __tablename__ = "scan_audit"

    id:               Mapped[int]            = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at:       Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_count:   Mapped[int]            = mapped_column(Integer, default=0)
    rejected_count:   Mapped[int]            = mapped_column(Integer, default=0)
    no_trade:         Mapped[bool]           = mapped_column(Boolean, default=False)
    scan_duration_ms: Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    data_source:      Mapped[Optional[str]]  = mapped_column(String(32), nullable=True)
    payload:          Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    signals: Mapped[list["SignalLog"]] = relationship(back_populates="scan", lazy="select")

    __table_args__ = (
        Index("ix_scan_audit_created_at", "created_at"),
    )


class TradeJournal(Base):
    __tablename__ = "trade_journal"

    id:               Mapped[int]            = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at:       Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=func.now())
    instrument:       Mapped[str]            = mapped_column(String(64))
    underlying:       Mapped[Optional[str]]  = mapped_column(String(32), nullable=True)
    direction:        Mapped[str]            = mapped_column(String(8), default="BUY")
    entry:            Mapped[float]          = mapped_column(Float)
    stop_loss:        Mapped[float]          = mapped_column(Float)
    target_1:         Mapped[float]          = mapped_column(Float, default=0.0)
    target_2:         Mapped[float]          = mapped_column(Float, default=0.0)
    target_3:         Mapped[float]          = mapped_column(Float, default=0.0)
    lots:             Mapped[int]            = mapped_column(Integer, default=1)
    quantity:         Mapped[int]            = mapped_column(Integer, default=0)
    confidence_score: Mapped[float]          = mapped_column(Float, default=0.0)
    status:           Mapped[str]            = mapped_column(String(16), default="paper")
    notes:            Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    exit_price:       Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_r:            Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_inr:          Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_at:          Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_reason:      Mapped[Optional[str]]  = mapped_column(String(32), nullable=True)
    signal_id:        Mapped[Optional[int]]  = mapped_column(ForeignKey("signal_log.id", ondelete="SET NULL"), nullable=True)

    signal: Mapped[Optional["SignalLog"]] = relationship(foreign_keys=[signal_id], lazy="select")

    __table_args__ = (
        Index("ix_trade_journal_created_at", "created_at"),
        Index("ix_trade_journal_status", "status"),
        Index("ix_trade_journal_underlying", "underlying"),
    )


class SignalLog(Base):
    __tablename__ = "signal_log"

    id:              Mapped[int]            = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at:      Mapped[datetime]       = mapped_column(DateTime(timezone=True), server_default=func.now())
    scan_id:         Mapped[Optional[int]]  = mapped_column(ForeignKey("scan_audit.id", ondelete="SET NULL"), nullable=True)
    journal_id:      Mapped[Optional[int]]  = mapped_column(ForeignKey("trade_journal.id", ondelete="SET NULL"), nullable=True)
    instrument:      Mapped[str]            = mapped_column(String(64))
    underlying:      Mapped[Optional[str]]  = mapped_column(String(32), nullable=True)
    direction:       Mapped[str]            = mapped_column(String(8))
    setup_type:      Mapped[Optional[str]]  = mapped_column(String(32), nullable=True)
    strike_type:     Mapped[Optional[str]]  = mapped_column(String(8), nullable=True)
    expiry_type:     Mapped[Optional[str]]  = mapped_column(String(16), nullable=True)
    dte:             Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    # Entry
    entry:           Mapped[float]          = mapped_column(Float, default=0.0)
    stop_loss:       Mapped[float]          = mapped_column(Float, default=0.0)
    target_1:        Mapped[float]          = mapped_column(Float, default=0.0)
    target_2:        Mapped[float]          = mapped_column(Float, default=0.0)
    target_3:        Mapped[float]          = mapped_column(Float, default=0.0)
    rr:              Mapped[float]          = mapped_column(Float, default=0.0)
    lots:            Mapped[int]            = mapped_column(Integer, default=1)
    # Scores
    score_total:     Mapped[float]          = mapped_column(Float, default=0.0)
    score_trend:     Mapped[float]          = mapped_column(Float, default=0.0)
    score_momentum:  Mapped[float]          = mapped_column(Float, default=0.0)
    score_volume:    Mapped[float]          = mapped_column(Float, default=0.0)
    score_oc:        Mapped[float]          = mapped_column(Float, default=0.0)
    score_sentiment: Mapped[float]          = mapped_column(Float, default=0.0)
    score_rr:        Mapped[float]          = mapped_column(Float, default=0.0)
    score_news:      Mapped[float]          = mapped_column(Float, default=0.0)
    ai_score:        Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Technical
    spot_price:      Mapped[float]          = mapped_column(Float, default=0.0)
    ema20:           Mapped[float]          = mapped_column(Float, default=0.0)
    ema50:           Mapped[float]          = mapped_column(Float, default=0.0)
    ema200:          Mapped[float]          = mapped_column(Float, default=0.0)
    rsi:             Mapped[float]          = mapped_column(Float, default=0.0)
    adx:             Mapped[float]          = mapped_column(Float, default=0.0)
    atr:             Mapped[float]          = mapped_column(Float, default=0.0)
    vwap:            Mapped[float]          = mapped_column(Float, default=0.0)
    rel_volume:      Mapped[float]          = mapped_column(Float, default=0.0)
    # Options
    atm_iv:          Mapped[float]          = mapped_column(Float, default=0.0)
    iv_rank:         Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pcr:             Mapped[float]          = mapped_column(Float, default=0.0)
    oi_change_pct:   Mapped[float]          = mapped_column(Float, default=0.0)
    option_volume:   Mapped[float]          = mapped_column(Float, default=0.0)
    spread_pct:      Mapped[float]          = mapped_column(Float, default=0.0)
    max_pain_dist:   Mapped[float]          = mapped_column(Float, default=0.0)
    # Market
    india_vix:       Mapped[float]          = mapped_column(Float, default=0.0)
    market_regime:   Mapped[Optional[str]]  = mapped_column(String(32), nullable=True)
    # Outcome
    outcome:         Mapped[Optional[str]]  = mapped_column(String(16), nullable=True)
    exit_price:      Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_r:           Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_at:         Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_reason:     Mapped[Optional[str]]  = mapped_column(String(32), nullable=True)
    # Full payload for reprocessing
    payload:         Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    scan: Mapped[Optional["ScanAudit"]] = relationship(back_populates="signals", lazy="select")

    __table_args__ = (
        Index("ix_signal_log_created_at", "created_at"),
        Index("ix_signal_log_underlying", "underlying"),
        Index("ix_signal_log_outcome", "outcome"),
    )


class IVHistory(Base):
    __tablename__ = "iv_history"

    id:      Mapped[int]   = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol:  Mapped[str]   = mapped_column(String(32))
    date:    Mapped[str]   = mapped_column(String(10))
    iv_pct:  Mapped[float] = mapped_column(Float)

    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_iv_history_symbol_date"),)


class DailyOHLCV(Base):
    __tablename__ = "daily_ohlcv"

    symbol:  Mapped[str]   = mapped_column(String(32), primary_key=True)
    date:    Mapped[str]   = mapped_column(String(10), primary_key=True)
    open:    Mapped[float] = mapped_column(Float)
    high:    Mapped[float] = mapped_column(Float)
    low:     Mapped[float] = mapped_column(Float)
    close:   Mapped[float] = mapped_column(Float)
    volume:  Mapped[int]   = mapped_column(BigInteger)


class PendingAlert(Base):
    __tablename__ = "pending_alerts"

    id:          Mapped[int]      = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    message:     Mapped[str]      = mapped_column(Text)
    channel:     Mapped[str]      = mapped_column(String(32), default="telegram")
    attempts:    Mapped[int]      = mapped_column(Integer, default=0)
    next_retry:  Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status:      Mapped[str]      = mapped_column(String(16), default="pending")

    __table_args__ = (
        Index("ix_pending_alerts_status_retry", "status", "next_retry"),
    )
