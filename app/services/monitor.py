import logging
from datetime import time as dtime
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Tracks alerts already fired this session so we don't spam the same alert.
# Keyed by (journal_id, level) where level is "sl", "t1", "t2", "t3".
_alerted: set = set()


def _market_hours() -> bool:
    now = datetime.now(IST).time()
    return dtime(10, 0) <= now <= dtime(14, 45)


def _get_option_price(nse_data, instrument: str) -> float | None:
    """Fetch current last-traded price for an option like 'NIFTY 24000 CE'."""
    parts = instrument.split()
    if len(parts) < 3:
        return None
    symbol, opt_type = parts[0], parts[2]
    try:
        strike = float(parts[1])
    except ValueError:
        return None
    try:
        oc = nse_data.get_option_chain(symbol)
        if not oc:
            return None
        records  = oc.get("records", {})
        expiries = records.get("expiryDates", [])
        if not expiries:
            return None
        nearest = expiries[0]
        for row in records.get("data", []):
            if row.get("expiryDate") == nearest and row.get("strikePrice") == strike:
                opt   = row.get(opt_type) or {}
                price = float(opt.get("lastPrice") or 0)
                return price if price > 0 else None
    except Exception as exc:
        logger.debug("Option price fetch failed [%s]: %s", instrument, exc)
    return None


def check_positions(nse_data, send_fn) -> None:
    """Check all open journal positions and fire Telegram alerts on target/SL hits.

    Called by the scheduler every 2 minutes during market hours (10:00–14:45 IST).
    Each alert level fires at most once per journal entry per session to prevent
    duplicate messages when the price hovers around a level.
    T1/T2 alerts are informational — position stays open.
    T3/SL alerts auto-close the journal entry with outcome and P&L(R).
    """
    if not _market_hours():
        return

    from app.services.storage import get_journal_entries, update_journal_entry
    from app.services.telegram import stop_loss_alert, target_hit_alert

    entries   = get_journal_entries(limit=100)
    open_pos  = [
        e for e in entries
        if e.get("exit_price") is None and e.get("status") in ("live", "paper")
    ]
    if not open_pos:
        return

    for pos in open_pos:
        instrument = pos.get("instrument", "")
        pos_id     = pos["id"]
        price      = _get_option_price(nse_data, instrument)
        if price is None:
            continue

        entry = float(pos.get("entry")    or 0)
        sl    = float(pos.get("stop_loss") or 0)
        t1    = float(pos.get("target_1")  or 0)
        t2    = float(pos.get("target_2")  or 0)
        t3    = float(pos.get("target_3")  or 0)
        risk  = abs(entry - sl) if entry != sl else 1.0

        def _pnl_r(exit_px: float) -> float:
            return round((exit_px - entry) / risk, 2)

        if sl > 0 and price <= sl and (pos_id, "sl") not in _alerted:
            _alerted.add((pos_id, "sl"))
            update_journal_entry(pos_id, {
                "exit_price": price,
                "outcome":    "loss",
                "pnl_r":      max(-3.0, _pnl_r(price)),
                "status":     "closed",
            })
            send_fn(stop_loss_alert(instrument, price))
            logger.info("SL alert: %s @ %.1f", instrument, price)

        elif t3 > 0 and price >= t3 and (pos_id, "t3") not in _alerted:
            _alerted.add((pos_id, "t3"))
            update_journal_entry(pos_id, {
                "exit_price": price,
                "outcome":    "win",
                "pnl_r":      _pnl_r(price),
                "status":     "closed",
            })
            send_fn(target_hit_alert(instrument, 3, price))
            logger.info("T3 alert: %s @ %.1f", instrument, price)

        elif t2 > 0 and price >= t2 and (pos_id, "t2") not in _alerted:
            _alerted.add((pos_id, "t2"))
            send_fn(target_hit_alert(instrument, 2, price))
            logger.info("T2 alert: %s @ %.1f", instrument, price)

        elif t1 > 0 and price >= t1 and (pos_id, "t1") not in _alerted:
            _alerted.add((pos_id, "t1"))
            send_fn(target_hit_alert(instrument, 1, price))
            logger.info("T1 alert: %s @ %.1f", instrument, price)
