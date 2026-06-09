"""Base class for all scoring categories."""
from app.core.interfaces import IScorer
from app.core.utils import clamp  # re-export so scorers only need one import


class BaseScorer(IScorer):
    """Concrete base — subclasses set `category` / `max_score` and implement `score()`."""

    def _clamp(self, value: int) -> int:
        return clamp(value, 0, self.max_score)
