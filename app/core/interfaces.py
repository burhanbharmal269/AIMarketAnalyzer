"""Abstract contracts for every extensible component.

Design rules:
- Nothing in this file imports from app.services or app.data_sources.
- All new signal types, data providers, scorers, and gates must implement
  the relevant interface before any integration code is written.
- Use ABCs for components we own (IScorer, IGate, ISignalStrategy).
- Use Protocol for external contracts we adapt to (IDataSource).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable


# ── Scoring ───────────────────────────────────────────────────────────────────

class IScorer(ABC):
    """Contract for a single scoring category (trend, momentum, volume, etc.).

    To add a new scoring dimension:
      1. Create a new file in app/services/scoring/
      2. Subclass IScorer and set `category` and `max_score`
      3. Implement `score()` — must return int in [0, max_score]
      4. Register in OptionsTradingStrategy (or whichever strategy uses it)
    """
    category: str    # must match a key in SCORE_CATEGORIES
    max_score: int   # ceiling for this category's contribution

    @abstractmethod
    def score(self, candidate: dict, market: dict) -> int:
        """Return a clamped integer score in [0, max_score]."""


# ── Gates ─────────────────────────────────────────────────────────────────────

class IGate(ABC):
    """Contract for a binary risk filter.

    To add a new hard gate:
      1. Create a new file in app/services/gates/
      2. Subclass IGate
      3. Implement `check()` — return None to pass, or a reason string to reject
      4. Register in OptionsTradingStrategy.gates (or the relevant strategy)

    Gates are evaluated in registration order. The first failure short-circuits
    nothing — all failures are collected and returned together so the UI can
    explain every rejection reason at once.
    """

    @abstractmethod
    def check(
        self,
        candidate: dict,
        market: dict,
        risk_state: dict,
        settings: dict,
    ) -> str | None:
        """None → candidate passes this gate.
        Non-empty string → candidate rejected; the string is the displayed reason.
        """


# ── Signal strategies ─────────────────────────────────────────────────────────

class ISignalStrategy(ABC):
    """Pluggable strategy for generating, scoring, and sizing signals.

    To add a new signal type (equity swing, equity long-term, futures, etc.):
      1. Create a new file in app/services/strategies/
      2. Subclass ISignalStrategy and set `signal_type`
      3. Implement the three abstract methods
      4. Register the strategy in the scan router or scan service

    The base class provides `run_scan()` as a template method — override it
    only if the scan orchestration itself differs (e.g. different post-filters).
    """
    signal_type: str   # "options" | "equity_swing" | "equity_longterm" | ...

    @abstractmethod
    def score_candidate(self, candidate: dict, market: dict) -> dict:
        """Return score dict: {scores: {category: int}, total: int, rawTotal: int}."""

    @abstractmethod
    def check_gates(
        self,
        candidate: dict,
        market: dict,
        risk_state: dict,
        settings: dict,
    ) -> list[str]:
        """Return list of failure reason strings. Empty list = all gates pass."""

    @abstractmethod
    def compute_position_size(self, candidate: dict, settings: dict) -> dict:
        """Return sizing dict: {rupeeRisk, perUnitRisk, lotRisk, lots, quantity}."""


# ── Data sources ──────────────────────────────────────────────────────────────

@runtime_checkable
class IDataSource(Protocol):
    """Structural protocol for any market data provider.

    Both NSELive and any future broker adapter (Zerodha, Upstox, etc.) must
    expose this surface. Use `isinstance(obj, IDataSource)` to verify.
    """

    def is_available(self) -> bool: ...

    def get_live_candidates(self) -> list[dict]: ...

    def get_market_snapshot(self) -> dict: ...
