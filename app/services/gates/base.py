"""Base class for all hard gates."""
from app.core.interfaces import IGate


class BaseGate(IGate):
    """Concrete base — subclasses only need to implement `check()`."""
