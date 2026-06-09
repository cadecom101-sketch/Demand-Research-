"""Next-evidence planning and decision diagnostics.

Two read-only, additive layers on top of a finished run:

1. `build_decision_diagnostics(...)` explains *why* the verdict was reached —
   per-phase pass/fail, what was accepted vs rejected, which gate each phase
   feeds, why generic evidence was not enough, and crucially whether the run
   fell short because evidence was MISSING or because extraction/search FAILED.

2. `build_next_evidence_plan(...)` + `render_next_evidence_markdown(...)` turn a
   PARK / REVISE verdict into a concrete next move: which failed gates matter
   most, which exact search families to run next (reusing the query planner),
   which source types would satisfy the missing gates, and what would and would
   NOT count as valid evidence.

Neither layer changes a gate, a threshold, or a verdict. They only explain and
guide. Diagnostics are never used to raise a verdict.
"""

from __future__ import annotations

from typing import List, Optional

from demand_research.models import DemandBrief, PhaseStatus, ProductHypothesis, ReviewVerdict
from demand_research.research.query_planner import build_query_families

# E1 gate -> the research phase(s) whose evidence feeds that gate. Used to pick
# the right query families for a failed gate, and to label which gate a phase
# supports in the diagnostics.
GATE_TO_PHASES = {
    "scope_lock": [1, 4],
    "minimum_real_observed_evidence": [1, 2, 3, 4],
    "demand_signal_exists": [1],
    "buyer_language_captured": [2],
    "observed_price_band": [3],
    "competitor_presence": [4],
    "specific_missing_mechanism_gap": [5],
    "fit_to_andrew_authored_primitive": [2, 4],
    "no_fabrication": [],
}

# Verdict-precedence ordering of gates (mirrors evaluate_e1_review). The earliest
# failing gate in this order is the primary blocker — the one to fix first.
_GATE_PRECEDENCE = [
    "no_fabrication",
    "scope_lock",
    "minimum_real_observed_evidence",
    "demand_signal_exists",
    "buyer_language_captured",
    "observed_price_band",
    "competitor_presence",
    "specific_missing_mechanism_gap",
    "fit_to_andrew_authored_primitive",
]

PHASE_NAMES = {
    1: "Signal Discovery", 2: "Buyer Language Mining", 3: "Price Band Mapping",
    4: "Competitor Presence", 5: "Missing-Mechanism Gap",
}

# What does NOT count as valid evidence for each gate — the conservative bar,
# restated so a reader cannot mistake weak evidence for a pass.
_WOULD_NOT_COUNT = {
    "demand_signal_exists": "Generic trend articles or AI-written 'top niches' lists do not show real demand.",
    "minimum_real_observed_evidence": "Fewer than 3 real observed sources, or any fabricated/placeholder URL.",
    "buyer_language_captured": (
        "Generic satisfaction/praise quotes ('love it', 'works great') do NOT prove pain. "
        "Paraphrases, composites, and listing copy do not count — only verbatim, attributable "
        "pain in the buyer's own words."
    ),
    "observed_price_band": (
        "Unpriced competitor leads and directional pricing articles do NOT satisfy the price "
        "band — only verified, observed competitor prices with URLs."
    ),
    "competitor_presence": "Merely asserting competitors exist; each needs an observed product/listing.",
    "specific_missing_mechanism_gap": (
        "Aesthetic/feature/label differences ('cleaner', 'more pages', 'cheaper') are not a "
        "structural gap; the gap must compare existing alternatives to the proposed mechanism."
    ),
    "scope_lock": "Member A/B operations, generic templates, or off-family evidence do not lock to Base.",
    "fit_to_andrew_authored_primitive": (
        "Evidence that only justifies a generic planner/dashboard — with no need for demand "
        "gates, listing readiness, launch tracking, or review cycles — does not fit the primitive."
    ),
    "no_fabrication": "Any fabricated source, invented quote, guessed price, or placeholder URL.",
}


def _failing_e1_gates(e1_artifact: dict) -> List[str]:
    return [g["gate_id"] for g in e1_artifact.get("gates", []) if g.get("status") == "FAIL"]


def _ordered_failing(gate_ids: List[str]) -> List[str]:
    ordered = [g for g in _GATE_PRECEDENCE if g in gate_ids]
    # Append any gate not in the precedence list (defensive) at the end.
    ordered += [g for g in gate_ids if g not in ordered]
    return ordered


# --------------------------------------------------------------------------- #
# Decision diagnostics
# --------------------------------------------------------------------------- #
def build_decision_diagnostics(
    *,
    brief: DemandBrief,
    e1_artifact: dict,
    signals: dict,
    phase_results: List,
    rejected_summary: dict,
    run_status: str,
    extraction_error_count: int = 0,
) -> dict:
    """Explain the verdict without changing it."""
    phases = []
    for p in phase_results:
        supports = [gid for gid, ph in GATE_TO_PHASES.items() if p.phase_number in ph]
        phases.append({
            "phase": p.phase_number,
            "name": p.phase_name,
            "status": p.status.value,
            "reason": p.reason,
            "accepted_source_count": len(p.sources_collected),
            "supports_e1_gates": supports,
        })

    # Missing-evidence vs tooling-failure: a partial/failed run with extraction
    # errors is a TOOLING problem, not proof of "no market". This distinction is
    # diagnostic only — it never raises a verdict.
    if extraction_error_count > 0 or run_status in ("partial", "failed"):
        failure_mode = "extraction_or_search_failure"
        failure_note = (
            f"Run status '{run_status}' with {extraction_error_count} extraction parse "
            "error(s): the shortfall is at least partly a tooling/extraction failure, not "
            "proof that demand is absent. Re-run before concluding the market is empty."
        )
    else:
        failure_mode = "evidence_missing"
        failure_note = (
            "Run completed cleanly; any shortfall reflects genuinely missing/again-thin "
            "evidence, not a tooling failure."
        )

    grade_counts = signals.get("grade_counts", {}) or {}
    generic_only = (grade_counts.get("A", 0) == 0 and grade_counts.get("B", 0) == 0
                    and grade_counts.get("C", 0) > 0)

    return {
        "verdict": brief.review_verdict,
        "internal_decision": brief.decision.value,
        "phases": phases,
        "accepted_evidence": {
            "grade_counts": dict(grade_counts),
            "buyer_language_artifacts": signals.get("buyer_language_artifact_count", 0),
            "verified_prices": signals.get("phase3_priced_count", 0),
            "competitors": signals.get("phase4_competitor_count", 0),
        },
        "rejected_evidence": {
            "total": rejected_summary.get("total", 0),
            "by_reason": rejected_summary.get("by_reason", {}),
        },
        "failing_e1_gates": _ordered_failing(_failing_e1_gates(e1_artifact)),
        "generic_evidence_only": generic_only,
        "why_generic_not_enough": (
            "Only Grade C category/competitor evidence is present (no Grade B buyer-language "
            "or Grade A behavioral). Category existence proves a market, not buyer pain or "
            "intent, so it cannot lift the verdict past PARK." if generic_only else None
        ),
        "failure_mode": failure_mode,
        "failure_mode_note": failure_note,
    }


# --------------------------------------------------------------------------- #
# Next-evidence plan
# --------------------------------------------------------------------------- #
def needs_next_evidence_plan(verdict: Optional[str]) -> bool:
    """A concrete next-evidence plan is produced for PARK and REVISE verdicts."""
    return verdict in (
        ReviewVerdict.E1_PARK.value,
        ReviewVerdict.E1_REVISE_BEFORE_RECORDING.value,
    )


def build_next_evidence_plan(
    *,
    hypothesis: ProductHypothesis,
    brief: DemandBrief,
    e1_artifact: dict,
    signals: dict,
) -> dict:
    """Produce a specific next-evidence plan for a PARK/REVISE verdict."""
    verdict = brief.review_verdict
    failing = _ordered_failing(_failing_e1_gates(e1_artifact))

    targeted = []
    seen_phases: set = set()
    for gate in failing:
        for phase in GATE_TO_PHASES.get(gate, []):
            if phase in seen_phases:
                continue
            seen_phases.add(phase)
            families = build_query_families(hypothesis, phase)
            targeted.append({
                "for_failed_gate": gate,
                "phase": phase,
                "phase_name": PHASE_NAMES.get(phase, f"Phase {phase}"),
                "search_families": [f.to_dict() for f in families],
                "would_satisfy_source_types": sorted({
                    t for f in families for t in f.satisfying_source_types
                }),
                "would_not_count": _WOULD_NOT_COUNT.get(gate, ""),
            })

    return {
        "applies": needs_next_evidence_plan(verdict),
        "verdict": verdict,
        "product_name": hypothesis.product_name,  # unchanged — no rename required
        "primary_blocker": failing[0] if failing else None,
        "failed_gates_in_priority_order": failing,
        "targeted_search_plan": targeted,
        "guardrails": [
            "Run more/better searches — do NOT lower any threshold.",
            "Only verbatim, attributable buyer pain counts as buyer language.",
            "Only verified observed prices count for the price band.",
            "No fabricated sources, invented quotes, guessed prices, or placeholder URLs.",
            "Recording into Revenue OS stays external/manual; B3 stays LOCKED.",
        ],
    }


def render_next_evidence_markdown(plan: dict) -> str:
    """Render the next-evidence plan to markdown."""
    if not plan.get("applies"):
        return ""
    L: List[str] = []
    L.append(f"# Next Evidence Plan — {plan.get('product_name', '')}")
    L.append("")
    L.append(f"**Verdict:** {plan.get('verdict', '')}")
    if plan.get("primary_blocker"):
        L.append(f"**Primary blocker (fix first):** `{plan['primary_blocker']}`")
    failed = plan.get("failed_gates_in_priority_order", [])
    if failed:
        L.append(f"**Failed gates, in priority order:** {', '.join(f'`{g}`' for g in failed)}")
    L.append("")
    for block in plan.get("targeted_search_plan", []):
        L.append(f"## Gate `{block['for_failed_gate']}` → {block['phase_name']} (Phase {block['phase']})")
        sts = block.get("would_satisfy_source_types", [])
        if sts:
            L.append(f"_Source types that would satisfy this gate:_ {', '.join(sts)}")
        wn = block.get("would_not_count", "")
        if wn:
            L.append(f"_What would NOT count:_ {wn}")
        for fam in block.get("search_families", []):
            L.append(f"- **{fam['name']}** — {fam['intent']}")
            for q in fam.get("queries", [])[:6]:
                L.append(f"    - `{q}`")
        L.append("")
    if plan.get("guardrails"):
        L.append("## Guardrails (unchanged governance)")
        for g in plan["guardrails"]:
            L.append(f"- {g}")
        L.append("")
    return "\n".join(L)
