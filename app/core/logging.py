"""Structured JSON logging setup.

Call configure_logging() once at startup.
All loggers in the application inherit the root handler.
"""
from __future__ import annotations
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import Any


class _JSONFormatter(logging.Formatter):
    """Emits one JSON object per log line — compatible with Datadog/Loki/CloudWatch."""

    LEVEL_MAP = {
        logging.DEBUG:    "DEBUG",
        logging.INFO:     "INFO",
        logging.WARNING:  "WARNING",
        logging.ERROR:    "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts":      datetime.now(timezone.utc).isoformat(),
            "level":   self.LEVEL_MAP.get(record.levelno, "UNKNOWN"),
            "logger":  record.name,
            "msg":     record.getMessage(),
        }

        # Correlation context (injected by middleware)
        for key in ("correlation_id", "scan_id", "symbol", "provider"):
            val = record.__dict__.get(key)
            if val is not None:
                doc[key] = val

        if record.exc_info:
            doc["exception"] = "".join(traceback.format_exception(*record.exc_info))

        if record.stack_info:
            doc["stack"] = record.stack_info

        return json.dumps(doc, ensure_ascii=False)


class _ConsoleFormatter(logging.Formatter):
    """Human-readable format for development — colour-coded levels."""

    RESET  = "\033[0m"
    GREY   = "\033[90m"
    YELLOW = "\033[33m"
    RED    = "\033[31m"
    BOLD   = "\033[1m"
    GREEN  = "\033[32m"

    LEVEL_COLOURS = {
        "DEBUG":    "\033[90m",
        "INFO":     "\033[32m",
        "WARNING":  "\033[33m",
        "ERROR":    "\033[31m",
        "CRITICAL": "\033[1;31m",
    }

    def format(self, record: logging.LogRecord) -> str:
        colour   = self.LEVEL_COLOURS.get(record.levelname, "")
        ts       = datetime.now(timezone.utc).strftime("%H:%M:%S")
        prefix   = f"{self.GREY}{ts}{self.RESET} {colour}{record.levelname:<8}{self.RESET}"
        name     = f"{self.GREY}{record.name}{self.RESET}"
        msg      = record.getMessage()
        line     = f"{prefix} {name} {msg}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(
    level: str   = "INFO",
    json_logs: bool = False,
) -> None:
    """Initialise root logger. Call once at app startup."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers (avoid duplicate lines in reload scenarios)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter() if json_logs else _ConsoleFormatter())
    root.addHandler(handler)

    # Silence noisy third-party loggers
    for noisy in ("urllib3", "httpcore", "httpx", "hpack", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging configured: level=%s, json=%s", level, json_logs
    )


def get_logger(name: str) -> logging.Logger:
    """Thin wrapper — preserves the option to add context injection later."""
    return logging.getLogger(name)
