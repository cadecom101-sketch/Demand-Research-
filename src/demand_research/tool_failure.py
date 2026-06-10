"""Tool-failure / partial-research detection + belief-state construction.

This module treats each run as a decision problem under uncertainty:

  - observations: raw research text, search metadata, extraction results;
  - belief state: how well each E1 gate is currently supported, and *why*;
  - action: the governed verdict (PARK / REVISE / APPROVED_TO_RECORD / KILL).

Its single job is honesty about the observation process. A research pass that
says "Server tool use limit exceeded" or "captured none of the returned data"
did NOT observe the market — and that must never be classified as a clean
"evidence missing" market conclusion. Equally, a clean pass that genuinely
found nothing must never be inflated into a tool failure.

Nothing here changes a gate, a threshold, or a verdict upward:

  - Tool failure is NOT evidence for demand.
  - Tool failure is NOT evidence against demand.
  - A detected failure marks the phase (and therefore the run) partial, which
    the existing `tool_failure` hard gate caps at PARK — strictly fail-closed.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# --------------------------------------------------------------------------- #
# Signal catalog
#
# Conservative, phrase-level signals matched case-insensitively against the
# RAW research text of a phase. Each entry is (category, compiled regex).
# Patterns are deliberately specific: "the search failed to surface anything"
# is a CLEAN no-evidence statement (search worked, market was thin) and must
# NOT match — hence the negative lookahead on "failed".
# --------------------------------------------------------------------------- #
_CLEAN_OUTCOME_VERBS = (
    r"(?:\s+to\s+(?:find|surface|turn\s+up|locate|identify|uncover|reveal|yield|produce|return))"
)

_SIGNAL_PATTERNS: List[tuple] = [
    # Provider / platform limits ------------------------------------------- #
    ("provider_limit", r"server\s+tool\s+use\s+limit\s+exceeded"),
    ("provider_limit", r"tool\s+use\s+limit\s+exceeded"),
    ("provider_limit", r"search\s+capability\s+being\s+cut\s+off"),
    ("provider_limit", r"max_uses_exceeded"),
    # Rate limiting / quota ------------------------------------------------- #
    ("rate_limited", r"rate[\s-]?limit(?:ed|s)?\s+(?:exceeded|hit|reached)"),
    ("rate_limited", r"\brate[\s-]limited\b"),
    ("rate_limited", r"too\s+many\s+requests"),
    ("rate_limited", r"quota\s+exceeded"),
    # Explicit incomplete-research statements ------------------------------- #
    ("incomplete_research", r"unable\s+to\s+complete\s+(?:this|the)\s+research"),
    ("incomplete_research", r"could\s+not\s+complete\s+(?:this|the)\s+research"),
    ("incomplete_research", r"research\s+could\s+not\s+be\s+completed"),
    ("incomplete_research", r"zero\s+verifiable\s+evidence"),
    ("incomplete_research", r"not\s+a\s+researched\s+conclusion"),
    ("incomplete_research", r"broken\s+tool\s+session"),
    ("incomplete_research", r"due\s+to\s+(?:a\s+)?tool\s+failure"),
    ("incomplete_research", r"because\s+of\s+(?:a\s+)?tool\s+failure"),
    # System failed to capture data the provider returned -------------------- #
    ("capture_failure", r"captured\s+none\s+of\s+the\s+returned\s+data"),
    ("capture_failure", r"failed\s+to\s+capture\s+the\s+returned\s+data"),
    ("capture_failure", r"parsing\s+bug"),
    ("capture_failure", r"failed\s+(?:to\s+)?parse\s+(?:of\s+)?the\s+search\s+(?:response|results?|payload)"),
    ("capture_failure", r"malformed\s+search\s+payload"),
    ("capture_failure", r"citation(?:/result)?\s+parse\s+failure"),
    # Generic tool/search errors -------------------------------------------- #
    ("tool_error", r"tool/search\s+failure"),
    ("tool_error", r"\btool\s+failure\b"),
    ("tool_error", r"search\s+tool\s+(?:failed|error|is\s+unavailable|stopped\s+working)"),
    ("tool_error", r"web\s+search\s+failed" + f"(?!{_CLEAN_OUTCOME_VERBS})"),
    ("tool_error", r"search(?:es)?\s+failed" + f"(?!{_CLEAN_OUTCOME_VERBS})"),
    ("tool_error", r"unable\s+to\s+(?:search|browse|use\s+the\s+search\s+tool)"),
    ("tool_error", r"empty\s+due\s+to\s+(?:a\s+)?tool\s+error"),
    # Timeouts / blocked access --------------------------------------------- #
    ("timeout", r"(?:request|search|api)\s+timed\s+out"),
    ("timeout", r"\bapi\s+timeout\b"),
    ("blocked", r"(?:request|page|access)\s+(?:was\s+)?blocked"),
    ("blocked", r"blocked\s+by\s+(?:a\s+)?captcha"),
]

_COMPILED_SIGNALS = [(cat, re.compile(pat, re.IGNORECASE)) for cat, pat in _SIGNAL_PATTERNS]

# Categories that point at platform/provider interaction problems (used for
# uncertainty classification).
_INTERACTION_CATEGORIES = {"provider_limit", "rate_limited", "tool_error", "timeout", "blocked"}
# Categories where the provider returned data but the system failed to keep it
# (model/system-side failure, not market state).
_MODEL_SIDE_CATEGORIES = {"capture_failure"}

_PREVIEW_CHARS = 200


def detect_signals_in_text(text: str) -> List[dict]:
    """Scan raw research text for tool/search-failure signals.

    Returns one record per matched signal pattern (first occurrence each):
    {"category", "matched_signal", "preview"}. The preview is a short
    non-secret excerpt around the match for the audit trail. An empty list
    means NO failure signal was detected — including for clean statements like
    "the search failed to surface any complaints", which describe a thin
    market, not a broken tool.
    """
    if not text:
        return []
    found: List[dict] = []
    for category, rx in _COMPILED_SIGNALS:
        m = rx.search(text)
        if not m:
            continue
        start = max(0, m.start() - 80)
        end = min(len(text), m.end() + (_PREVIEW_CHARS - 80))
        preview = re.sub(r"\s+", " ", text[start:end]).strip()
        found.append({
            "category": category,
            "matched_signal": m.group(0),
            "preview": preview[:_PREVIEW_CHARS],
        })
    return found


def detect_phase_tool_failure(
    phase_number: int,
    phase_name: str,
    raw_text: str,
    *,
    accepted_source_count: int = 0,
    search_failures: Optional[dict] = None,
) -> Optional[dict]:
    """Build a phase-level tool-failure record, or None if the phase is clean.

    Detection sources:
      - raw_research_text: signal phrases in the phase's raw research output;
      - search_metadata: provider-reported tool_error / rate_limited search
        attempt statuses for this phase.

    Severity is "critical" when the phase accepted zero sources (nothing was
    observed) and "degraded" when some sources survived (observation was
    partial). Either way the phase — and therefore the run — is partial and
    can never be certified as a clean observation.
    """
    signals = detect_signals_in_text(raw_text)
    detection_sources: List[str] = []
    categories: List[str] = []
    if signals:
        detection_sources.append("raw_research_text")
        for s in signals:
            if s["category"] not in categories:
                categories.append(s["category"])

    sf = {k: v for k, v in (search_failures or {}).items() if v}
    if sf:
        detection_sources.append("search_metadata")
        for status in ("tool_error", "rate_limited"):
            if sf.get(status) and status not in categories:
                categories.append(status)

    if not detection_sources:
        return None

    severity = "critical" if accepted_source_count == 0 else "degraded"
    return {
        "phase": phase_number,
        "phase_id": f"phase_{phase_number}",
        "phase_name": phase_name,
        "failure_types": categories,
        "failure_severity": severity,
        "detection_sources": detection_sources,
        "matched_signals": signals,
        "search_failure_counts": sf or None,
        "accepted_source_count": accepted_source_count,
        "phase_partial": True,
        "run_partial": True,
        "prevents_clean_certification": True,
        "affects_verdict": (
            "fail-closed only: the run is marked partial, which the tool_failure "
            "hard gate caps at PARK. Detection never raises a verdict."
        ),
    }


def detect_run_tool_failures(
    phase_results: List,
    search_failures_by_phase: Optional[Dict[str, dict]] = None,
) -> List[dict]:
    """Run tool-failure detection over every completed phase.

    `phase_results` are PhaseResult objects whose `findings` carry the raw
    research text verbatim. `search_failures_by_phase` maps phase_id ->
    {"tool_error": n, "rate_limited": n} from the search log.
    """
    failures: List[dict] = []
    sf_by_phase = search_failures_by_phase or {}
    for p in phase_results:
        record = detect_phase_tool_failure(
            p.phase_number,
            p.phase_name,
            p.findings or "",
            accepted_source_count=len(p.sources_collected),
            search_failures=sf_by_phase.get(f"phase_{p.phase_number}"),
        )
        if record is not None:
            failures.append(record)
    return failures


# --------------------------------------------------------------------------- #
# Uncertainty classification (diagnostic only — never a score input)
# --------------------------------------------------------------------------- #
def classify_uncertainty(
    *,
    tool_failures: List[dict],
    extraction_error_count: int = 0,
    extraction_salvage_count: int = 0,
    failing_gates: Optional[List[str]] = None,
    phases_not_run: Optional[List[int]] = None,
) -> List[str]:
    """Classify which kinds of uncertainty this run carries.

    - outcome_uncertainty: always present — desk research can never prove that
      demand will convert if the product is built.
    - model_uncertainty: LLM extraction/parsing/capture failures occurred.
    - state_uncertainty: the market state (buyer pain, prices, competitors,
      gaps) was not fully observed.
    - interaction_uncertainty: platform/provider limits, rate limits, blocked
      pages, or tool errors interfered with observation.
    """
    types = ["outcome_uncertainty"]

    failure_categories = {c for f in tool_failures for c in f.get("failure_types", [])}
    if (
        extraction_error_count > 0
        or extraction_salvage_count > 0
        or failure_categories & _MODEL_SIDE_CATEGORIES
    ):
        types.append("model_uncertainty")
    if tool_failures or (failing_gates or []) or (phases_not_run or []):
        types.append("state_uncertainty")
    if failure_categories & _INTERACTION_CATEGORIES:
        types.append("interaction_uncertainty")
    return types


# --------------------------------------------------------------------------- #
# Belief state (per-gate observation status, NOT a new scoring system)
# --------------------------------------------------------------------------- #
# Belief key -> (research phase that observes it, E1 gate it corresponds to,
# signals-dict key holding its evidence count).
_BELIEF_KEYS = {
    "category_exists": (1, "demand_signal_exists", "category_signal_count"),
    "buyer_pain_articulated": (2, "buyer_language_captured", "buyer_language_artifact_count"),
    "price_band_observed": (3, "observed_price_band", "phase3_priced_count"),
    "competitor_presence": (4, "competitor_presence", "phase4_competitor_count"),
    "missing_mechanism_gap": (5, "specific_missing_mechanism_gap", None),
}

# All observation statuses a belief entry can take. Listed here so readers and
# tests share one vocabulary.
OBSERVATION_STATUSES = (
    "supported",
    "unsupported_after_clean_search",
    "not_observed",
    "not_evaluable",
    "partial_tool_failure",
    "diagnostic_only",
)


def _diagnostic_confidence(evidence_count: int) -> float:
    """Map an evidence count to a coarse diagnostic confidence (0.5–0.9).

    This is a *reporting* aid for the belief state only. It is never read by
    the decision engine, the E1 gates, or the scorecard.
    """
    return round(min(0.9, 0.5 + 0.1 * max(0, evidence_count)), 2)


def build_belief_state(
    *,
    phase_results: List,
    e1_artifact: dict,
    signals: dict,
    tool_failures: List[dict],
    diagnostic_phases: Optional[set] = None,
) -> dict:
    """Build the per-gate belief state from what was actually observed.

    Distinguishes (per belief key):
      - supported: the gate passed on real documented evidence;
      - unsupported_after_clean_search: the phase ran cleanly and the gate
        still failed — a genuine market observation;
      - partial_tool_failure: the phase ran but research/tooling failed, so
        absence of evidence is NOT a market conclusion;
      - not_observed: the phase did not run;
      - not_evaluable: the gate's inputs (buyer language / competitors) are
        themselves missing or partial, so it cannot be assessed either way;
      - diagnostic_only: the phase ran in diagnostic continuation after an
        earlier hard-gate failure — its evidence is recorded for future cycles
        but cannot satisfy this gate in this run.
    """
    diagnostic_phases = diagnostic_phases or set()
    gate_status = {g.get("gate_id"): g.get("status") for g in e1_artifact.get("gates", [])}
    phases_ran = {p.phase_number for p in phase_results}
    sources_by_phase = {
        p.phase_number: len(p.sources_collected) for p in phase_results
    }
    tool_failed_phases = {f["phase"] for f in tool_failures}

    belief: dict = {}
    for key, (phase_num, gate_id, count_key) in _BELIEF_KEYS.items():
        count = int(signals.get(count_key, 0) or 0) if count_key else 0
        passed = gate_status.get(gate_id) == "PASS"
        tool_failed = phase_num in tool_failed_phases
        ran = phase_num in phases_ran

        if passed:
            entry = {
                "status": "supported",
                "confidence": _diagnostic_confidence(count),
                "evidence_count": count,
                "reason": f"Gate '{gate_id}' passed on real documented evidence.",
            }
            if tool_failed:
                # Supported but the observation window was degraded: keep the
                # support, cap the diagnostic confidence, and say why.
                entry["confidence"] = min(entry["confidence"], 0.6)
                entry["reason"] += (
                    " Observation was partial (tool/search failure in this phase), "
                    "so confidence is reduced."
                )
        elif tool_failed:
            entry = {
                "status": "partial_tool_failure",
                "confidence": None,
                "evidence_count": count,
                "reason": (
                    f"Phase {phase_num} research/tool failure prevented clean observation. "
                    "Absence of evidence here is not a market conclusion."
                ),
            }
        elif key == "missing_mechanism_gap" and _gap_inputs_incomplete(
            gate_status, tool_failed_phases, phases_ran
        ):
            # The gap is synthesis: when its buyer-language / competitor inputs
            # are missing or partial it is NOT evaluable either way — even if
            # Phase 5 never ran.
            entry = {
                "status": "not_evaluable",
                "confidence": None,
                "evidence_count": count,
                "reason": (
                    "Insufficient buyer-language and/or competitor evidence: the gap "
                    "can only be evaluated against observed alternatives and pain."
                ),
            }
        elif phase_num in diagnostic_phases:
            observed = int(sources_by_phase.get(phase_num, 0))
            entry = {
                "status": "diagnostic_only",
                "confidence": None,
                "evidence_count": observed,
                "reason": (
                    f"Phase {phase_num} ran in diagnostic-only continuation after an "
                    f"earlier hard-gate failure; {observed} source(s) were recorded "
                    "for future cycles but cannot satisfy this gate in this run."
                ),
            }
        elif not ran:
            entry = {
                "status": "not_observed",
                "confidence": None,
                "evidence_count": 0,
                "reason": (
                    f"Phase {phase_num} did not run (an earlier hard gate failed first), "
                    "so this was never observed."
                ),
            }
        else:
            entry = {
                "status": "unsupported_after_clean_search",
                "confidence": None,
                "evidence_count": count,
                "reason": (
                    f"Phase {phase_num} ran without detected tool/search failure and "
                    f"gate '{gate_id}' was still not satisfied — a genuine observation, "
                    "not a tooling artifact."
                ),
            }
        belief[key] = entry
    return belief


def _gap_inputs_incomplete(gate_status: dict, tool_failed_phases: set, phases_ran: set) -> bool:
    """The missing-mechanism gap needs buyer-language + competitor inputs."""
    buyer_ok = gate_status.get("buyer_language_captured") == "PASS"
    comp_ok = gate_status.get("competitor_presence") == "PASS"
    inputs_partial = bool({2, 4} & tool_failed_phases) or not ({2, 4} <= phases_ran)
    return (not buyer_ok or not comp_ok) or inputs_partial
