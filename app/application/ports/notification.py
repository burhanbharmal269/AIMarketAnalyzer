"""INotificationProvider — secondary port for all outbound alerts."""
from __future__ import annotations
from abc import ABC, abstractmethod


class INotificationProvider(ABC):

    @abstractmethod
    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """Send a notification. Returns True on success."""

    @abstractmethod
    async def send_signal_alert(self, signal_dict: dict) -> bool:
        """Format and send a trading signal alert."""

    @abstractmethod
    async def health_check(self) -> bool: ...
