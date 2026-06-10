"""Shared runtime state visible to both main.py and routers.

Kept in a separate module to avoid circular imports between app.main and app.routers.
"""
from __future__ import annotations
from typing import Any

state: dict[str, Any] = {
    "ready":      False,   # True once background init completes
    "started_at": None,    # monotonic float, set at lifespan yield
    "checks": {
        "kite": None,      # True / False / None (pending)
        "nse":  None,
        "db":   None,
    },
}
