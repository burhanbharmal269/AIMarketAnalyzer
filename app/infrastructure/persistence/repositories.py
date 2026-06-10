"""Concrete repository implementations — bridge between domain and SQLAlchemy models.

These delegate to existing storage.py during migration.
New code uses the repository interfaces; old code uses storage.py directly.
Both can coexist until migration is complete.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

from app.application.ports.repositories import ITradeRepository, ISignalRepository, IScanRepository
from app.domain.trade.entities import Trade
from app.domain.trade.value_objects import TradeStatus
from app.domain.signal.entities import Signal

logger = logging.getLogger(__name__)


class StorageTradeRepository(ITradeRepository):
    """Delegates to existing storage.py — zero behavioral change during Phase 4 migration."""

    async def save(self, trade: Trade) -> int:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._save_sync, trade)

    def _save_sync(self, trade: Trade) -> int:
        from app.services.storage import add_journal_entry
        return add_journal_entry({
            "instrument":      trade.instrument,
            "direction":       trade.direction,
            "entry":           trade.entry,
            "stopLoss":        trade.stop_loss,
            "targets":         [trade.target_1, trade.target_2, trade.target_3],
            "confidenceScore": trade.confidence_score,
            "status":          trade.status.value,
            "notes":           trade.notes or "",
            "lots":            trade.lots,
        }) or 0

    async def get_by_id(self, trade_id: int) -> Trade | None:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_by_id_sync, trade_id)

    def _get_by_id_sync(self, trade_id: int) -> Trade | None:
        from app.services.storage import get_journal_entries
        entries = get_journal_entries(limit=200)
        for e in entries:
            if e.get("id") == trade_id:
                return self._row_to_trade(e)
        return None

    async def get_open_trades(self) -> list[Trade]:
        import asyncio
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(
            None, lambda: __import__("app.services.storage", fromlist=["get_journal_entries"]).get_journal_entries(limit=100)
        )
        return [self._row_to_trade(r) for r in rows if r.get("status") in ("open", "paper")]

    async def get_recent(self, limit: int = 50) -> list[Trade]:
        import asyncio
        loop = asyncio.get_running_loop()
        from app.services.storage import get_journal_entries
        rows = await loop.run_in_executor(None, lambda: get_journal_entries(limit=limit))
        return [self._row_to_trade(r) for r in rows]

    async def update_outcome(
        self, trade_id, outcome, exit_price, pnl_r, pnl_inr=0.0, exit_reason=""
    ) -> None:
        import asyncio
        loop = asyncio.get_running_loop()
        from app.services.storage import update_journal_outcome
        await loop.run_in_executor(
            None,
            lambda: update_journal_outcome(trade_id, outcome, exit_price, pnl_r),
        )

    async def compute_risk_state(self, capital: float, risk_pct: float) -> dict:
        import asyncio
        loop = asyncio.get_running_loop()
        from app.services.storage import compute_risk_state
        return await loop.run_in_executor(None, lambda: compute_risk_state(risk_pct=risk_pct))

    @staticmethod
    def _row_to_trade(row: dict) -> Trade:
        targets = row.get("targets", [0, 0, 0])
        return Trade(
            id=row.get("id"),
            signal_id=row.get("signal_id"),
            instrument=row.get("instrument", ""),
            direction=row.get("direction", "BUY"),
            entry=float(row.get("entry", 0) or 0),
            stop_loss=float(row.get("stopLoss", row.get("stop_loss", 0)) or 0),
            target_1=float(targets[0]) if len(targets) > 0 else 0.0,
            target_2=float(targets[1]) if len(targets) > 1 else 0.0,
            target_3=float(targets[2]) if len(targets) > 2 else 0.0,
            lots=int(row.get("lots", 1) or 1),
            confidence_score=float(row.get("confidenceScore", row.get("confidence_score", 0)) or 0),
            status=TradeStatus(row.get("status", "paper")),
            notes=row.get("notes", ""),
            exit_price=row.get("exitPrice", row.get("exit_price")),
            pnl_r=row.get("pnlR", row.get("pnl_r")),
        )


class StorageSignalRepository(ISignalRepository):
    """Delegates to existing storage.py."""

    async def save(self, signal: dict, scan_id: int | None = None) -> int:
        import asyncio
        loop = asyncio.get_running_loop()
        from app.services.storage import record_approved_signals
        results = await loop.run_in_executor(
            None,
            lambda: record_approved_signals(scan_id, [signal], {}),
        )
        return results[0] if results else 0

    async def get_recent(self, limit: int = 100) -> list[dict]:
        import asyncio
        loop = asyncio.get_running_loop()
        from app.services.storage import get_signal_log
        return await loop.run_in_executor(None, lambda: get_signal_log(limit=limit))

    async def get_analytics(self) -> dict:
        import asyncio
        loop = asyncio.get_running_loop()
        from app.services.storage import get_signal_analytics
        return await loop.run_in_executor(None, get_signal_analytics)

    async def link_to_trade(self, signal_id: int, trade_id: int) -> None:
        import asyncio
        loop = asyncio.get_running_loop()
        from app.services.storage import link_signal_to_journal
        await loop.run_in_executor(None, lambda: link_signal_to_journal(signal_id, trade_id))


class StorageScanRepository(IScanRepository):
    """Delegates to existing storage.py."""

    async def save(self, result: dict) -> int:
        import asyncio
        loop = asyncio.get_running_loop()
        from app.services.storage import record_scan
        return await loop.run_in_executor(None, lambda: record_scan(result)) or 0

    async def get_recent(self, limit: int = 10) -> list[dict]:
        import asyncio
        loop = asyncio.get_running_loop()
        from app.services.storage import get_scan_audit
        return await loop.run_in_executor(None, lambda: get_scan_audit(limit=limit))

    async def prune(self, keep_days: int = 30) -> int:
        import asyncio
        loop = asyncio.get_running_loop()
        from app.services.storage import prune_scan_audit
        await loop.run_in_executor(None, lambda: prune_scan_audit(keep_days=keep_days))
        return 0
