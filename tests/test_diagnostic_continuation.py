"""Tests for diagnostic continuation (Priority A).

When a hard-gate phase (2/3/4) fails — cleanly or via tool failure — the
remaining phases still run, but every result they produce is downgraded to
DIAGNOSTIC_ONLY. Their evidence is preserved in the NORMAL artifact files
(source_ledger.jsonl, price_band_artifacts.jsonl, competitor_map.jsonl,
missing_mechanism_gap.json) marked diagnostic_only, yet it is excluded from
every gate, claim, score, and the E1 review of the run that collected it:
the verdict is identical to a run that stopped at the failed phase, B3 stays
LOCKED, recording stays external, public execution stays NONE.

All fixture strings are synthetic; none are real demand evidence.
"""

import asyncio
import json
import types

from demand_research.models import Decision, PhaseStatus, ProductHypothesis
from demand_research.research.claude_researcher import ClaudeResearcher
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.audit.recorder import RunRecorder
from demand_research.decision_engine import DecisionEngine


# --------------------------------------------------------------------------- #
# Fakes (same harness shape as test_tool_failure_detection)
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

_PHASE2_FAILURE_TEXT = (
    "I was unable to complete this research. Subsequent attempts hit "
    '"Server tool use limit exceeded". I have zero verifiable evidence to report.'
)


def _run_phase2_clean_fail(tmp_path):
    """Phase 1 passes; Phase 2 cleanly finds nothing; Phases 3-5 collect real
    diagnostic evidence (prices + competitors + a structural gap)."""
    sources_by_call = [
        [_src("signal", i) for i in range(4)],            # phase 1: passes
        [],                                               # phase 2: clean nothing
        [_src("price", i, price=True) for i in range(4)],  # phase 3: diagnostic
        [_src("competitor", i) for i in range(4)],         # phase 4: diagnostic
    ]
    texts_by_call = [
        None,
        "The search failed to surface any buyer complaints about this job.",
        None, None,
    ]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(
        researcher=_fake_researcher(sources_by_call, _GAP, texts_by_call)
    )
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    return brief, rec


def _run_phase2_tool_failed(tmp_path):
    """Phase 2 hits a tool failure; downstream phases still collect evidence."""
    sources_by_call = [
        [_src("signal", i) for i in range(4)],
        [],
        [_src("price", i, price=True) for i in range(4)],
        [_src("competitor", i) for i in range(4)],
    ]
    texts_by_call = [None, _PHASE2_FAILURE_TEXT, None, None]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(
        researcher=_fake_researcher(sources_by_call, _GAP, texts_by_call)
    )
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    return brief, rec


def _run_clean_full_pass(tmp_path):
    sources_by_call = [
        [_src("signal", i) for i in range(5)],
        [_src("pain", i, quote=f"{_PAIN} #{i}") for i in range(6)],
        [_src("price", i, price=True) for i in range(5)],
        [_src("competitor", i) for i in range(5)],
    ]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    return brief, rec


# --------------------------------------------------------------------------- #
# 1. Continuation happens and is labeled
# --------------------------------------------------------------------------- #
def test_phases_3_to_5_run_diagnostic_after_phase2_clean_fail(tmp_path):
    brief, _ = _run_phase2_clean_fail(tmp_path)
    assert brief.phase_2_result.status == PhaseStatus.FAIL
    for result in (brief.phase_3_result, brief.phase_4_result, brief.phase_5_result):
        assert result is not None
        assert result.status == PhaseStatus.DIAGNOSTIC_ONLY
        assert "diagnostic-only continuation" in (result.diagnostic_reason or "")
        # The underlying PASS/FAIL the phase would have had is preserved.
        assert result.details["diagnostic_underlying_status"] in ("PASS", "FAIL")
    # Diagnostic phases really collected evidence.
    assert len(brief.phase_3_result.sources_collected) >= 3
    assert len(brief.phase_4_result.sources_collected) >= 3


def test_continuation_also_happens_after_tool_failed_phase2(tmp_path):
    brief, rec = _run_phase2_tool_failed(tmp_path)
    assert brief.run_status == "partial"
    for result in (brief.phase_3_result, brief.phase_4_result, brief.phase_5_result):
        assert result is not None and result.status == PhaseStatus.DIAGNOSTIC_ONLY


def test_phase1_failure_still_stops_the_run(tmp_path):
    # No category signal at all -> nothing meaningful to observe downstream.
    sources_by_call = [[], [], [], []]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    assert brief.phase_1_result.status == PhaseStatus.FAIL
    assert brief.phase_2_result is None
    assert brief.phase_3_result is None
    assert brief.decision == Decision.KILL


def test_clean_full_pass_has_no_diagnostic_phases(tmp_path):
    brief, rec = _run_clean_full_pass(tmp_path)
    for result in (brief.phase_1_result, brief.phase_2_result, brief.phase_3_result,
                   brief.phase_4_result, brief.phase_5_result):
        assert result.status in (PhaseStatus.PASS, PhaseStatus.FAIL)
        assert result.diagnostic_reason is None
    cont = json.loads((rec.run_dir / "diagnostic_continuation.json").read_text(encoding="utf-8"))
    assert cont == {}  # artifact exists, empty for a fully gating run


# --------------------------------------------------------------------------- #
# 2. Verdict invariance + fail-closed governance
# --------------------------------------------------------------------------- #
def test_verdict_is_fail_closed_despite_diagnostic_evidence(tmp_path):
    brief, _ = _run_phase2_clean_fail(tmp_path)
    # Phase 2 failure still caps at PARK even though phases 3-5 collected
    # verified prices, competitors, and a structural gap diagnostically.
    assert brief.decision == Decision.PARK
    e1 = brief.e1_review or {}
    assert e1.get("review_verdict") != "E1_APPROVED_TO_RECORD"
    assert e1.get("b3_status") == "LOCKED"
    assert e1.get("recording_status") != "RECORDED"
    assert e1.get("public_execution_status") == "NONE"


def test_diagnostic_evidence_excluded_from_e1_gates(tmp_path):
    brief, rec = _run_phase2_clean_fail(tmp_path)
    gates = {g["gate_id"]: g for g in (brief.e1_review or {}).get("gates", [])}
    # Price/competitor gates must NOT pass on diagnostic-only evidence.
    assert gates["observed_price_band"]["status"] == "FAIL"
    assert gates["competitor_presence"]["status"] == "FAIL"
    assert gates["buyer_language_captured"]["status"] == "FAIL"


def test_diagnostic_evidence_excluded_from_score_and_signals(tmp_path):
    brief_cont, _ = _run_phase2_clean_fail(tmp_path / "cont")
    diag = brief_cont.audit["decision_diagnostics"]
    # Gate-facing accepted-evidence counts exclude diagnostic sources.
    assert diag["accepted_evidence"]["verified_prices"] == 0
    assert diag["accepted_evidence"]["competitors"] == 0


def test_decision_engine_diagnostic_gate_caps_verdict():
    engine = DecisionEngine()
    gates, cap_rank, _, overrides = engine.evaluate_gates({
        "category_signal_count": 5,
        "buyer_language_artifact_count": 0,
        "grade_counts": {"C": 5},
        "run_status": "success",
        "phase2_present": True,
        "phase2_failed": True,
        "diagnostic_trigger_phase": 2,
        "diagnostic_phase_numbers": [3, 4, 5],
    })
    by_name = {g.gate: g for g in gates}
    assert by_name["diagnostic_continuation"].status == "fail"
    assert any("diagnostic_continuation" in o for o in overrides)
    assert cap_rank <= 1  # PARK or lower


def test_diagnostic_claims_cannot_be_supported(tmp_path):
    _, rec = _run_phase2_clean_fail(tmp_path)
    claims = [json.loads(l) for l in
              (rec.run_dir / "claim_ledger.jsonl").read_text(encoding="utf-8").splitlines()
              if l.strip()]
    by_type = {c["claim_type"]: c for c in claims}
    price = by_type["channel_viability"]
    assert price["status"] == "unsupported"
    assert "diagnostic-only" in price["reason"]
    comp = by_type["competitor_density"]
    assert comp["status"] == "unsupported"
    assert comp["supporting_source_ids"] == []
    gap = by_type["mechanism_gap"]
    assert gap["status"] == "unsupported"


# --------------------------------------------------------------------------- #
# 3. Evidence preservation in the NORMAL artifact files
# --------------------------------------------------------------------------- #
def test_diagnostic_sources_preserved_in_source_ledger(tmp_path):
    _, rec = _run_phase2_clean_fail(tmp_path)
    entries = [json.loads(l) for l in
               (rec.run_dir / "source_ledger.jsonl").read_text(encoding="utf-8").splitlines()
               if l.strip()]
    phase3 = [e for e in entries if e["phase_id"] == "phase_3"]
    phase4 = [e for e in entries if e["phase_id"] == "phase_4"]
    assert len(phase3) >= 3 and len(phase4) >= 3
    assert all(e["diagnostic_only"] is True for e in phase3 + phase4)
    # Gating phases stay unmarked.
    phase1 = [e for e in entries if e["phase_id"] == "phase_1"]
    assert phase1 and all(e["diagnostic_only"] is False for e in phase1)


def test_diagnostic_price_bands_written_and_marked(tmp_path):
    _, rec = _run_phase2_clean_fail(tmp_path)
    records = [json.loads(l) for l in
               (rec.run_dir / "price_band_artifacts.jsonl").read_text(encoding="utf-8").splitlines()
               if l.strip()]
    assert len(records) >= 3  # preserved, not discarded
    assert all(r["diagnostic_only"] is True for r in records)
    assert all(isinstance(r["price_observed"], (int, float)) for r in records)


def test_diagnostic_competitor_map_written_and_marked(tmp_path):
    _, rec = _run_phase2_clean_fail(tmp_path)
    records = [json.loads(l) for l in
               (rec.run_dir / "competitor_map.jsonl").read_text(encoding="utf-8").splitlines()
               if l.strip()]
    assert len(records) >= 3
    assert all(r["diagnostic_only"] is True for r in records)


def test_diagnostic_missing_mechanism_written_and_marked(tmp_path):
    _, rec = _run_phase2_clean_fail(tmp_path)
    mech = json.loads((rec.run_dir / "missing_mechanism_gap.json").read_text(encoding="utf-8"))
    assert mech.get("diagnostic_only") is True
    assert mech.get("gap_statement")  # the gap content itself is preserved


def test_diagnostic_continuation_artifact_schema(tmp_path):
    _, rec = _run_phase2_clean_fail(tmp_path)
    cont = json.loads((rec.run_dir / "diagnostic_continuation.json").read_text(encoding="utf-8"))
    assert cont["run_id"] == rec.run_id
    assert cont["timestamp_utc"]
    assert cont["triggered_by_phase"] == 2
    assert cont["triggered_by_phase_name"] == "Buyer Language Mining"
    assert cont["diagnostic_phases"] == [3, 4, 5]
    assert cont["diagnostic_source_counts"]["phase_3"] >= 3
    assert "never" in cont["governance"] or "excluded" in cont["governance"]


def test_continuation_alone_does_not_mark_run_partial(tmp_path):
    # Diagnostic continuation is a deliberate evidence-collection decision, not
    # an observation failure: a clean phase-2 miss stays a clean (non-partial)
    # run even though phases 3-5 ran diagnostically.
    brief, rec = _run_phase2_clean_fail(tmp_path)
    assert brief.run_status == "success"
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text(encoding="utf-8"))
    assert diag["run_partial"] is False
    assert diag["failure_mode"] == "evidence_missing"


# --------------------------------------------------------------------------- #
# 4. Diagnostics / belief / plan / scorecard / markdown surfaces
# --------------------------------------------------------------------------- #
def test_diagnostics_carry_continuation_and_gate_bucket(tmp_path):
    _, rec = _run_phase2_clean_fail(tmp_path)
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text(encoding="utf-8"))
    assert diag["diagnostic_continuation"]["triggered_by_phase"] == 2
    by_phase = {p["phase"]: p for p in diag["phases"]}
    for n in (3, 4, 5):
        assert by_phase[n]["status"] == "DIAGNOSTIC_ONLY"
        assert by_phase[n]["observation_state"] == "diagnostic_only"
    assert "observed_price_band" in diag["gates_evaluated_diagnostically"]
    assert "competitor_presence" in diag["gates_evaluated_diagnostically"]
    # Phase 2 failed cleanly -> its gate stays in the clean bucket.
    assert "buyer_language_captured" in diag["gates_unsupported_after_clean_search"]


def test_belief_state_reports_diagnostic_phases_with_counts(tmp_path):
    _, rec = _run_phase2_clean_fail(tmp_path)
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text(encoding="utf-8"))
    belief = diag["belief_state"]
    pb = belief["price_band_observed"]
    assert pb["status"] == "diagnostic_only"
    assert pb["evidence_count"] >= 3       # observed evidence is acknowledged...
    assert pb["confidence"] is None        # ...but carries no gate confidence
    assert "cannot satisfy this gate" in pb["reason"]


def test_next_evidence_plan_says_collected_diagnostically(tmp_path):
    _, rec = _run_phase2_clean_fail(tmp_path)
    plan = json.loads((rec.run_dir / "next_evidence_plan.json").read_text(encoding="utf-8"))
    targets = {t["gate"]: t for t in plan["next_evidence_targets"]}
    assert targets["observed_price_band"]["evidence_state"] == "collected_diagnostically"
    assert "re-validate" in targets["observed_price_band"]["reason"]
    # The clean phase-2 miss stays a genuine missing-evidence target.
    assert targets["buyer_language_captured"]["evidence_state"] == "missing_after_clean_search"


def test_scorecard_notes_diagnostic_exclusion(tmp_path):
    _, rec = _run_phase2_clean_fail(tmp_path)
    scorecard = json.loads((rec.run_dir / "evidence_scorecard.json").read_text(encoding="utf-8"))
    note = scorecard["diagnostic_continuation_note"]
    assert "diagnostic-only" in note
    assert "excluded" in note


def test_markdown_brief_reports_continuation_honestly(tmp_path):
    _, rec = _run_phase2_clean_fail(tmp_path)
    md = (rec.run_dir / "demand_brief.md").read_text(encoding="utf-8")
    assert "Diagnostic continuation" in md
    assert "DIAGNOSTIC_ONLY" in md
    assert "approval-eligible" in md  # explicitly says it cannot approve
