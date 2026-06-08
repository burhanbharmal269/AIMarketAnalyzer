"""Background price monitor — checks open journal positions every 2 minutes
and sends Telegram alerts when T1, T2, or SL is hit.

Design:
  - Runs as a daemon thread so it dies cleanly when the server stops.
  - Only active during market hours (9:15-15:30 IST).
  - Alerts are de-duplicated per (entry_id, level) for the lifetime of the process.
  - SL hits auto-close the journal entry; T1/T2 hits alert only.
  - Telegram errors are swallowed — the alert is always written to the log.
"""

import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST             = ZoneInfo("Asia/Kolkata")
_INTERVAL_SECS    = 60    # 1-minute polling
_WATCHDOG_SECS    = 900   # NSE session health check every 15 min
_alerted: set     = set() # (entry_id, "t1"|"t2"|"sl") already sent this session
_monitor_started  = False
_watchdog_started = False


def _market_hours() -> bool:
    now = datetime.now(IST)
    return (9, 15) <= (now.hour, now.minute) <= (15, 30)


def _get_option_price(nse_data, instrument: str) -> float | None:
    """Fetch current LTP for an option from its instrument string 'SYMBOL STRIKE TYPE'.

    Priority:
      1. Angel One ltpData/optionGreek — fast, no scraping (when credentials configured)
      2. NSE option chain full fetch — fallback (slow, subject to Akamai blocking)
    """
    parts = instrument.strip().split()
    if len(parts) != 3:
        return None
    underlying, strike_str, opt_type = parts
    if opt_type not in ("CE", "PE"):
        return None
    try:
        strike = float(strike_str)
    except ValueError:
        return None

    # ── Primary: Angel One (fast single-strike lookup) ─────────────────────────
    try:
        from app.data_sources.angel import get_option_ltp, ANGEL_AVAILABLE
        if ANGEL_AVAILABLE:
            ltp = get_option_ltp(underlying, strike, opt_type)
            if ltp is not None and ltp > 0:
                return ltp
    except Exception as exc:
        logger.debug("Angel option LTP failed for %s: %s", instrument, exc)

    # ── Fallback: full NSE option chain (slow) ─────────────────────────────────
    oc = nse_data.get_option_chain(underlying)
    if not oc:
        return None

    records  = oc.get("records", {})
    expiries = records.get("expiryDates", [])
    if not expiries:
        return None

    nearest = expiries[0]
    rows = [r for r in records.get("data", []) if r.get("expiryDate") == nearest]
    for row in rows:
        if abs(float(row.get("strikePrice", 0)) - strike) < 0.5:
            opt = row.get(opt_type, {}) or {}
            ltp = opt.get("lastPrice")
            if ltp:
                return float(ltp)
    return None


def _send_alert(send_fn, label: str, instrument: str,
                entry: float, current: float, level: float,
                pnl_r: float | None) -> None:
    lines = [
        f"[{label}] {instrument}",
        f"Entry: {entry} | Now: {current} | Level: {level}",
    ]
    if pnl_r is not None:
        lines.append(f"P&L: {'+' if pnl_r >= 0 else ''}{pnl_r:.2f}R")
    message = "\n".join(lines)
    logger.info("Price alert: %s", message)
    try:
        send_fn(message)
    except Exception as exc:
        logger.debug("Telegram send skipped (not configured?): %s", exc)


def _resolve_signal_id(journal_entry: dict) -> int | None:
    """Return signal_log.id linked to a journal entry, if any."""
    try:
        from app.services.storage import get_recent_signals
        sig_id = journal_entry.get("signal_id")
        if sig_id:
            return int(sig_id)
        # Fallback: find the most recent open signal for this instrument
        instrument = journal_entry.get("instrument", "")
        signals = get_recent_signals(limit=100)
        for s in signals:
            if s["instrument"] == instrument and s["outcome"] is None:
                return s["id"]
    except Exception:
        pass
    return None


def _check_positions(nse_data, send_fn) -> None:
    from app.services.storage import get_journal_entries, update_journal_entry, update_signal_outcome

    entries = get_journal_entries(limit=100)
    active  = [e for e in entries if e.get("status") in ("open", "paper")]

    for entry in active:
        eid        = entry["id"]
        instrument = entry.get("instrument", "")
        if not instrument:
            continue

        current = _get_option_price(nse_data, instrument)
        if current is None:
            continue

        entry_px = float(entry.get("entry")     or 0)
        sl_px    = float(entry.get("stop_loss") or 0)
        t1_px    = float(entry.get("target_1")  or 0)
        t2_px    = float(entry.get("target_2")  or 0)
        t3_px    = float(entry.get("target_3")  or 0)
        risk     = abs(entry_px - sl_px) if sl_px else 0

        def _pnl(price):
            return round((price - entry_px) / risk, 2) if risk > 0 else 0.0

        sig_id = _resolve_signal_id(entry)

        # SL hit — auto-close as loss
        if sl_px > 0 and current <= sl_px and (eid, "sl") not in _alerted:
            _alerted.add((eid, "sl"))
            pnl_r = _pnl(current)
            final_pnl = max(pnl_r, -3.0)
            update_journal_entry(eid, {
                "status":     "closed",
                "outcome":    "loss",
                "exit_price": current,
                "pnl_r":      final_pnl,
            })
            if sig_id:
                try:
                    update_signal_outcome(sig_id, "loss", current, final_pnl, "sl_hit")
                except Exception as exc:
                    logger.debug("signal_log outcome update failed: %s", exc)
            _send_alert(send_fn, "SL HIT — AUTO CLOSED", instrument, entry_px, current, sl_px, pnl_r)

        # T3 hit — auto-close as full win (check before T2 so we don't double-fire)
        elif t3_px > 0 and current >= t3_px and (eid, "t3") not in _alerted:
            for lvl in ("t1", "t2", "t3"):
                _alerted.add((eid, lvl))
            pnl_r = _pnl(current)
            final_pnl = min(pnl_r, 5.0)
            update_journal_entry(eid, {
                "status":     "closed",
                "outcome":    "win",
                "exit_price": current,
                "pnl_r":      final_pnl,
            })
            if sig_id:
                try:
                    update_signal_outcome(sig_id, "win", current, final_pnl, "t3_hit")
                except Exception as exc:
                    logger.debug("signal_log outcome update failed: %s", exc)
            _send_alert(send_fn, "T3 HIT — AUTO CLOSED", instrument, entry_px, current, t3_px, pnl_r)

        # T2 hit — auto-close as win (conservative paper-trade exit)
        elif t2_px > 0 and current >= t2_px and (eid, "t2") not in _alerted:
            _alerted.add((eid, "t1"))
            _alerted.add((eid, "t2"))
            pnl_r = _pnl(current)
            final_pnl = min(pnl_r, 5.0)
            update_journal_entry(eid, {
                "status":     "closed",
                "outcome":    "win",
                "exit_price": current,
                "pnl_r":      final_pnl,
            })
            if sig_id:
                try:
                    update_signal_outcome(sig_id, "win", current, final_pnl, "t2_hit")
                except Exception as exc:
                    logger.debug("signal_log outcome update failed: %s", exc)
            _send_alert(send_fn, "T2 HIT — AUTO CLOSED", instrument, entry_px, current, t2_px, pnl_r)

        # T1 hit — alert only, let trade run toward T2/T3
        elif t1_px > 0 and current >= t1_px and (eid, "t1") not in _alerted:
            _alerted.add((eid, "t1"))
            _send_alert(send_fn, "T1 HIT — Trail stop to entry", instrument, entry_px, current, t1_px, _pnl(current))


def _session_healthy(nse_data) -> bool:
    """Lightweight NSE health probe — fetch VIX. Returns True if session is alive."""
    try:
        vix = nse_data.get_india_vix()
        return vix is not None and vix > 0
    except Exception:
        return False


def _run_watchdog(nse_data, send_fn) -> None:
    """Background thread: checks NSE session every 15 min during market hours.
    Forces a session reset and sends a Telegram alert if the session is dead.
    """
    global _watchdog_started
    consecutive_failures = 0
    logger.info("NSE session watchdog started (interval=%ds)", _WATCHDOG_SECS)
    while True:
        time.sleep(_WATCHDOG_SECS)
        if not _market_hours():
            continue
        if _session_healthy(nse_data):
            consecutive_failures = 0
            logger.debug("NSE session watchdog: OK")
        else:
            consecutive_failures += 1
            logger.warning("NSE session unhealthy (failure #%d) — forcing reconnect", consecutive_failures)
            # Force session reset so next request re-initialises
            try:
                nse_data._session = None
            except Exception:
                pass
            # Verify reconnect
            if _session_healthy(nse_data):
                logger.info("NSE session recovered after %d failure(s)", consecutive_failures)
                if consecutive_failures >= 2:
                    try:
                        send_fn("NSE SESSION RECOVERED\nReconnected after %d failed checks." % consecutive_failures)
                    except Exception:
                        pass
            else:
                logger.error("NSE session still dead after reset — alerts may not fire")
                try:
                    send_fn(
                        "NSE SESSION DEAD\n"
                        "Consecutive failures: %d\n"
                        "Price alerts are NOT firing. Restart the server if this persists." % consecutive_failures
                    )
                except Exception:
                    pass


def start_price_monitor(nse_data, send_fn) -> None:
    """Start the background position monitor and session watchdog. Safe to call multiple times."""
    global _monitor_started, _watchdog_started
    if not _monitor_started:
        _monitor_started = True

        def _loop():
            logger.info("Price monitor running (interval=%ds, market hours only)", _INTERVAL_SECS)
            while True:
                try:
                    if _market_hours():
                        _check_positions(nse_data, send_fn)
                except Exception as exc:
                    logger.warning("Monitor cycle error: %s", exc)
                time.sleep(_INTERVAL_SECS)

        threading.Thread(target=_loop, daemon=True, name="price-monitor").start()

    if not _watchdog_started:
        _watchdog_started = True
        threading.Thread(target=_run_watchdog, args=(nse_data, send_fn),
                         daemon=True, name="nse-watchdog").start()
