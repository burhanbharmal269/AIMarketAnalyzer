"""Base signal strategy — template method pattern for scan orchestration."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.interfaces import ISignalStrategy

logger = logging.getLogger(__name__)

_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}


class BaseSignalStrategy(ISignalStrategy):
    """Provides `run_scan()` as a template method.

    Subclasses implement `score_candidate`, `check_gates`, and
    `compute_position_size`. Override `_post_filter()` to apply
    strategy-specific post-processing (e.g. sector concentration).
    """

    # ── Sequential funnel ────────────────────────────────────────────────────
    # Each stage is a lightweight check that eliminates obvious non-starters
    # before the expensive gate+score loop runs. This means scoring only
    # touches candidates that already passed structural checks.
    #
    # Stage thresholds are INTENTIONALLY more lenient than the full gates —
    # the funnel kills clear non-starters; the gates enforce exact rules.

    def _funnel_stage_volume(self, c: dict, settings: dict) -> str | None:
        """Stage 1: Volume Expansion — is there real participation in this contract?"""
        underlying = c.get("underlying", "")
        vol = c.get("optionVolume", 0)
        is_index = underlying in _INDEX_SYMBOLS
        if vol == 0:
            # Post-expiry fresh contracts have zero volume until ~10:30 but OI is live
            return None if c.get("oiChangePct", 0) > 0 else "No option volume and no OI activity"
        min_vol = settings.get("minVolume", 25_000) if is_index else 3_000
        if vol < min_vol:
            return f"Option volume {vol:,} below minimum {min_vol:,}"
        return None

    def _funnel_stage_oi(self, c: dict, settings: dict) -> str | None:
        """Stage 2: OI Build-up — participants opening, not closing."""
        oi_chg = c.get("oiChangePct", 0)
        if oi_chg < -10:
            return f"OI unwinding {oi_chg:.1f}% — participants exiting, not entering"
        return None

    def _funnel_stage_breakout(self, c: dict, settings: dict) -> str | None:
        """Stage 3: Price Breakout — EMA20 must be on correct side of EMA50.

        Funnel uses a loose 2-EMA check (EMA20 vs EMA50 only) so candidates
        reach the gate+score loop where TrendAlignmentGate enforces the full
        EMA20>50>200 stack. Requiring the full stack here silently dropped
        candidates in choppy markets before they could even be evaluated.
        """
        direction = c.get("direction", "")
        ema20 = c.get("ema20", 0)
        ema50 = c.get("ema50", 0)
        if ema20 == 0 or ema50 == 0:
            return None  # missing data — let gate handle it
        if direction == "BUY" and ema20 < ema50:
            return "EMA20 below EMA50 — price structure not bullish"
        if direction == "SELL" and ema20 > ema50:
            return "EMA20 above EMA50 — price structure not bearish"
        return None

    def _funnel_stage_option(self, c: dict, settings: dict) -> str | None:
        """Stage 4: Option Confirmation — contract must be liquid enough to execute."""
        max_spread = settings.get("maxSpread", 1.5)
        spread = c.get("spreadPct", 0)
        # Use 2× tolerance here — full SpreadGate enforces exact limit later
        if spread > max_spread * 2:
            return f"Spread {spread:.1f}% far exceeds limit {max_spread}% — illiquid"
        return None

    def _run_funnel(
        self, candidates: list[dict], settings: dict
    ) -> tuple[list[dict], list[dict], dict]:
        """Run the 4-stage elimination funnel.

        Returns (survivors, funnel_rejects, stage_log).
        funnel_rejects are pre-formatted as rejected items for the scan result.
        """
        stages = [
            ("Volume Expansion",    self._funnel_stage_volume),
            ("OI Build-up",         self._funnel_stage_oi),
            ("Price Breakout",      self._funnel_stage_breakout),
            ("Option Confirmation", self._funnel_stage_option),
        ]

        current = candidates
        funnel_rejects: list[dict] = []
        stage_log: dict[str, dict] = {}

        for stage_name, check_fn in stages:
            passed, failed = [], []
            for c in current:
                reason = check_fn(c, settings)
                if reason:
                    failed.append((c, reason))
                else:
                    passed.append(c)

            for c, reason in failed:
                funnel_rejects.append({
                    "candidate":        c,
                    "approved":         False,
                    "grade":            "—",
                    "score":            {"total": 0, "scores": {}},
                    "sizing":           {"lots": 0},
                    "setupType":        "—",
                    "exitPlan":         None,
                    "validUntil":       "—",
                    "explanation":      f"Eliminated at funnel stage '{stage_name}': {reason}",
                    "risks":            [],
                    "rejectionReasons": [f"[{stage_name}] {reason}"],
                    "funnelStage":      stage_name,
                })

            stage_log[stage_name] = {
                "in": len(current), "out": len(passed), "dropped": len(failed),
            }
            logger.info(
                "Funnel [%-22s]: %3d → %3d  (dropped %d)",
                stage_name, len(current), len(passed), len(failed),
            )
            current = passed

        return current, funnel_rejects, stage_log

    # ── Main scan loop ────────────────────────────────────────────────────────

    def run_scan(
        self,
        candidates: list[dict],
        market: dict,
        risk_state: dict,
        settings: dict,
    ) -> dict:
        from zoneinfo import ZoneInfo
        _IST = ZoneInfo("Asia/Kolkata")
        min_score = settings["minScore"]

        # ── Sequential funnel: eliminate non-starters before expensive scoring ─
        survivors, funnel_rejects, funnel_log = self._run_funnel(candidates, settings)
        logger.info(
            "Funnel complete: %d/%d candidates proceed to gate+score",
            len(survivors), len(candidates),
        )

        evaluated = []
        for candidate in survivors:
            failures = self.check_gates(candidate, market, risk_state, settings)
            score    = self.score_candidate(candidate, market)

            # ── Signal grade: drives position size adjustment ─────────────────
            # Grade A (≥ 80): high-conviction — multiple strong signals aligned.
            #   Full position size. Historically these correlate with higher WR.
            # Grade B (70-79): good setup but borderline — fewer signals align.
            #   65% of computed lots. Smaller bet on lower-conviction entries.
            # This implements a practical Kelly-type position scaling without
            # requiring historical WR data: more signals aligned = edge is clearer.
            grade = "A" if score["total"] >= 80 else "B"

            sizing = self.compute_position_size(candidate, settings, grade=grade)

            approved = (
                not failures
                and score["total"] >= min_score
                and sizing["lots"] >= 1
            )

            rejection_reasons = list(failures)
            if score["total"] < min_score:
                # Identify the weakest scoring category to give actionable feedback
                scores_dict = score.get("scores", {})
                if scores_dict:
                    weakest_cat = min(scores_dict, key=lambda k: scores_dict[k])
                    weakest_pts = scores_dict[weakest_cat]
                    rejection_reasons.append(
                        f"Score {score['total']}/100 below threshold {min_score}. "
                        f"Weakest: {weakest_cat} ({weakest_pts} pts) — "
                        f"improve trend/momentum alignment to qualify."
                    )
                else:
                    rejection_reasons.append(
                        f"Score {score['total']} is below threshold {min_score}."
                    )
            if sizing["lots"] < 1:
                flag         = sizing.get("capitalFlag", "")
                premium_pct  = sizing.get("premiumPct", 0)
                premium_1lot = sizing.get("premium1Lot", 0)
                lot_risk     = sizing.get("lotRisk", 0)
                capital      = settings["accountCapital"]
                risk_pct     = settings["riskPercent"]
                risk_budget  = round(capital * risk_pct / 100)

                if flag == "undercapitalized":
                    min_capital = int(premium_1lot / 0.05)   # need 1 lot ≤ 5% of capital
                    rejection_reasons.append(
                        f"Undercapitalized: 1 lot costs ₹{premium_1lot:,} = {premium_pct}% of your "
                        f"₹{capital:,} account (professional limit: 5%). "
                        f"Minimum capital for this instrument: ₹{min_capital:,}."
                    )
                elif flag == "premium_too_high_for_grade":
                    rejection_reasons.append(
                        f"Premium ₹{premium_1lot:,}/lot = {premium_pct}% of capital — too high for a "
                        f"Grade B signal (5–8% zone requires Grade A). Score needs ≥80 to qualify."
                    )
                elif lot_risk > 0:
                    capital_needed = int(lot_risk / (risk_pct / 100))
                    risk_pct_needed = round(lot_risk / capital * 100, 1)
                    rejection_reasons.append(
                        f"Risk per lot ₹{lot_risk:,} exceeds {risk_pct}% risk budget (₹{risk_budget:,}). "
                        f"To trade 1 lot: raise capital to ₹{capital_needed:,} OR set risk% to {risk_pct_needed}%."
                    )
                else:
                    rejection_reasons.append("Position size cannot be computed — check entry and stop-loss values.")

            evaluated.append({
                "candidate":        candidate,
                "approved":         approved,
                "grade":            grade,
                "score":            score,
                "sizing":           sizing,
                "setupType":        self._classify_setup(candidate),
                "exitPlan":         self._build_exit_plan(candidate) if approved else None,
                "validUntil":       self._signal_valid_until(candidate),
                "explanation":      self._build_explanation(candidate, sizing, approved),
                "risks":            self._build_risks(candidate, market),
                "rejectionReasons": rejection_reasons,
            })

        approved_list = sorted(
            [item for item in evaluated if item["approved"]],
            key=lambda item: item["score"]["total"],
            reverse=True,
        )[: settings["maxSignals"]]

        approved_list = self._post_filter(approved_list)

        approved_ids = {item["candidate"]["id"] for item in approved_list}
        gate_rejected = [item for item in evaluated if item["candidate"]["id"] not in approved_ids]
        rejected      = funnel_rejects + gate_rejected

        return {
            "settings":         settings,
            "approved":         approved_list,
            "rejected":         rejected,
            "noTrade":          len(approved_list) == 0,
            "scoreThreshold":   min_score,
            "thresholdRelaxed": False,
            "generatedAt":      datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
            "funnelLog":        funnel_log,
        }

    def _post_filter(self, approved_list: list[dict]) -> list[dict]:
        """Override in subclasses to add strategy-specific post-processing."""
        return approved_list

    @staticmethod
    def _classify_setup(candidate: dict) -> str:
        """Label the primary setup type driving this signal.

        Ordered by specificity — first matching label wins.
        Used in UI badges and Telegram output to mirror professional signal format.
        """
        from datetime import datetime, time as dtime
        from zoneinfo import ZoneInfo
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).time()
        in_orb_window = dtime(9, 30) <= now_ist <= dtime(10, 0)

        if candidate.get("orbBreakout") and in_orb_window:
            return "ORB"          # Opening Range Breakout — first 15-min candle confirmed
        if candidate.get("orbBreakout"):
            return "ORB Cont"     # ORB breakout but outside the prime window
        if candidate.get("srBreakout"):
            return "S/R Break"    # Multi-touch S/R level cleared with volume
        if candidate.get("pdBreakout"):
            return "PDH/L Break"  # Previous day high/low breakout
        adx = candidate.get("adx", 0)
        if adx >= 25 and candidate.get("vwapConfirmed") and candidate.get("tf15Aligned"):
            return "Momentum"     # Strong ADX + VWAP + 15m EMA — institutional momentum
        if candidate.get("vwapConfirmed") and candidate.get("tf15Aligned"):
            return "Trend"        # VWAP + intraday EMA alignment — trend continuation
        if candidate.get("gapUp") or candidate.get("gapDown"):
            return "Gap Play"     # Opening gap in trade direction
        return "Trend"            # Default — EMA-driven directional trade

    @staticmethod
    def _signal_valid_until(candidate: dict) -> str:
        from datetime import timedelta
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        valid_until = now + timedelta(minutes=candidate["signalValidMinutes"])
        return valid_until.strftime("%I:%M %p")

    @staticmethod
    def _build_explanation(candidate: dict, sizing: dict, approved: bool) -> str:
        if not approved:
            return (
                "Rejected before recommendation because deterministic risk gates or score "
                "requirements were not satisfied."
            )
        return (
            f"{candidate['instrument']} qualifies because trend, momentum, liquidity and "
            f"option-chain evidence align with the trade direction. The setup uses a "
            f"defined stop at {candidate['stopLoss']} and the account risk rule limits "
            f"size to {sizing['lots']} lot(s). The trade remains valid only while price "
            "action holds the entry structure and event risk does not change."
        )

    @staticmethod
    def _build_exit_plan(candidate: dict) -> dict:
        """Compute adaptive trailing-stop exit plan from entry/targets.

        Research-backed exit management: once partial profit is locked,
        move stop to protect it. Prevents winners turning into losers.

        Rules:
          After T1 hit → move stop to entry (breakeven, zero loss from here)
          After T2 hit → trail stop to T1 (lock minimum 1R profit)
          T3 = full target, close remaining position
        """
        entry   = candidate.get("entry", 0)
        sl      = candidate.get("stopLoss", 0)
        targets = candidate.get("targets", [])
        t1      = targets[0] if len(targets) > 0 else None
        t2      = targets[1] if len(targets) > 1 else None
        t3      = targets[2] if len(targets) > 2 else None

        risk    = abs(entry - sl) if sl else 0
        rr      = candidate.get("rr", 0)

        return {
            "entry":            entry,
            "initialStop":      sl,
            "afterT1Stop":      round(entry, 2),          # breakeven — zero loss guaranteed
            "afterT2Stop":      round(t1, 2) if t1 else None,   # lock T1 profit
            "t1":               t1,
            "t2":               t2,
            "t3":               t3,
            "riskPerUnit":      round(risk, 2),
            "rr":               rr,
            "plan": (
                f"Enter at {entry}. "
                f"Stop at {sl} (risk {round(risk,1)} pts). "
                + (f"At T1 ({t1}): move stop to entry ({entry}) — breakeven secured. " if t1 else "")
                + (f"At T2 ({t2}): trail stop to T1 ({t1}) — minimum profit locked. " if t2 and t1 else "")
                + (f"T3 ({t3}): close full position." if t3 else "")
            ),
        }

    @staticmethod
    def _build_risks(candidate: dict, market: dict) -> list[str]:
        risks = []
        if candidate["spreadPct"] > 2:
            risks.append("Spread can reduce realized reward.")
        if market["indiaVix"] > 16:
            risks.append("Volatility is above the calm-market zone.")
        if candidate["expiry"] == "Weekly":
            risks.append("Weekly options carry faster time decay after failed follow-through.")
        for event in market.get("eventCalendar", []):
            if event["severity"] == "high" and event["minutesAway"] <= 1440:
                risks.append(
                    f"{event['name']} can change market sentiment within the next trading day."
                )
        risks.extend(candidate.get("notes", []))
        return risks[:5]
