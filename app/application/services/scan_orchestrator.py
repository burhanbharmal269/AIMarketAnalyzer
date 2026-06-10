"""ScanOrchestrator — async pipeline coordinating all scan phases.

Replaces services/scan_service.py as the single entry point for scans.
Old code path is preserved: existing scan_service.py still works until
this orchestrator is wired into the router in Phase 7.

Pipeline:
  1. Pre-flight checks (market hours, VIX gate, risk state)
  2. Fetch F&O universe from market data provider
  3. Fetch enriched candidates (quotes, option chains, technicals)
  4. Gate + score each candidate (delegates to existing services)
  5. Risk sizing via RiskEngine
  6. AI agent ensemble (optional, async)
  7. Publish events (Telegram, journal, monitor)
  8. Persist scan audit
"""
from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.application.ports.market_data import IMarketDataProvider
from app.application.ports.cache import ICacheProvider
from app.application.ports.notification import INotificationProvider
from app.application.services.risk_engine import RiskEngine
from app.domain.risk.entities import RiskState
from app.domain.signal.entities import Candidate, Signal, ScoreBreakdown
from app.core.constants import DEFAULT_SCAN_SETTINGS

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    scan_id:       int   = 0
    approved:      list[dict] = field(default_factory=list)
    rejected:      list[dict] = field(default_factory=list)
    no_trade:      bool  = False
    no_trade_reason: str = ""
    duration_ms:   int   = 0
    candidates_checked: int = 0
    data_source:   str   = "composite"
    started_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def summary(self) -> str:
        if self.no_trade:
            return f"NO TRADE — {self.no_trade_reason}"
        return (
            f"{len(self.approved)} approved, {len(self.rejected)} rejected "
            f"from {self.candidates_checked} candidates in {self.duration_ms}ms"
        )


class ScanOrchestrator:
    """Async scan pipeline. Each phase is a named method for testability."""

    def __init__(
        self,
        market_data:  IMarketDataProvider,
        cache:        ICacheProvider,
        risk_engine:  RiskEngine,
        notifier:     INotificationProvider | None = None,
        ai_orchestrator=None,   # app.application.agents.orchestrator.AIOrchestrator
        event_bus=None,         # app.application.events.bus.AsyncEventBus
    ) -> None:
        self._market = market_data
        self._cache  = cache
        self._risk   = risk_engine
        self._notify = notifier
        self._ai     = ai_orchestrator
        self._bus    = event_bus
        self._scanning = False   # simple mutex

    async def run(self, settings: dict | None = None) -> ScanResult:
        """Execute a full scan and return the result."""
        if self._scanning:
            from app.core.exceptions import ScanInProgressError
            raise ScanInProgressError()

        self._scanning = True
        result = ScanResult()
        t0     = time.monotonic()

        try:
            settings = {**DEFAULT_SCAN_SETTINGS, **(settings or {})}

            # ── Phase 1: pre-flight ────────────────────────────────────────────
            no_trade, reason = await self._preflight(settings)
            if no_trade:
                result.no_trade        = True
                result.no_trade_reason = reason
                logger.info("Scan blocked: %s", reason)
                return result

            # ── Phase 2: check cache ───────────────────────────────────────────
            cached = await self._cache.get_scan_result()
            if cached:
                logger.info("Serving scan from cache")
                return self._dict_to_result(cached)

            # ── Phase 3: fetch candidates ──────────────────────────────────────
            raw_candidates = await self._fetch_candidates(settings)
            result.candidates_checked = len(raw_candidates)
            logger.info("Fetched %d candidates for evaluation", len(raw_candidates))

            # ── Phase 4: gate + score ──────────────────────────────────────────
            approved_raw, rejected_raw = await self._score_and_gate(
                raw_candidates, settings
            )

            # ── Phase 5: risk sizing ───────────────────────────────────────────
            portfolio_state = await self._load_portfolio(settings)
            approved_final  = await self._apply_risk(approved_raw, portfolio_state, settings)

            # ── Phase 6: AI ensemble (if configured) ──────────────────────────
            if self._ai and approved_final:
                approved_final = await self._run_ai(approved_final, settings)

            # ── Phase 7: persist + notify ──────────────────────────────────────
            scan_id = await self._persist(approved_final, rejected_raw, settings)
            result.scan_id   = scan_id
            result.approved  = approved_final
            result.rejected  = rejected_raw

            # Cache the result
            await self._cache.set_scan_result(result.__dict__)

            # Notifications
            if self._notify and approved_final:
                await self._notify_signals(approved_final)

            # Emit domain events
            if self._bus:
                await self._emit_events(approved_final, scan_id)

        except Exception as exc:
            logger.exception("Scan pipeline error: %s", exc)
            result.no_trade        = True
            result.no_trade_reason = f"Scan error: {exc}"
        finally:
            self._scanning = False
            result.duration_ms = int((time.monotonic() - t0) * 1000)

        logger.info("Scan complete: %s", result.summary)
        return result

    # ── Pipeline phases ───────────────────────────────────────────────────────

    async def _preflight(self, settings: dict) -> tuple[bool, str]:
        """Returns (no_trade: bool, reason: str)."""
        # Delegate to existing scan_service preflight logic during migration
        try:
            from app.services.scan_service import _check_no_trade_conditions
            loop   = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, _check_no_trade_conditions, settings
            )
            if result:
                return True, result
        except (ImportError, AttributeError):
            pass   # function may not exist in all versions

        return False, ""

    async def _fetch_candidates(self, settings: dict) -> list[dict]:
        """Fetch raw candidate list from existing scanner or market data provider."""
        loop = asyncio.get_running_loop()
        try:
            from app.services.scan_service import fetch_candidates
            return await loop.run_in_executor(None, fetch_candidates, settings)
        except (ImportError, AttributeError):
            # Fallback: return empty list — existing scanner runs standalone
            return []

    async def _score_and_gate(
        self, candidates: list[dict], settings: dict
    ) -> tuple[list[dict], list[dict]]:
        """Run gates + scoring pipeline. Delegates to existing services."""
        loop = asyncio.get_running_loop()
        try:
            from app.services.scan_service import score_and_gate_candidates
            approved, rejected = await loop.run_in_executor(
                None, score_and_gate_candidates, candidates, settings
            )
            return approved, rejected
        except (ImportError, AttributeError):
            return candidates, []

    async def _load_portfolio(self, settings: dict):
        """Fetch current portfolio state for risk evaluation."""
        loop = asyncio.get_running_loop()
        try:
            from app.services.storage import compute_risk_state
            state_dict = await loop.run_in_executor(
                None, lambda: compute_risk_state(risk_pct=settings.get("riskPercent", 2.0))
            )
            return RiskState.from_storage_dict(state_dict)
        except Exception as exc:
            logger.warning("Could not load portfolio state: %s", exc)
            return RiskState()

    async def _apply_risk(
        self, candidates: list[dict], risk_state: RiskState, settings: dict
    ) -> list[dict]:
        """Apply RiskEngine to compute lot sizing and filter hard-stop violations."""
        capital   = float(settings.get("accountCapital", 100_000))
        portfolio = risk_state.to_portfolio(capital)
        approved  = []

        for raw in candidates:
            try:
                candidate = Candidate.from_raw(raw)
                decision  = self._risk.evaluate(candidate, portfolio)
                if decision.approved:
                    raw["lots"]     = decision.lots
                    raw["quantity"] = decision.quantity
                    raw["lotRisk"]  = decision.lot_risk
                    approved.append(raw)
                else:
                    raw["gateFailures"] = raw.get("gateFailures", []) + decision.failures
                    logger.debug(
                        "Risk gate failed for %s: %s",
                        raw.get("instrument", "?"), decision.failures
                    )
            except Exception as exc:
                logger.warning("Risk evaluation error for %s: %s", raw.get("instrument"), exc)
                approved.append(raw)   # pass through on error — don't drop signal

        return approved

    async def _run_ai(self, candidates: list[dict], settings: dict) -> list[dict]:
        """Run AI ensemble — enriches candidates with AI scores and explanations."""
        if not self._ai:
            return candidates
        try:
            results = await self._ai.analyse_candidates(candidates, settings)
            # Merge AI output back into candidates
            by_instrument = {r.get("instrument"): r for r in results}
            for c in candidates:
                ai = by_instrument.get(c.get("instrument"), {})
                c["aiScore"]       = ai.get("score", 0)
                c["aiGrade"]       = ai.get("grade", "")
                c["explanation"]   = ai.get("explanation", c.get("explanation", ""))
                c["aiRecommendation"] = ai.get("recommendation", "")
            return candidates
        except Exception as exc:
            logger.warning("AI ensemble failed: %s", exc)
            return candidates

    async def _persist(
        self, approved: list[dict], rejected: list[dict], settings: dict
    ) -> int:
        """Persist scan audit record. Returns scan_id."""
        loop = asyncio.get_running_loop()
        try:
            from app.services.storage import record_scan, record_approved_signals
            scan_record = {
                "approved":      approved,
                "rejected":      rejected,
                "approvedCount": len(approved),
                "rejectedCount": len(rejected),
                "settings":      settings,
            }
            scan_id = await loop.run_in_executor(None, lambda: record_scan(scan_record))
            if approved:
                await loop.run_in_executor(
                    None, lambda: record_approved_signals(scan_id, approved, settings)
                )
            return scan_id or 0
        except Exception as exc:
            logger.warning("Persist failed: %s", exc)
            return 0

    async def _notify_signals(self, approved: list[dict]) -> None:
        for signal in approved[:3]:    # limit to top 3 to avoid spam
            try:
                await self._notify.send_signal_alert({"candidate": signal, "score": signal.get("score", {})})
            except Exception as exc:
                logger.warning("Notification failed: %s", exc)

    async def _emit_events(self, approved: list[dict], scan_id: int) -> None:
        from app.domain.signal.events import SignalApproved
        for signal in approved:
            event = SignalApproved(
                instrument=signal.get("instrument", ""),
                scan_id=scan_id,
                score=signal.get("score", {}).get("total", 0),
                direction=signal.get("direction", "BUY"),
            )
            await self._bus.publish(event)

    @staticmethod
    def _dict_to_result(d: dict) -> ScanResult:
        r = ScanResult()
        r.scan_id            = d.get("scan_id", 0)
        r.approved           = d.get("approved", [])
        r.rejected           = d.get("rejected", [])
        r.no_trade           = d.get("no_trade", False)
        r.no_trade_reason    = d.get("no_trade_reason", "")
        r.duration_ms        = d.get("duration_ms", 0)
        r.candidates_checked = d.get("candidates_checked", 0)
        return r
