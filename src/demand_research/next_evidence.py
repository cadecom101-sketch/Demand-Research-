"""Next-evidence planning and decision diagnostics.

Two read-only, additive layers on top of a finished run:

1. `build_decision_diagnostics(...)` explains *why* the verdict was reached —
   per-phase pass/fail and observation state, what was accepted vs rejected,
   which gate each phase feeds, why generic evidence was not enough, and
   crucially whether the run fell short because evidence was genuinely MISSING
   (clean search, thin market) or because tooling FAILED (tool/search failure,
   extraction failure) — those are different epistemic states and are never
   collapsed into one.

2. `build_next_evidence_plan(...)` + `render_next_evidence_markdown(...)` turn a
   PARK / REVISE verdict into a concrete next move: which failed gates matter
   most (value-of-information ranking), which exact search families to run next
   (reusing the query planner), which source types would satisfy the missing
   gates, and what would and would NOT count as valid evidence.

Neither layer changes a gate, a threshold, or a verdict. They only explain and
guide. Diagnostics are never used to raise a verdict.
"""

from __future__ import annotations

from typing import List, Optional

from demand_research.models import DemandBrief, PhaseStatus, ProductHypothesis, ReviewVerdict
from demand_research.research.query_planner import build_query_families
from demand_research.tool_failure import classify_uncertainty

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
def _phase_observation_state(p, tool_failed: bool) -> str:
    """Classify one completed phase's observation state (diagnostic only)."""
    if tool_failed:
        return "partial_tool_failure"
    if p.status == PhaseStatus.PASS:
        return "accepted"
    if len(p.sources_collected) > 0:
        return "observed_below_bar"
    return "unsupported_after_clean_search"


def build_decision_diagnostics(
    *,
    brief: DemandBrief,
    e1_artifact: dict,
    signals: dict,
    phase_results: List,
    rejected_summary: dict,
    run_status: str,
    extraction_error_count: int = 0,
    extraction_salvage_count: int = 0,
    tool_failures: Optional[List[dict]] = None,
    belief_state: Optional[dict] = None,
) -> dict:
    """Explain the verdict without changing it.

    The failure-mode classification distinguishes three epistemically different
    situations (never collapsed):

      - evidence_incomplete_due_to_tooling: a phase's raw research reported a
        tool/search failure — observation was incomplete; absence of evidence
        is NOT a market conclusion;
      - extraction_or_search_failure: the run is partial because extraction
        parsing failed or was salvaged — also not a clean market conclusion;
      - evidence_missing: every research pass completed without detected
        tool/search failure and evidence was genuinely thin — a real,
        observed market conclusion.
    """
    tool_failures = tool_failures or []
    tool_failed_nums = sorted({f["phase"] for f in tool_failures})
    completed_nums = {p.phase_number for p in phase_results}
    phases_not_run = sorted(set(range(1, 6)) - completed_nums)

    phases = []
    for p in phase_results:
        supports = [gid for gid, ph in GATE_TO_PHASES.items() if p.phase_number in ph]
        phases.append({
            "phase": p.phase_number,
            "name": p.phase_name,
            "status": p.status.value,
            "observation_state": _phase_observation_state(
                p, p.phase_number in tool_failed_nums
            ),
            "reason": p.reason,
            "accepted_source_count": len(p.sources_collected),
            "supports_e1_gates": supports,
        })
    for num in phases_not_run:
        phases.append({
            "phase": num,
            "name": PHASE_NAMES.get(num, f"Phase {num}"),
            "status": "NOT_RUN",
            "observation_state": "not_observed",
            "reason": "Phase did not run (an earlier hard gate failed first).",
            "accepted_source_count": 0,
            "supports_e1_gates": [gid for gid, ph in GATE_TO_PHASES.items() if num in ph],
        })

    failing = _ordered_failing(_failing_e1_gates(e1_artifact))
    run_partial = bool(tool_failures) or extraction_error_count > 0 or run_status in (
        "partial", "failed",
    )

    # Missing-evidence vs tooling-failure. This distinction is diagnostic only —
    # it never raises a verdict; tool failure is neither evidence for nor
    # against demand.
    if tool_failures:
        failed_desc = ", ".join(
            f"Phase {f['phase']} ({f['phase_name']}: {'/'.join(f['failure_types'])})"
            for f in tool_failures
        )
        failure_mode = "evidence_incomplete_due_to_tooling"
        failure_note = (
            f"Research/tool failure detected in {failed_desc}. Observation of the market "
            "was incomplete, so any evidence shortfall in the affected phase(s) is NOT a "
            "clean market conclusion (it does not show the evidence is absent — it shows "
            "the run could not look properly). The verdict remains fail-closed; re-run "
            "before drawing any market conclusion from the affected phases."
        )
    elif extraction_error_count > 0 or run_status in ("partial", "failed"):
        failure_mode = "extraction_or_search_failure"
        failure_note = (
            f"Run status '{run_status}' with {extraction_error_count} extraction parse "
            f"error(s) and {extraction_salvage_count} salvage event(s): the shortfall is at "
            "least partly a tooling/extraction failure, not proof that demand is absent. "
            "Re-run before concluding the market is empty."
        )
    elif failing:
        failure_mode = "evidence_missing"
        failure_note = (
            "All research passes completed without detected tool/search failure; the "
            "shortfall reflects genuinely missing/thin evidence after a clean search, "
            "not a tooling failure."
        )
    else:
        failure_mode = "none"
        failure_note = (
            "All E1 gates passed on real documented evidence with no detected "
            "tool/search failure."
        )

    # Which failing gates were starved by tooling vs genuinely unsupported vs
    # simply never observed.
    gates_tooling, gates_clean, gates_not_observed = [], [], []
    for gate in failing:
        feed = GATE_TO_PHASES.get(gate, [])
        if any(n in tool_failed_nums for n in feed):
            gates_tooling.append(gate)
        elif feed and all(n in phases_not_run for n in feed):
            gates_not_observed.append(gate)
        else:
            gates_clean.append(gate)

    uncertainty_types = classify_uncertainty(
        tool_failures=tool_failures,
        extraction_error_count=extraction_error_count,
        extraction_salvage_count=extraction_salvage_count,
        failing_gates=failing,
        phases_not_run=phases_not_run,
    )

    grade_counts = signals.get("grade_counts", {}) or {}
    generic_only = (grade_counts.get("A", 0) == 0 and grade_counts.get("B", 0) == 0
                    and grade_counts.get("C", 0) > 0)

    return {
        "verdict": brief.review_verdict,
        "internal_decision": brief.decision.value,
        "run_partial": run_partial,
        # True whenever observation was incomplete: nothing in this run may be
        # read as a conclusion about the market.
        "not_a_market_conclusion": bool(tool_failures) or extraction_error_count > 0,
        "phases": phases,
        "clean_phases": sorted(completed_nums - set(tool_failed_nums)),
        "phases_with_tool_failure": tool_failed_nums,
        "phases_not_run": phases_not_run,
        "tool_failures": tool_failures,
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
        "failing_e1_gates": failing,
        "gates_not_fully_evaluable_due_to_tooling": gates_tooling,
        "gates_unsupported_after_clean_search": gates_clean,
        "gates_not_observed": gates_not_observed,
        "uncertainty_types": uncertainty_types,
        "belief_state": belief_state or {},
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
# Next-evidence plan (value-of-information ranked)
# --------------------------------------------------------------------------- #
# Per-gate value of information: how much resolving this gate could change the
# verdict. Hard gates that block approval outright are "critical"; supporting
# gates are "high"; synthesis gates that depend on other evidence are lower.
# This RANKS what to collect next — it is never itself evidence and never
# lowers a threshold.
_GATE_VOI = {
    "no_fabrication": (
        "critical",
        "Integrity gate; nothing can pass while fabricated/placeholder evidence is present.",
    ),
    "scope_lock": (
        "critical",
        "Hard gate; evidence must lock to the Base member before anything else counts.",
    ),
    "minimum_real_observed_evidence": (
        "critical",
        "Hard gate; fewer than 3 real observed sources blocks every downstream gate.",
    ),
    "demand_signal_exists": (
        "critical",
        "Hard gate; without real category demand signals E1 cannot pass.",
    ),
    "buyer_language_captured": (
        "critical",
        "Hard gate; without verbatim buyer-pain evidence E1 cannot pass.",
    ),
    "observed_price_band": (
        "high",
        "Needed for price validation, but cannot override missing buyer pain.",
    ),
    "competitor_presence": (
        "high",
        "Needed to prove comparable alternatives exist and to support gap analysis.",
    ),
    "specific_missing_mechanism_gap": (
        "medium_to_high",
        "Can only be evaluated after enough buyer-language and competitor evidence exists.",
    ),
    "fit_to_andrew_authored_primitive": (
        "medium",
        "Fit assessment; resolves only once buyer-language and competitor evidence exist.",
    ),
}

# High-signal source types per gate — better evidence TARGETING, never a
# lowered bar (each still has to clear the existing validators and gates).
_GATE_BEST_SOURCES = {
    "buyer_language_captured": [
        "negative reviews", "low-star reviews", "forum complaints",
        "reddit threads", "YouTube comments", "community questions",
    ],
    "observed_price_band": [
        "marketplace product listing", "public product page with visible price",
        "public competitor pricing page",
    ],
    "competitor_presence": [
        "competitor product page", "marketplace listing",
        "comparison article", "review page",
    ],
    "demand_signal_exists": [
        "marketplace category page with real listings", "live product listings",
        "public search-interest page (directional only — not buyer pain)",
    ],
    "specific_missing_mechanism_gap": [
        "negative reviews naming the missing mechanism",
        "competitor teardown of what existing tools do not govern",
        "buyer complaints that existing tools organize but do not validate",
    ],
}


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
    tool_failures: Optional[List[dict]] = None,
    phases_not_run: Optional[List[int]] = None,
) -> dict:
    """Produce a specific next-evidence plan for a PARK/REVISE verdict.

    Targets are ranked by value of information: which gate the evidence affects
    and how much resolving it could change the verdict. The plan also
    distinguishes *why* each gate is open — evidence genuinely missing after a
    clean search vs incomplete because tooling failed vs never observed — so a
    re-run knows whether to search differently or simply retry.
    """
    verdict = brief.review_verdict
    failing = _ordered_failing(_failing_e1_gates(e1_artifact))
    tool_failed_nums = {f["phase"] for f in (tool_failures or [])}
    not_run = set(phases_not_run or [])

    def _evidence_state(gate: str) -> str:
        feed = GATE_TO_PHASES.get(gate, [])
        if any(n in tool_failed_nums for n in feed):
            return "incomplete_due_to_tool_failure"
        if feed and all(n in not_run for n in feed):
            return "not_observed"
        return "missing_after_clean_search"

    next_targets = []
    for gate in failing:
        voi, reason = _GATE_VOI.get(
            gate, ("medium", "Open gate; resolving it informs the verdict.")
        )
        state = _evidence_state(gate)
        if state == "incomplete_due_to_tool_failure":
            reason += (
                " NOTE: this gate's research pass hit a tool/search failure, so the "
                "evidence is INCOMPLETE, not proven absent — re-running the same "
                "observation has high value before changing the search itself."
            )
        next_targets.append({
            "gate": gate,
            "value_of_information": voi,
            "reason": reason,
            "evidence_state": state,
            "best_source_types": _GATE_BEST_SOURCES.get(gate, []),
        })

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
        "next_evidence_targets": next_targets,
        "targeted_search_plan": targeted,
        "guardrails": [
            "Run more/better searches — do NOT lower any threshold.",
            "Only verbatim, attributable buyer pain counts as buyer language.",
            "Only verified observed prices count for the price band.",
            "No fabricated sources, invented quotes, guessed prices, or placeholder URLs.",
            "Value-of-information ranking guides collection; it is never itself evidence.",
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
    targets = plan.get("next_evidence_targets", [])
    if targets:
        L.append("## Value-of-Information Ranking")
        L.append("")
        L.append("| Gate | VOI | Evidence state | Best source types |")
        L.append("| ---- | --- | -------------- | ----------------- |")
        for t in targets:
            L.append(
                f"| `{t['gate']}` | {t['value_of_information']} | {t['evidence_state']} | "
                f"{', '.join(t.get('best_source_types', [])) or '—'} |"
            )
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
