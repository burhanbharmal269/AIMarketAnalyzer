import logging

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:
    BackgroundScheduler = None

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


def create_scheduler(scan_fn, send_fn=None):
    if not settings.enable_scheduler or BackgroundScheduler is None:
        return None

    _send = send_fn or (lambda msg: logger.info("Scheduler alert (no Telegram): %s", msg[:80]))

    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        _make_market_open_job(scan_fn, _send),
        "cron", day_of_week="mon-fri", hour=9, minute=20,
        id="market_open_scan",
    )
    scheduler.add_job(
        _make_eod_job(scan_fn, _send),
        "cron", day_of_week="mon-fri", hour=15, minute=20,
        id="eod_scan",
    )
    return scheduler
