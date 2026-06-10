"""Pydantic v2 request/response schemas for the scan API."""
from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, model_validator


class ScanSettingsRequest(BaseModel):
    """POST /scan request body."""
    accountCapital:        float = Field(100_000, ge=50_000, description="Trading capital in INR")
    riskPercent:           float = Field(2.0, ge=0.5, le=5.0)
    maxSpread:             float = Field(1.5, ge=0.0, le=5.0)
    minVolume:             int   = Field(25_000, ge=1_000)
    eventWindow:           int   = Field(60, ge=0, le=720, description="Minutes to block before event")
    lossStreak:            int   = Field(0, ge=0)
    maxDailyLossPct:       float = Field(3.0, ge=1.0, le=10.0)
    maxWeeklyDrawdownPct:  float = Field(8.0, ge=2.0, le=25.0)
    maxMonthlyDrawdownPct: float = Field(15.0, ge=5.0, le=40.0)
    minScore:              float = Field(70.0, ge=0.0, le=100.0)
    maxSignals:            int   = Field(5, ge=1, le=20)
    useAI:                 bool  = Field(False, description="Enable AI ensemble analysis")
    dataSource:            str   = Field("auto", pattern="^(auto|angel|nse)$")

    model_config = {"extra": "forbid"}


class ScoreBreakdownResponse(BaseModel):
    trend:       float = 0.0
    momentum:    float = 0.0
    volume:      float = 0.0
    optionChain: float = 0.0
    sentiment:   float = 0.0
    riskReward:  float = 0.0
    news:        float = 0.0
    total:       float = 0.0


class CandidateResponse(BaseModel):
    instrument:  str
    underlying:  str   = ""
    direction:   str
    spotPrice:   float = 0.0
    entry:       float = 0.0
    stopLoss:    float = 0.0
    targets:     list[float] = []
    lots:        int   = 0
    quantity:    int   = 0
    lotRisk:     float = 0.0
    score:       ScoreBreakdownResponse = Field(default_factory=ScoreBreakdownResponse)
    setupType:   str   = "Trend"
    expiryType:  str   = "Weekly"
    dte:         int   = 0
    atmIv:       float = 0.0
    ivRank:      Optional[float] = None
    pcr:         float = 0.0
    explanation: str   = ""
    aiScore:     Optional[float] = None
    aiGrade:     str   = ""
    aiRec:       str   = ""
    keyThesis:   str   = ""


class ScanResponse(BaseModel):
    """POST /scan response body."""
    scanId:            int   = 0
    approved:          list[CandidateResponse] = []
    rejected:          list[dict]              = []
    noTrade:           bool  = False
    noTradeReason:     str   = ""
    candidatesChecked: int   = 0
    durationMs:        int   = 0
    dataSource:        str   = "composite"
    timestamp:         datetime = Field(default_factory=datetime.utcnow)

    model_config = {"populate_by_name": True}


class HealthResponse(BaseModel):
    status:    str   = "ok"
    version:   str   = ""
    providers: dict[str, bool] = {}
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RiskStateResponse(BaseModel):
    dailyPnl:       float = 0.0
    dailyPnlPct:    float = 0.0
    weeklyPnlPct:   float = 0.0
    monthlyPnlPct:  float = 0.0
    lossStreak:     int   = 0
    openTrades:     int   = 0
    capitalAt:      float = 0.0


class SignalLogResponse(BaseModel):
    id:          int
    createdAt:   datetime
    instrument:  str
    direction:   str
    score:       float = 0.0
    outcome:     Optional[str] = None
    pnlR:        Optional[float] = None
    lots:        int   = 0


class JournalEntryResponse(BaseModel):
    id:          int
    createdAt:   datetime
    instrument:  str
    direction:   str
    entry:       float
    stopLoss:    float
    targets:     list[float] = []
    lots:        int   = 1
    status:      str
    exitPrice:   Optional[float] = None
    pnlR:        Optional[float] = None
    notes:       str   = ""
