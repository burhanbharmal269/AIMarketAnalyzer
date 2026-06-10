"""RedisCache — implements ICacheProvider using aioredis.

Falls back gracefully when Redis is unavailable (NullCache behaviour).
"""
from __future__ import annotations
import json
import logging
from datetime import date, datetime
from typing import Any

from app.application.ports.cache import ICacheProvider
from app.domain.market.entities import OptionChainSnapshot

logger = logging.getLogger(__name__)


class RedisCache(ICacheProvider):
    """
    Key schema:
      oc:{symbol}:{DDMMYYYY}           → OptionChainSnapshot (TTL 5 min)
      candles:{symbol}:{interval}:{YYYYMMDD} → list[Candle] (TTL 60s)
      ivrank:{symbol}                  → float               (TTL 1h)
      scan:latest                      → ScanResult           (TTL 15 min)
      regime:current                   → MarketRegime         (TTL 10 min)
      ltp:{symbol}                     → float               (TTL 10s)
      vix:india                        → float               (TTL 30s)
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._url    = redis_url
        self._client = None

    async def connect(self) -> None:
        try:
            import aioredis
            self._client = aioredis.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await self._client.ping()
            logger.info("Redis connected: %s", self._url)
        except Exception as exc:
            logger.warning("Redis unavailable (%s) — running without cache", exc)
            self._client = None

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None

    # ── Raw ops ───────────────────────────────────────────────────────────────

    async def get(self, key: str) -> str | None:
        if not self._client:
            return None
        try:
            return await self._client.get(key)
        except Exception as exc:
            logger.debug("Redis GET %s failed: %s", key, exc)
            return None

    async def set(self, key: str, value: str, ttl: int = 60) -> None:
        if not self._client:
            return
        try:
            await self._client.setex(key, ttl, value)
        except Exception as exc:
            logger.debug("Redis SET %s failed: %s", key, exc)

    async def delete(self, key: str) -> None:
        if not self._client:
            return
        try:
            await self._client.delete(key)
        except Exception as exc:
            logger.debug("Redis DEL %s failed: %s", key, exc)

    # ── Option chain ──────────────────────────────────────────────────────────

    async def get_option_chain(
        self, symbol: str, expiry: date
    ) -> OptionChainSnapshot | None:
        raw = await self.get(f"oc:{symbol}:{expiry.strftime('%d%m%Y')}")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return self._deserialize_oc(data)
        except Exception:
            return None

    async def set_option_chain(
        self, data: OptionChainSnapshot, ttl: int = 300
    ) -> None:
        key = f"oc:{data.symbol}:{data.expiry.date.strftime('%d%m%Y')}"
        await self.set(key, json.dumps(self._serialize_oc(data)), ttl)

    # ── Scan result ───────────────────────────────────────────────────────────

    async def get_scan_result(self) -> dict | None:
        raw = await self.get("scan:latest")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def set_scan_result(self, result: dict, ttl: int = 900) -> None:
        try:
            await self.set("scan:latest", json.dumps(result, default=str), ttl)
        except Exception as exc:
            logger.debug("Redis set_scan_result failed: %s", exc)

    # ── Market regime ─────────────────────────────────────────────────────────

    async def get_regime(self) -> dict | None:
        raw = await self.get("regime:current")
        return json.loads(raw) if raw else None

    async def set_regime(self, regime: dict, ttl: int = 600) -> None:
        await self.set("regime:current", json.dumps(regime, default=str), ttl)

    # ── IV rank ───────────────────────────────────────────────────────────────

    async def get_iv_rank(self, symbol: str) -> float | None:
        raw = await self.get(f"ivrank:{symbol}")
        return float(raw) if raw else None

    async def set_iv_rank(self, symbol: str, rank: float, ttl: int = 3600) -> None:
        await self.set(f"ivrank:{symbol}", str(rank), ttl)

    # ── VIX ───────────────────────────────────────────────────────────────────

    async def get_vix(self) -> float | None:
        raw = await self.get("vix:india")
        return float(raw) if raw else None

    async def set_vix(self, vix: float, ttl: int = 30) -> None:
        await self.set("vix:india", str(vix), ttl)

    # ── Health ────────────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        if not self._client:
            return False
        try:
            return await self._client.ping() == "PONG" or True
        except Exception:
            return False

    # ── Serialization helpers ─────────────────────────────────────────────────

    @staticmethod
    def _serialize_oc(oc: OptionChainSnapshot) -> dict:
        return {
            "symbol": oc.symbol,
            "expiry": oc.expiry.date.isoformat(),
            "spot_price": oc.spot_price,
            "source": oc.source,
            "fetched_at": oc.fetched_at.isoformat(),
            "strikes": [
                {
                    "strike": s.strike,
                    "option_type": s.option_type.value,
                    "ltp": s.ltp, "oi": s.oi,
                    "oi_change": s.oi_change, "volume": s.volume,
                    "iv": s.iv, "bid": s.bid, "ask": s.ask,
                    "delta": s.delta, "gamma": s.gamma,
                    "theta": s.theta, "vega": s.vega,
                }
                for s in oc.strikes
            ],
        }

    @staticmethod
    def _deserialize_oc(data: dict) -> OptionChainSnapshot:
        from app.domain.market.value_objects import OptionType, Expiry, Price, Volume, OI, IVPercent
        from app.domain.market.entities import StrikeData
        from datetime import date as _date
        import datetime as dt
        strikes = [
            StrikeData(
                strike=s["strike"],
                option_type=OptionType(s["option_type"]),
                ltp=Price(s["ltp"]),
                oi=OI(s["oi"]),
                oi_change=s["oi_change"],
                volume=Volume(int(s["volume"])),
                iv=IVPercent(s["iv"]),
                bid=Price(s["bid"]),
                ask=Price(s["ask"]),
                delta=s.get("delta", 0.0),
                gamma=s.get("gamma", 0.0),
                theta=s.get("theta", 0.0),
                vega=s.get("vega", 0.0),
            )
            for s in data.get("strikes", [])
        ]
        expiry_date = _date.fromisoformat(data["expiry"])
        return OptionChainSnapshot(
            symbol=data["symbol"],
            expiry=Expiry(expiry_date),
            spot_price=Price(data["spot_price"]),
            strikes=strikes,
            source=data.get("source", "cache"),
            fetched_at=dt.datetime.fromisoformat(data.get("fetched_at", dt.datetime.utcnow().isoformat())),
        )


class NullCache(ICacheProvider):
    """No-op cache — used when Redis is not configured. All reads return None."""

    async def get_option_chain(self, symbol, expiry): return None
    async def set_option_chain(self, data, ttl=300): pass
    async def get_scan_result(self): return None
    async def set_scan_result(self, result, ttl=900): pass
    async def get_regime(self): return None
    async def set_regime(self, regime, ttl=600): pass
    async def get_iv_rank(self, symbol): return None
    async def set_iv_rank(self, symbol, rank, ttl=3600): pass
    async def get_vix(self): return None
    async def set_vix(self, vix, ttl=30): pass
    async def get(self, key): return None
    async def set(self, key, value, ttl=60): pass
    async def delete(self, key): pass
    async def health_check(self): return False
