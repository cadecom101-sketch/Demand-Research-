"""Decision engine: deterministic BUILD/TEST/REVISE/PARK/KILL.

The verdict is produced in two deterministic stages:

1. A weighted evidence-quality score (the formula is preserved from v1 and now
   exposed component-by-component for the scorecard).
2. Hard decision gates that can only *lower* the verdict — they cannot be
   overridden by model prose. A high score with no buyer-language artifacts, or
   a partial run, is capped before it can become BUILD.

`decide()` returns a `DecisionOutcome` carrying everything the audit layer needs
to explain the verdict.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from demand_research.audit.models import GateResult, ScoreComponent
from demand_research.models import Decision, PhaseResult, PhaseStatus, ProductHypothesis, SourceCard

FORMULA_VERSION = "v1"

# Verdict ordering: a gate can only push the decision *down* this ladder.
_RANK = {Decision.KILL: 0, Decision.PARK: 1, Decision.REVISE: 2, Decision.TEST: 3, Decision.BUILD: 4}
_BY_RANK = {v: k for k, v in _RANK.items()}

DECISION_THRESHOLDS = {
    "BUILD": "score >= 0.75 AND all hard gates pass",
    "TEST": "score 0.60-0.75 with gates clear (cheap external test warranted)",
    "REVISE": "score 0.50-0.60, or buyer/mechanism/grade gate caps a higher score",
    "PARK": "score 0.30-0.50, Phase 2 fails, or a partial/tool-failure run",
    "KILL": "Phase 1 fails / no category signal at all",
}


@dataclass
class DecisionOutcome:
    decision: Decision
    reasoning: str
    score: float
    components: List[ScoreComponent] = field(default_factory=list)
    gates: List[GateResult] = field(default_factory=list)
    fatal_gaps: List[str] = field(default_factory=list)
    thresholds: dict = field(default_factory=lambda: dict(DECISION_THRESHOLDS))
    overrides: List[str] = field(default_factory=list)


class DecisionEngine:
    """Determines the final decision and exposes the full scoring + gate trail."""

    def __init__(self, min_sources_per_phase: int = 3):
        self.min_sources_per_phase = min_sources_per_phase

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def decide(
        self,
        hypothesis: ProductHypothesis,
        phase_results: List[PhaseResult],
        all_sources: List[SourceCard],
        signals: Optional[dict] = None,
    ) -> DecisionOutcome:
        signals = signals or {}
        components, total = self.score_breakdown(phase_results, all_sources)

        failed_phase = next((p for p in phase_results if p.status == PhaseStatus.FAIL), None)
        base_decision, base_reason = self._base_decision(failed_phase, phase_results, total)

        gates, cap_rank, gate_fatals, overrides = self.evaluate_gates(signals)

        base_rank = _RANK[base_decision]
        final_rank = min(base_rank, cap_rank)
        final = _BY_RANK[final_rank]

        reason = base_reason
        if final_rank < base_rank:
            reason += (
                f" Hard gates capped the verdict from {base_decision.value} to {final.value}: "
                + "; ".join(overrides)
            )

        fatal_gaps = list(gate_fatals)
        return DecisionOutcome(
            decision=final,
            reasoning=reason,
            score=round(total, 4),
            components=components,
            gates=gates,
            fatal_gaps=fatal_gaps,
            thresholds=dict(DECISION_THRESHOLDS),
            overrides=overrides,
        )

    # ------------------------------------------------------------------ #
    # Base (score-driven) decision
    # ------------------------------------------------------------------ #
    def _base_decision(
        self,
        failed_phase: Optional[PhaseResult],
        phase_results: List[PhaseResult],
        total: float,
    ) -> tuple[Decision, str]:
        if failed_phase is not None:
            n = failed_phase.phase_number
            if n == 1:
                return Decision.KILL, (
                    "Phase 1 failed: no real market signals found — no evidence of buyer "
                    "activity or category existence."
                )
            if n == 2:
                return Decision.PARK, (
                    "Phase 2 failed: no verbatim buyer-language artifacts. The market may "
                    "exist but articulated pain is unproven. Revisit with broader searches."
                )
            if n == 3:
                return Decision.REVISE, (
                    "Phase 3 failed: could not map a competitor price band. Revise the "
                    "product definition or channel."
                )
            if n == 4:
                return Decision.REVISE, (
                    "Phase 4 failed: could not analyse 3+ real competitors. Narrow the scope."
                )
            if n == 5:
                return Decision.REVISE, (
                    "Phase 5 failed: no structural missing mechanism. The product may be a "
                    "feature add-on or re-skin."
                )

        # All provided phases passed — map score to a tier.
        if total >= 0.75:
            return Decision.BUILD, (
                f"All phases passed with strong evidence (quality {total:.2f})."
            )
        if total >= 0.60:
            return Decision.TEST, (
                f"All phases passed with moderate-strong evidence (quality {total:.2f}); "
                "warrants a cheap external/behavioral test before full build."
            )
        if total >= 0.50:
            return Decision.REVISE, (
                f"All phases passed but evidence is moderate (quality {total:.2f}); "
                "tighten buyer, channel, or mechanism before testing."
            )
        return Decision.PARK, (
            f"Phases passed but evidence is weak (quality {total:.2f}); keep researching."
        )

    # ------------------------------------------------------------------ #
    # Hard gates (deterministic; can only lower the verdict)
    # ------------------------------------------------------------------ #
    def evaluate_gates(self, signals: dict) -> tuple[List[GateResult], int, List[str], List[str]]:
        m = self.min_sources_per_phase
        cat = int(signals.get("category_signal_count", 0))
        blang = int(signals.get("buyer_language_artifact_count", 0))
        beh = int(signals.get("behavioral_intent_count", 0))
        grade_counts = signals.get("grade_counts", {}) or {}
        run_status = signals.get("run_status", "success")

        gates: List[GateResult] = []
        caps: List[int] = [_RANK[Decision.BUILD]]
        fatals: List[str] = []
        overrides: List[str] = []

        # Gate 1 — category signal minimum (KILL/PARK floor).
        if cat >= m:
            gates.append(GateResult(gate="category_signal_minimum", status="pass",
                                    reason=f"{cat} category signals (need {m})."))
        else:
            cap = _RANK[Decision.KILL] if cat == 0 else _RANK[Decision.PARK]
            caps.append(cap)
            reason = f"only {cat} category signals (need {m}); capped at {_BY_RANK[cap].value}."
            gates.append(GateResult(gate="category_signal_minimum", status="fail", reason=reason))
            fatals.append(f"Category-signal gate failed: {cat} of {m} required.")
            overrides.append(f"category_signal_minimum -> {_BY_RANK[cap].value}")

        # Gate 2 — buyer-language required for BUILD/TEST.
        if blang >= m:
            gates.append(GateResult(gate="buyer_language_for_build", status="pass",
                                    reason=f"{blang} verbatim buyer-language artifacts (need {m})."))
        else:
            caps.append(_RANK[Decision.REVISE])
            reason = (f"only {blang} verbatim buyer-language artifacts (need {m}); "
                      f"cannot exceed REVISE.")
            gates.append(GateResult(gate="buyer_language_for_build", status="fail", reason=reason))
            overrides.append("buyer_language_for_build -> REVISE")

        # Gate 3 — at least some direct pain or behavioral intent for a build.
        if blang > 0 or beh > 0:
            gates.append(GateResult(gate="any_pain_or_behavioral", status="pass",
                                    reason=f"{blang} buyer quotes / {beh} behavioral signals."))
        else:
            caps.append(_RANK[Decision.REVISE])
            gates.append(GateResult(gate="any_pain_or_behavioral", status="fail",
                                    reason="no direct buyer pain and no behavioral intent; cannot BUILD."))
            overrides.append("any_pain_or_behavioral -> REVISE")

        # Gate 4 — Grade-C ceiling (mostly weak evidence).
        a = int(grade_counts.get("A", 0))
        b = int(grade_counts.get("B", 0))
        if a == 0 and b < m:
            caps.append(_RANK[Decision.REVISE])
            gates.append(GateResult(gate="grade_c_ceiling", status="fail",
                                    reason=(f"evidence is mostly Grade C/D (A={a}, B={b}); "
                                            "cannot exceed REVISE without a behavioral test.")))
            overrides.append("grade_c_ceiling -> REVISE")
        else:
            gates.append(GateResult(gate="grade_c_ceiling", status="pass",
                                    reason=f"sufficient strong evidence (A={a}, B={b})."))

        # Gate 5 — tool / search failure (partial run).
        if run_status == "success":
            gates.append(GateResult(gate="tool_failure", status="pass",
                                    reason="run completed without tool/search failure."))
        else:
            caps.append(_RANK[Decision.PARK])
            gates.append(GateResult(gate="tool_failure", status="fail",
                                    reason=f"run status '{run_status}'; evidence collection incomplete, cannot BUILD."))
            fatals.append(f"Run status '{run_status}': evidence collection incomplete.")
            overrides.append("tool_failure -> PARK")

        return gates, min(caps), fatals, overrides

    # ------------------------------------------------------------------ #
    # Scoring (formula v1, now component-visible)
    # ------------------------------------------------------------------ #
    def score_breakdown(
        self, phase_results: List[PhaseResult], all_sources: List[SourceCard]
    ) -> tuple[List[ScoreComponent], float]:
        components: List[ScoreComponent] = []

        # 1. Phase pass ratio (weight 0.40).
        passed = sum(1 for p in phase_results if p.status == PhaseStatus.PASS)
        total_phases = 5
        pass_raw = passed / total_phases
        components.append(ScoreComponent(
            name="phase_pass_ratio", weight=0.40, raw_score=round(pass_raw, 4),
            weighted_score=round(pass_raw * 0.40, 4),
            rationale=f"{passed}/{total_phases} phases passed.",
        ))

        # 2. Source count (weight 0.30).
        min_sources, max_sources = 3, 15
        actual = len(all_sources)
        if actual >= min_sources:
            src_raw = min(1.0, (actual - min_sources) / (max_sources - min_sources))
        else:
            src_raw = 0.0
        components.append(ScoreComponent(
            name="source_count", weight=0.30, raw_score=round(src_raw, 4),
            weighted_score=round(src_raw * 0.30, 4),
            rationale=f"{actual} validated sources (floor {min_sources}, ceiling {max_sources}).",
        ))

        # 3. Recency (weight 0.20).
        cutoff = datetime.utcnow() - timedelta(days=90)
        recent = sum(1 for s in all_sources if s.date_observed >= cutoff)
        rec_raw = (recent / actual) if actual else 0.0
        components.append(ScoreComponent(
            name="recency", weight=0.20, raw_score=round(rec_raw, 4),
            weighted_score=round(rec_raw * 0.20, 4),
            rationale=f"{recent}/{actual} sources within 90 days." if actual else "no sources.",
        ))

        # 4. Direct-quote ratio (weight 0.10).
        direct = sum(1 for s in all_sources if s.is_direct_quote is True)
        q_raw = (direct / actual) if actual else 0.0
        components.append(ScoreComponent(
            name="direct_quote_ratio", weight=0.10, raw_score=round(q_raw, 4),
            weighted_score=round(min(q_raw, 1.0) * 0.10, 4),
            rationale=f"{direct}/{actual} sources are verbatim direct quotes." if actual else "no sources.",
        ))

        total = min(1.0, sum(c.weighted_score for c in components))
        return components, round(total, 4)
