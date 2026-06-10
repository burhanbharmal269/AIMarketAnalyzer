"""IAIProvider — secondary port for all LLM communication.

Callers never import openai/anthropic directly.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AIAnalysis:
    """Output from a single specialist AI agent."""
    agent_name:     str
    score:          float    # 0.0–1.0
    confidence:     float    # 0.0–1.0
    reasoning:      str
    recommendation: str      # "STRONG_BUY"|"BUY"|"NEUTRAL"|"AVOID"|"STRONG_AVOID"
    metadata:       dict     = field(default_factory=dict)


@dataclass
class AIRecommendation:
    """Aggregated output from all agents — final AI verdict."""
    composite_score:  float
    recommendation:   str
    explanation:      str
    agent_analyses:   list[AIAnalysis] = field(default_factory=list)
    regime:           str              = ""
    regime_note:      str              = ""
    shortlist:        list[str]        = field(default_factory=list)
    skipped:          dict             = field(default_factory=dict)

    @property
    def bullish(self) -> bool:
        return self.recommendation in ("STRONG_BUY", "BUY")

    @property
    def bearish(self) -> bool:
        return self.recommendation in ("STRONG_AVOID", "AVOID")


class IAIProvider(ABC):
    """Text-in / structured-out. No SDK-specific types exposed."""

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        user_prompt:   str,
        temperature:   float = 0.1,
        max_tokens:    int   = 500,
    ) -> dict:
        """Return parsed JSON dict from LLM response."""

    @abstractmethod
    async def batch_complete(
        self,
        requests: list[dict],   # [{"system": str, "user": str, "temperature": float}]
        max_concurrency: int = 5,
    ) -> list[dict]:
        """Run multiple completions concurrently."""

    @abstractmethod
    async def health_check(self) -> bool: ...
