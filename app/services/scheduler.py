import logging
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
except Exception:
    BackgroundScheduler = None
    IntervalTrigger     = None

from app.config import settings

logger = logging.getLogger(__name__)


def _make_market_open_job(scan_fn, send_fn):
    def job():
        try:
            logger.info("Scheduler: running market open scan")
            from app.services.telegram import market_open_alert
            result = scan_fn()
            msg    = market_open_alert(result["market"], result)
            send_fn(msg)
            logger.info("Scheduler: market open alert sent")
        except Exception as exc:
            logger.error("Market open job failed: %s", exc)
    return job


def _make_eod_job(scan_fn, send_fn):
    def job():
        try:
            logger.info("Scheduler: running EOD scan")
            from app.services.telegram import eod_alert
            result = scan_fn()
            msg    = eod_alert(result["market"], result)
            send_fn(msg)
            logger.info("Scheduler: EOD alert sent")
        except Exception as exc:
            logger.error("EOD job failed: %s", exc)
    return job


def _make_ohlcv_refresh_job():
    def job():
        try:
            from app.services.storage import invalidate_ohlcv_today
            n = invalidate_ohlcv_today()
            logger.info("EOD ohlcv cache invalidated: %d rows cleared", n)
        except Exception as exc:
            logger.error("OHLCV refresh job failed: %s", exc)
    return job


def _make_monitor_job(nse_data, send_fn):
    def job():
        try:
            from app.services.monitor import check_positions
            check_positions(nse_data, send_fn)
        except Exception as exc:
            logger.error("Monitor job failed: %s", exc)
    return job


def _make_probe_job():
    def job():
        try:
            from app.services.api_probe import probe_all
            probe_all()
        except Exception as exc:
            logger.error("API probe job failed: %s", exc)
    return job


def create_scheduler(scan_fn, send_fn=None, nse_data=None):
    if not settings.enable_scheduler or BackgroundScheduler is None:
        return None

    _send = send_fn or (lambda msg: logger.info("Scheduler alert (no Telegram): %s", msg[:80]))

    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    # 10:05 — first clean scan after opening volatility gate clears (gate = 9:15–10:00)
    scheduler.add_job(
        _make_market_open_job(scan_fn, _send),
        "cron", day_of_week="mon-fri", hour=10, minute=5,
        id="morning_scan",
    )
    # 11:15 — peak liquidity window; trends confirmed, highest option volumes
    scheduler.add_job(
        _make_market_open_job(scan_fn, _send),
        "cron", day_of_week="mon-fri", hour=11, minute=15,
        id="midmorning_scan",
    )
    # 13:05 — post-lunch re-entry check; new setups that develop after 12:30 drift
    scheduler.add_job(
        _make_market_open_job(scan_fn, _send),
        "cron", day_of_week="mon-fri", hour=13, minute=5,
        id="afternoon_scan",
    )
    # 14:15 — last entry window before closing gate (14:45); 30-min buffer to close
    scheduler.add_job(
        _make_market_open_job(scan_fn, _send),
        "cron", day_of_week="mon-fri", hour=14, minute=15,
        id="final_scan",
    )
    # 15:20 — EOD summary after market close
    scheduler.add_job(
        _make_eod_job(scan_fn, _send),
        "cron", day_of_week="mon-fri", hour=15, minute=20,
        id="eod_scan",
    )
    # 16:05 — invalidate today's daily_ohlcv so next-day first scan fetches complete EOD candle
    scheduler.add_job(
        _make_ohlcv_refresh_job(),
        "cron", day_of_week="mon-fri", hour=16, minute=5,
        id="ohlcv_refresh",
    )
    # Price monitor — every 2 min during market hours (gate check is inside the job)
    if nse_data is not None and IntervalTrigger is not None:
        scheduler.add_job(
            _make_monitor_job(nse_data, _send),
            IntervalTrigger(minutes=2, timezone=IST),
            id="price_monitor",
        )
        logger.info("Price monitor scheduled (every 2 min, market hours only)")

    # API integration probe — every 5 min, always on
    if IntervalTrigger is not None:
        scheduler.add_job(
            _make_probe_job(),
            IntervalTrigger(minutes=5, timezone=IST),
            id="api_probe",
        )
        logger.info("API integration probe scheduled (every 5 min)")

    return scheduler
