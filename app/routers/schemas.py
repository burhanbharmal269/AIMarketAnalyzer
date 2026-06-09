"""Pydantic request/response models for all API routers.

Centralised here so each router file stays focused on routing logic only.
"""
from typing import Optional
from pydantic import BaseModel, Field


class ScanSettings(BaseModel):
    accountCapital: float = Field(default=100_000, ge=10_000)
    riskPercent:    float = Field(default=2, ge=0.1, le=10)
    maxSpread:      float = Field(default=1.5, ge=0.5, le=10)
    minVolume:      int   = Field(default=25_000, ge=0)
    eventWindow:    int   = Field(default=60, ge=0)
    lossStreak:     int   = Field(default=0, ge=0)


class TelegramSendRequest(BaseModel):
    message: str


class JournalEntry(BaseModel):
    instrument:      str
    direction:       str
    entry:           float
    stopLoss:        float
    targets:         list[float] = Field(default=[0.0, 0.0, 0.0])
    confidenceScore: int         = Field(default=0)
    status:          str         = Field(default="paper")
    notes:           str         = Field(default="")


class JournalUpdate(BaseModel):
    exit_price: Optional[float] = None
    outcome:    Optional[str]   = None
    pnl_r:      Optional[float] = None
    status:     Optional[str]   = None
    notes:      Optional[str]   = None
