"""Base signal strategy — template method pattern for scan orchestration."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.interfaces import ISignalStrategy


class BaseSignalStrategy(ISignalStrategy):
    """Provides `run_scan()` as a template method.

    Subclasses implement `score_candidate`, `check_gates`, and
    `compute_position_size`. Override `_post_filter()` to apply
    strategy-specific post-processing (e.g. sector concentration).
    """

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

        evaluated = []
        for candidate in candidates:
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
                rejection_reasons.append(
                    "Position size would exceed configured account risk."
                )

            evaluated.append({
                "candidate":        candidate,
                "approved":         approved,
                "grade":            grade,
                "score":            score,
                "sizing":           sizing,
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
        rejected     = [item for item in evaluated if item["candidate"]["id"] not in approved_ids]

        return {
            "settings":         settings,
            "approved":         approved_list,
            "rejected":         rejected,
            "noTrade":          len(approved_list) == 0,
            "scoreThreshold":   min_score,
            "thresholdRelaxed": False,
            "generatedAt":      datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
        }

    def _post_filter(self, approved_list: list[dict]) -> list[dict]:
        """Override in subclasses to add strategy-specific post-processing."""
        return approved_list

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
