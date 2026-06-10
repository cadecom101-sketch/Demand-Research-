"""Tests for research/tool-failure detection, honest diagnostics, and the
belief-state distinction between clean missing evidence and partial/tool-failed
observation.

The motivating live-run bug: Phase 2 raw research clearly reported a
tool/search failure ("Server tool use limit exceeded", "I have zero verifiable
evidence", "captured none of the returned data") but decision_diagnostics.json
classified the run as clean `evidence_missing` with "Run completed cleanly".
These tests prove that misclassification can no longer happen, and that the
fix never weakens a gate: tool-failed runs stay fail-closed (PARK ceiling, B3
LOCKED, recording external, public execution NONE).

NOTE: all failure/quote strings below are synthetic detector fixtures modeled
on the observed live-run text — they are not real demand evidence.
"""

import asyncio
import json
import types

from demand_research.models import Decision, PhaseStatus, ProductHypothesis
from demand_research.research.claude_researcher import ClaudeResearcher
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.audit.recorder import RunRecorder
from demand_research.tool_failure import (
    build_belief_state,
    classify_uncertainty,
    detect_phase_tool_failure,
    detect_run_tool_failures,
    detect_signals_in_text,
)


# --------------------------------------------------------------------------- #
# Fakes: same harness shape as the workflow tests, plus per-call research text
# --------------------------------------------------------------------------- #
class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.__dict__.setdefault("type", "text")
        self.__dict__.setdefault("citations", None)


class _Response:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class _FakeMessages:
    """Research/extraction fake. `texts_by_call[i]` is the raw research text the
    i-th research pass returns; `sources_by_call[i]` are its extracted sources."""

    def __init__(self, sources_by_call, gap_payload, texts_by_call=None):
        self._sources_by_call = list(sources_by_call)
        self._texts_by_call = list(texts_by_call or [])
        self._gap_payload = gap_payload
        self._research_idx = 0

    def create(self, **kwargs):
        if kwargs.get("tools"):
            idx = min(self._research_idx, len(self._sources_by_call) - 1)
            text = (
                self._texts_by_call[idx]
                if idx < len(self._texts_by_call) and self._texts_by_call[idx]
                else "Found real listings in search."
            )
            self._research_idx += 1
            srcs = self._sources_by_call[idx]
            items = [types.SimpleNamespace(url=s["url"]) for s in srcs]
            return _Response([
                _Block(type="text", text=text),
                _Block(type="web_search_tool_result", content=items),
            ])
        prompt = kwargs["messages"][0]["content"]
        if '"is_structural"' in prompt:
            payload = self._gap_payload
        else:
            idx = min(self._research_idx - 1, len(self._sources_by_call) - 1)
            payload = {"sources": self._sources_by_call[max(idx, 0)]}
        return _Response([_Block(type="text", text=json.dumps(payload))])


def _fake_researcher(sources_by_call, gap_payload, texts_by_call=None):
    client = types.SimpleNamespace(
        messages=_FakeMessages(sources_by_call, gap_payload, texts_by_call)
    )
    return ClaudeResearcher(client=client)


def _hypothesis():
    return ProductHypothesis(
        product_name="Base — Retail Instant-Download OS",
        target_buyer="Solo Etsy-first seller of instant-download digital products",
        buyer_job="Decide which digital product idea to make before wasting build time",
        product_format="Notion operating system (instant-download / retail digital products)",
        primary_channel="Etsy-first",
        missing_mechanism_hypothesis="Gates demand proof and listing readiness before launch",
    )


def _src(prefix, i, *, price=False, quote=None):
    return {
        "source_name": f"{prefix} {i}",
        "url": f"https://www.etsy.com/listing/{prefix}-{i}",
        "platform": "Etsy",
        "price": (12.0 + i) if price else None,
        "buyer_language": quote,
        "is_direct_quote": (True if quote else None),
        "what_it_proves": "A comparable product exists.",
        "what_it_does_not_prove": "Does not prove conversion demand.",
        "gap_note": "Tracks listings but does not gate launch readiness.",
    }


_PAIN = "I wasted hours making digital downloads and got no sales"
_GAP = {"is_structural": True, "gap_statement": "Gates launch before motion.",
        "reason": "Competitors only track after the fact."}

# Synthetic reproduction of the observed live-run Phase 2 raw research output.
_PHASE2_FAILURE_TEXT = (
    "I was unable to complete this research. The first search batch executed "
    "server-side, but a parsing bug captured none of the returned data. "
    'Subsequent attempts hit "Server tool use limit exceeded". '
    "I have zero verifiable evidence to report. No evidence captured — due to "
    'tool failure, not a genuine "no pain exists" conclusion. The absence of '
    "evidence in this report reflects a broken tool session, not a researched "
    "conclusion about the market."
)


def _run_tool_failed_workflow(tmp_path):
    """Phase 1 passes cleanly; Phase 2 hits the observed tool failure."""
    sources_by_call = [
        [_src("signal", i) for i in range(4)],  # phase 1: clean, passes
        [],                                      # phase 2: nothing captured
        [], [],
    ]
    texts_by_call = [None, _PHASE2_FAILURE_TEXT, None, None]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(
        researcher=_fake_researcher(sources_by_call, _GAP, texts_by_call)
    )
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    return brief, rec


def _run_clean_missing_workflow(tmp_path):
    """Phase 1 passes; Phase 2 finds genuinely nothing after a clean search."""
    sources_by_call = [[_src("signal", i) for i in range(4)], [], [], []]
    texts_by_call = [
        None,
        "The search failed to surface any buyer complaints about this job. "
        "No real pain found in these words — a genuine no-evidence result.",
        None, None,
    ]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(
        researcher=_fake_researcher(sources_by_call, _GAP, texts_by_call)
    )
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    return brief, rec


# --------------------------------------------------------------------------- #
# 1. Signal detection (pure functions)
# --------------------------------------------------------------------------- #
def test_signal_detection_catches_live_run_phrases():
    signals = detect_signals_in_text(_PHASE2_FAILURE_TEXT)
    categories = {s["category"] for s in signals}
    assert "provider_limit" in categories          # Server tool use limit exceeded
    assert "incomplete_research" in categories     # unable to complete this research
    assert "capture_failure" in categories         # captured none of the returned data
    # Every record carries a short, auditable preview around the match.
    for s in signals:
        assert s["matched_signal"]
        assert s["preview"] and len(s["preview"]) <= 200


def test_individual_spec_signals_detected():
    cases = {
        "Server tool use limit exceeded": "provider_limit",
        "I was unable to complete this research": "incomplete_research",
        "zero verifiable evidence": "incomplete_research",
        "this reflects a broken tool session": "incomplete_research",
        "not a researched conclusion": "incomplete_research",
        "captured none of the returned data": "capture_failure",
        "my search capability being cut off": "provider_limit",
        "a tool/search failure occurred": "tool_error",
        "I was unable to search for reviews": "tool_error",
        "I was unable to browse the listing": "tool_error",
        "the request timed out": "timeout",
        "rate limit exceeded on the provider": "rate_limited",
        "quota exceeded for searches": "rate_limited",
        "the page was blocked": "blocked",
    }
    for text, expected_category in cases.items():
        cats = {s["category"] for s in detect_signals_in_text(text)}
        assert expected_category in cats, f"missed: {text!r}"


def test_clean_no_evidence_text_is_not_a_tool_failure():
    # "search failed to find/surface X" is a CLEAN thin-market statement: the
    # tool worked; the market was thin. It must never be inflated into a
    # tool failure (that would let a re-run claim incomplete observation).
    clean_texts = [
        "The search failed to surface any relevant buyer complaints.",
        "My searches failed to find verbatim pain quotes; the market looks thin.",
        "Web search failed to turn up comparable listings with prices.",
        "No real pain found in these words — a genuine no-evidence result.",
        "",
    ]
    for text in clean_texts:
        assert detect_signals_in_text(text) == [], f"false positive on: {text!r}"


def test_phase_failure_severity_critical_vs_degraded():
    critical = detect_phase_tool_failure(
        2, "Buyer Language Mining", _PHASE2_FAILURE_TEXT, accepted_source_count=0
    )
    assert critical is not None
    assert critical["failure_severity"] == "critical"
    assert critical["phase_partial"] is True
    assert critical["run_partial"] is True
    assert critical["prevents_clean_certification"] is True

    degraded = detect_phase_tool_failure(
        2, "Buyer Language Mining",
        "Found several quotes, but later searches hit Server tool use limit exceeded.",
        accepted_source_count=3,
    )
    assert degraded is not None and degraded["failure_severity"] == "degraded"


def test_search_metadata_detection_without_text_signal():
    # Provider-reported tool_error/rate_limited search attempts flag the phase
    # even when the research prose itself says nothing about the failure.
    record = detect_phase_tool_failure(
        3, "Price Band Mapping", "Some ordinary findings text.",
        accepted_source_count=0,
        search_failures={"tool_error": 2, "rate_limited": 1},
    )
    assert record is not None
    assert record["detection_sources"] == ["search_metadata"]
    assert "tool_error" in record["failure_types"]
    assert "rate_limited" in record["failure_types"]


def test_recorder_tracks_search_failures_by_phase(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    rec.log_searches("phase_2", "Buyer Language Mining", [
        {"query": "a", "status": "results_found", "result_count": 3},
        {"query": "b", "status": "tool_error", "error_message": "boom"},
        {"query": "c", "status": "rate_limited", "error_message": "max_uses_exceeded"},
    ])
    failures = rec.search_failures_by_phase()
    assert failures == {"phase_2": {"tool_error": 1, "rate_limited": 1}}


# --------------------------------------------------------------------------- #
# 2. The observed bug: Phase 2 tool failure is no longer "clean evidence_missing"
# --------------------------------------------------------------------------- #
def test_phase2_tool_failure_not_classified_as_clean_evidence_missing(tmp_path):
    _, rec = _run_tool_failed_workflow(tmp_path)
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text())

    assert diag["failure_mode"] == "evidence_incomplete_due_to_tooling"
    assert diag["failure_mode"] != "evidence_missing"
    assert diag["run_partial"] is True
    assert diag["not_a_market_conclusion"] is True
    assert diag["phases_with_tool_failure"] == [2]
    # Diagnostics must never claim a clean run when a phase tool-failed.
    assert "completed cleanly" not in json.dumps(diag).lower()
    # The note explains the epistemic distinction.
    note = diag["failure_mode_note"].lower()
    assert "not a" in note and "market conclusion" in note.replace("clean ", "")


def test_tool_failed_gates_separated_from_clean_gates(tmp_path):
    _, rec = _run_tool_failed_workflow(tmp_path)
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text())
    # buyer_language_captured is fed by tool-failed Phase 2 -> not fully evaluable.
    assert "buyer_language_captured" in diag["gates_not_fully_evaluable_due_to_tooling"]
    assert "buyer_language_captured" not in diag["gates_unsupported_after_clean_search"]
    # Phase entries carry observation states; phases 3-5 now run in
    # diagnostic-only continuation instead of being skipped — their evidence is
    # collected for future cycles but never satisfies a gate in this run.
    by_phase = {p["phase"]: p for p in diag["phases"]}
    assert by_phase[2]["observation_state"] == "partial_tool_failure"
    assert by_phase[1]["observation_state"] == "accepted"
    for n in (3, 4, 5):
        assert by_phase[n]["status"] == "DIAGNOSTIC_ONLY"
        assert by_phase[n]["observation_state"] == "diagnostic_only"
    assert diag["phases_not_run"] == []
    cont = diag["diagnostic_continuation"]
    assert cont["triggered_by_phase"] == 2
    assert cont["diagnostic_phases"] == [3, 4, 5]


def test_uncertainty_types_in_diagnostics(tmp_path):
    _, rec = _run_tool_failed_workflow(tmp_path)
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text())
    types_found = diag["uncertainty_types"]
    assert "outcome_uncertainty" in types_found
    assert "state_uncertainty" in types_found        # market not fully observed
    assert "interaction_uncertainty" in types_found  # provider limit hit
    assert "model_uncertainty" in types_found        # capture/parsing failure


def test_clean_missing_run_stays_clean_evidence_missing(tmp_path):
    _, rec = _run_clean_missing_workflow(tmp_path)
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text())
    assert diag["failure_mode"] == "evidence_missing"
    assert diag["run_partial"] is False
    assert diag["not_a_market_conclusion"] is False
    assert diag["tool_failures"] == []
    assert "interaction_uncertainty" not in diag["uncertainty_types"]
    # No tool-failure artifact rows for a clean run.
    assert (rec.run_dir / "research_tool_failures.jsonl").read_text().strip() == ""


# --------------------------------------------------------------------------- #
# 3. Audit artifact: research_tool_failures.jsonl
# --------------------------------------------------------------------------- #
def test_tool_failure_writes_audit_artifact(tmp_path):
    _, rec = _run_tool_failed_workflow(tmp_path)
    lines = [json.loads(l) for l in
             (rec.run_dir / "research_tool_failures.jsonl").read_text().splitlines()
             if l.strip()]
    assert len(lines) == 1
    record = lines[0]
    assert record["run_id"] == rec.run_id
    assert record["timestamp_utc"]
    assert record["phase"] == 2
    assert record["phase_id"] == "phase_2"
    assert record["phase_name"] == "Buyer Language Mining"
    assert record["failure_severity"] == "critical"
    assert "raw_research_text" in record["detection_sources"]
    assert record["matched_signals"] and all(
        s["preview"] for s in record["matched_signals"]
    )
    assert record["phase_partial"] is True
    assert record["run_partial"] is True
    assert record["prevents_clean_certification"] is True
    # No secrets in the artifact.
    blob = json.dumps(record).lower()
    assert "api_key" not in blob and "anthropic_api_key" not in blob


def test_raw_research_artifact_preserved_verbatim(tmp_path):
    _, rec = _run_tool_failed_workflow(tmp_path)
    raw = (rec.run_dir / "raw_research_findings_phase_2.txt").read_text()
    assert raw == _PHASE2_FAILURE_TEXT  # byte-for-byte, never rewritten


# --------------------------------------------------------------------------- #
# 4. Fail-closed governance is unchanged (strictly tightened, never loosened)
# --------------------------------------------------------------------------- #
def test_tool_failed_run_remains_fail_closed(tmp_path):
    brief, rec = _run_tool_failed_workflow(tmp_path)
    # The existing tool_failure hard gate caps a partial run at PARK.
    assert brief.run_status == "partial"
    assert brief.decision in (Decision.PARK, Decision.KILL)
    e1 = brief.e1_review or {}
    assert e1.get("review_verdict") != "E1_APPROVED_TO_RECORD"
    assert e1.get("b3_status") == "LOCKED"
    assert e1.get("recording_status") != "RECORDED"
    assert e1.get("public_execution_status") == "NONE"
    manifest = json.loads((rec.run_dir / "run_manifest.json").read_text())
    assert manifest["run_status"] == "partial"


def test_clean_full_chain_can_still_reach_approval(tmp_path):
    # Regression: honest failure detection must not block a genuinely clean,
    # fully evidenced run from reaching (at most) E1_APPROVED_TO_RECORD.
    sources_by_call = [
        [_src("signal", i) for i in range(5)],
        [_src("pain", i, quote=f"{_PAIN} #{i}") for i in range(6)],
        [_src("price", i, price=True) for i in range(5)],
        [_src("competitor", i) for i in range(5)],
    ]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text())
    assert diag["run_partial"] is False
    assert diag["tool_failures"] == []
    assert diag["failure_mode"] in ("none", "evidence_missing")
    e1 = brief.e1_review or {}
    assert e1.get("review_verdict") in (
        "E1_APPROVED_TO_RECORD", "E1_REVISE_BEFORE_RECORDING")
    assert e1.get("b3_status") == "LOCKED"
    assert e1.get("recording_status") != "RECORDED"


# --------------------------------------------------------------------------- #
# 5. Belief state
# --------------------------------------------------------------------------- #
def test_belief_state_distinguishes_observation_states(tmp_path):
    _, rec = _run_tool_failed_workflow(tmp_path)
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text())
    belief = diag["belief_state"]

    assert belief["category_exists"]["status"] == "supported"
    assert belief["category_exists"]["evidence_count"] >= 3
    assert belief["category_exists"]["confidence"] is not None

    bp = belief["buyer_pain_articulated"]
    assert bp["status"] == "partial_tool_failure"
    assert bp["confidence"] is None
    assert "not a market conclusion" in bp["reason"].lower()

    # Phases 3/4 now run in diagnostic continuation: their observation status
    # says so explicitly instead of claiming they were never observed.
    assert belief["price_band_observed"]["status"] == "diagnostic_only"
    assert belief["competitor_presence"]["status"] == "diagnostic_only"
    assert belief["missing_mechanism_gap"]["status"] == "not_evaluable"


def test_belief_state_clean_run_uses_unsupported_after_clean_search(tmp_path):
    _, rec = _run_clean_missing_workflow(tmp_path)
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text())
    bp = diag["belief_state"]["buyer_pain_articulated"]
    assert bp["status"] == "unsupported_after_clean_search"
    assert "genuine" in bp["reason"].lower()


def test_belief_state_unit_supported_with_tool_failure_caps_confidence():
    # Gate passed but the phase's observation window was degraded: support is
    # kept, diagnostic confidence is capped, and the reason says why.
    phase_results = [types.SimpleNamespace(
        phase_number=1, phase_name="Signal Discovery", findings="ok",
        sources_collected=[1, 2, 3, 4],
    )]
    e1_artifact = {"gates": [{"gate_id": "demand_signal_exists", "status": "PASS"}]}
    signals = {"category_signal_count": 4}
    failures = [{"phase": 1, "phase_name": "Signal Discovery",
                 "failure_types": ["rate_limited"]}]
    belief = build_belief_state(
        phase_results=phase_results, e1_artifact=e1_artifact,
        signals=signals, tool_failures=failures,
    )
    entry = belief["category_exists"]
    assert entry["status"] == "supported"
    assert entry["confidence"] <= 0.6
    assert "partial" in entry["reason"].lower()


def test_classify_uncertainty_unit():
    # Clean, fully-passing run: only irreducible outcome uncertainty remains.
    assert classify_uncertainty(
        tool_failures=[], extraction_error_count=0, extraction_salvage_count=0,
        failing_gates=[], phases_not_run=[],
    ) == ["outcome_uncertainty"]
    # Salvage events imply model uncertainty even without tool failures.
    assert "model_uncertainty" in classify_uncertainty(
        tool_failures=[], extraction_salvage_count=1,
        failing_gates=[], phases_not_run=[],
    )
    # Provider-limit failures imply interaction + state uncertainty.
    failure = [{"phase": 2, "failure_types": ["provider_limit"]}]
    types_found = classify_uncertainty(
        tool_failures=failure, failing_gates=["buyer_language_captured"],
        phases_not_run=[3, 4, 5],
    )
    assert "interaction_uncertainty" in types_found
    assert "state_uncertainty" in types_found


# --------------------------------------------------------------------------- #
# 6. Value-of-information next-evidence plan
# --------------------------------------------------------------------------- #
def test_voi_plan_ranks_buyer_language_critical_and_flags_tooling(tmp_path):
    _, rec = _run_tool_failed_workflow(tmp_path)
    plan = json.loads((rec.run_dir / "next_evidence_plan.json").read_text())
    assert plan["applies"] is True
    targets = {t["gate"]: t for t in plan["next_evidence_targets"]}

    bl = targets["buyer_language_captured"]
    assert bl["value_of_information"] == "critical"
    assert bl["evidence_state"] == "incomplete_due_to_tool_failure"
    assert "negative reviews" in bl["best_source_types"]
    assert "low-star reviews" in bl["best_source_types"]

    # Gates fed only by diagnostic-continuation phases are
    # 'collected_diagnostically', never 'missing_after_clean_search'.
    if "observed_price_band" in targets:
        assert targets["observed_price_band"]["evidence_state"] == "collected_diagnostically"
        assert targets["observed_price_band"]["value_of_information"] == "high"


def test_voi_plan_clean_run_says_missing_after_clean_search(tmp_path):
    _, rec = _run_clean_missing_workflow(tmp_path)
    plan = json.loads((rec.run_dir / "next_evidence_plan.json").read_text())
    targets = {t["gate"]: t for t in plan["next_evidence_targets"]}
    assert targets["buyer_language_captured"]["evidence_state"] == "missing_after_clean_search"
    # The plan guides collection; it never claims to be evidence itself.
    assert any("never itself evidence" in g for g in plan["guardrails"])
    # Existing plan surface is unchanged (regression).
    assert plan["product_name"] == _hypothesis().product_name
    assert plan["targeted_search_plan"]


# --------------------------------------------------------------------------- #
# 7. Scorecard clarity
# --------------------------------------------------------------------------- #
def test_scorecard_partial_clarity_for_tool_failed_run(tmp_path):
    _, rec = _run_tool_failed_workflow(tmp_path)
    scorecard = json.loads((rec.run_dir / "evidence_scorecard.json").read_text())
    assert scorecard["clean_vs_partial"] == "partial_tool_failure"
    assert "not a clean market score" in scorecard["partial_run_caps"].lower()
    assert "PARK" in scorecard["partial_run_caps"]
    assert scorecard["why_score_does_not_approve"]
    assert "buyer_language_captured" in scorecard["evidence_not_observed_due_to_tooling"]


def test_scorecard_clean_run_labeled_clean(tmp_path):
    _, rec = _run_clean_missing_workflow(tmp_path)
    scorecard = json.loads((rec.run_dir / "evidence_scorecard.json").read_text())
    assert scorecard["clean_vs_partial"] == "clean"
    assert scorecard.get("partial_run_caps") is None
    assert "score" in scorecard["score_meaning"].lower()


# --------------------------------------------------------------------------- #
# 8. Markdown brief surfaces the honesty fields
# --------------------------------------------------------------------------- #
def test_markdown_brief_reports_tool_failure_honestly(tmp_path):
    _, rec = _run_tool_failed_workflow(tmp_path)
    md = (rec.run_dir / "demand_brief.md").read_text()
    assert "evidence_incomplete_due_to_tooling" in md
    assert "NOT a market conclusion" in md
    assert "partial_tool_failure" in md
    assert "completed cleanly" not in md.lower()


# --------------------------------------------------------------------------- #
# 9. Detector unit coverage for run-level wrapper
# --------------------------------------------------------------------------- #
def test_detect_run_tool_failures_only_flags_failed_phases():
    phases = [
        types.SimpleNamespace(phase_number=1, phase_name="Signal Discovery",
                              findings="Found real listings.", sources_collected=[1, 2, 3]),
        types.SimpleNamespace(phase_number=2, phase_name="Buyer Language Mining",
                              findings=_PHASE2_FAILURE_TEXT, sources_collected=[]),
    ]
    failures = detect_run_tool_failures(phases)
    assert [f["phase"] for f in failures] == [2]
    assert failures[0]["failure_severity"] == "critical"


def test_detect_run_tool_failures_uses_search_metadata():
    phases = [
        types.SimpleNamespace(phase_number=3, phase_name="Price Band Mapping",
                              findings="Ordinary findings.", sources_collected=[]),
    ]
    failures = detect_run_tool_failures(
        phases, {"phase_3": {"rate_limited": 2}},
    )
    assert len(failures) == 1
    assert failures[0]["detection_sources"] == ["search_metadata"]
    assert failures[0]["failure_types"] == ["rate_limited"]

# --------------------------------------------------------------------------- #
# 10. Negative-review classification: generic vs target-pain
# --------------------------------------------------------------------------- #
def test_generic_negative_reviews_are_not_target_pain():
    from demand_research.research.evidence_validator import EvidenceValidator
    v = EvidenceValidator()
    generic = (
        "bad download", "the file didn't open", "seller was rude to me",
        "too expensive for what you get", "ugly design", "not enough pages",
        "hard to use", "I didn't like it", "poor quality", "not worth it",
        "instructions unclear", "the template broke", "couldn't access file",
        "asked for a refund, waste of money",
    )
    for q in generic:
        assert v.is_generic_negative_review(q) is True, f"should reject: {q!r}"


def test_target_pain_negative_reviews_are_kept():
    from demand_research.research.evidence_validator import EvidenceValidator
    v = EvidenceValidator()
    on_target = (
        "This didn't help me know what product to make.",
        "Still left me guessing what would sell.",
        "Too generic for product research.",
        "It tracks listings but doesn't validate demand.",
        "I made and listed products and still got no sales.",
        "I needed help deciding what to build, not just organizing tasks.",
        "No demand validation, no idea scoring, no build or kill support.",
        "I wanted help choosing the right product idea, not another planner.",
        "This helped organize my shop but did not help me know what customers want.",
        "I spent hours building a template and nobody bought it.",
        # Generic complaint PLUS a target-pain connection in the exact quote: kept.
        "The template broke, and it still didn't help me decide what to make.",
    )
    for q in on_target:
        assert v.is_generic_negative_review(q) is False, f"should keep: {q!r}"


def test_phase2_rejects_generic_negative_reviews_durably(tmp_path):
    # Phase 2 returns only generic negative reviews -> zero pain artifacts,
    # rejected durably with an explicit reason (not silently dropped, not
    # counted as buyer pain).
    sources_by_call = [
        [_src("signal", i) for i in range(4)],
        [_src("angry", 0, quote="bad download, want a refund"),
         _src("angry", 1, quote="seller was rude and the file didn't open"),
         _src("angry", 2, quote="too expensive and ugly design")],
        [], [],
    ]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))

    assert brief.phase_2_result.status == PhaseStatus.FAIL
    assert brief.phase_2_result.sources_collected == []
    rejected = [json.loads(l) for l in
                (rec.run_dir / "rejected_sources.jsonl").read_text().splitlines()
                if l.strip()]
    negative_rejections = [r for r in rejected
                           if r["rejection_reason"] == "generic_negative_not_target_pain"]
    assert len(negative_rejections) == 3
    # Fails closed.
    e1 = brief.e1_review or {}
    assert e1.get("review_verdict") != "E1_APPROVED_TO_RECORD"
    assert e1.get("b3_status") == "LOCKED"


def test_phase2_accepts_target_pain_negative_reviews(tmp_path):
    # Negative reviews that directly name the target pain ARE captured as
    # buyer-language artifacts when verbatim and source-linked.
    quotes = [
        "This didn't help me know what product to make.",
        "I made digital products and still got no sales.",
        "It tracks listings but doesn't validate demand.",
    ]
    sources_by_call = [
        [_src("signal", i) for i in range(4)],
        [_src("pain", i, quote=q) for i, q in enumerate(quotes)],
        [], [],
    ]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))

    assert brief.phase_2_result.status == PhaseStatus.PASS
    captured = [s.buyer_language_captured for s in brief.phase_2_result.sources_collected]
    assert sorted(captured) == sorted(quotes)
