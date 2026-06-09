"""E1 demand-brief review tests.

These verify the recording-readiness layer on top of the audit + decision
infrastructure: the nine E1 gates, the verdict logic, scope lock for the Base
member (Member A/B excluded), the e1_review_gates.json artifact, the Revenue OS
payload DRAFT markers, and that BUILD is disabled. They reuse the offline fake
client from test_audit and also unit-test evaluate_e1_review directly for gates
that are hard to trigger through the full pipeline.
"""

import asyncio
import json

from demand_research.models import (
    Decision,
    DemandBrief,
    ProductHypothesis,
    PhaseStatus,
    ReviewVerdict,
)
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.audit.recorder import RunRecorder
from demand_research.e1_review import evaluate_e1_review

import test_audit as t  # reuse offline fakes: _researcher, _hypothesis, _src, _GAP


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _e1(rec):
    return json.loads((rec.run_dir / "e1_review_gates.json").read_text())


def _gate(e1, gate_id):
    return next((g for g in e1["gates"] if g["gate_id"] == gate_id), {})


# --------------------------------------------------------------------------- #
# Fixtures (Base member — Governed Solo-Operator Launch OS)
# --------------------------------------------------------------------------- #
def _base_sources(quotes=5, prices=4, competitors=4, signals=4):
    return [
        [t._src("signal", i) for i in range(signals)],
        [t._src("quote", i, quote=True) for i in range(quotes)],
        [t._src("price", i, price=True) for i in range(prices)],
        [t._src("competitor", i) for i in range(competitors)],
    ]


def _generic_hypothesis():
    # Base-ish surface (Notion/Etsy/template) but NO authored-primitive mechanism.
    return ProductHypothesis(
        product_name="Pretty Notion Planner Template",
        target_buyer="Notion users",
        buyer_job="organize their week",
        product_format="Notion template",
        primary_channel="Etsy",
        missing_mechanism_hypothesis="Nicer design with more pages and better colors",
    )


def _generic_src(prefix, i, *, quote=False, price=False):
    return {
        "source_name": f"{prefix} {i}",
        "url": f"https://www.etsy.com/listing/{prefix}-{i}",
        "platform": "Etsy",
        "price": (10.0 + i) if price else None,
        "buyer_language": ("I want a prettier planner template" if quote else None),
        "is_direct_quote": (True if quote else None),
        "what_it_proves": "A generic Notion planner template exists.",
        "what_it_does_not_prove": "Does not prove conversion demand.",
        "gap_note": "Looks nicer with more pages and better colors.",
    }


_GENERIC_GAP = {
    "is_structural": False,
    "gap_statement": "Mine looks better with more pages and nicer colors.",
    "missing_mechanism": "prettier design",
    "reason": "better design and more templates",
}


# --------------------------------------------------------------------------- #
# evaluate_e1_review unit harness (for gates hard to trigger end-to-end)
# --------------------------------------------------------------------------- #
def _unit_hyp():
    return ProductHypothesis(
        product_name="Governed Etsy Instant-Download Launch OS",
        target_buyer="Solo Etsy digital-product seller",
        buyer_job="gate demand before building a digital product",
        product_format="Notion template",
        primary_channel="Etsy",
        missing_mechanism_hypothesis=(
            "Forces demand + fee gates and listing readiness before launch; review loop after."
        ),
    )


def _unit_eval(*, n_sources=20, buyer_phrases=6, prices=4, competitors=4,
               phase1_pass=True, category=4, mm_status="supported", mm_structural=True,
               target_member="Base", cleared_test=True, run_status="success"):
    hyp = _unit_hyp()
    brief = DemandBrief(product_hypothesis=hyp, decision=Decision.TEST,
                        decision_reasoning="x", evidence_quality_score=0.7)
    brief.run_status = run_status
    graded = [{"source_id": f"S{i:03d}", "phase": (i % 4) + 1,
               "grade": "B" if (i % 4) + 1 == 2 else "C"} for i in range(n_sources)]
    signals = {
        "buyer_language_artifact_count": buyer_phrases,
        "phase1_pass": phase1_pass,
        "category_signal_count": category,
    }
    price_bands = [{"source_id": f"P{i}", "price_observed": 10 + i,
                    "url": f"https://www.etsy.com/{i}"} for i in range(prices)]
    comps = [{"source_id": f"C{i}", "competitor_name": f"c{i}",
              "url": f"https://www.etsy.com/c{i}"} for i in range(competitors)]
    mm = {"status": mm_status, "is_structural": mm_structural,
          "gap_statement": "Gates launch before motion"}
    return evaluate_e1_review(
        hypothesis=hyp, brief=brief, signals=signals, graded=graded,
        price_bands=price_bands, directional=[], competitors=comps,
        missing_mechanism=mm, target_member=target_member, decision_cleared_test=cleared_test,
    )


# --------------------------------------------------------------------------- #
# 1 — full passing Base evidence -> E1_APPROVED_TO_RECORD
# --------------------------------------------------------------------------- #
def test_full_base_evidence_approved_to_record(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(_base_sources(), t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec, target_member="Base"))

    e1 = _e1(rec)
    assert e1["candidate_state"] == "E1_CANDIDATE"
    assert e1["review_verdict"] == "E1_APPROVED_TO_RECORD"
    assert e1["recording_status"] == "READY_TO_RECORD"
    assert e1["b2_acceptance_status"] == "READY_FOR_ACCEPTANCE"
    assert e1["b3_status"] == "LOCKED"
    assert e1["public_execution_status"] == "NONE"
    assert e1["current_state"] == "E0_AUTHORED_CAPTURED"
    assert all(g["status"] == "PASS" for g in e1["gates"])
    # No BUILD, and never E1_RECORDED from this repo.
    assert brief.decision != Decision.BUILD
    assert brief.review_verdict == "E1_APPROVED_TO_RECORD"
    assert e1["review_verdict"] != "E1_RECORDED"


# 2 — strong evidence cannot produce BUILD in e1-demand-brief mode
def test_strong_evidence_never_builds(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(_base_sources(), t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    assert brief.decision == Decision.TEST
    assert brief.decision != Decision.BUILD
    manifest = json.loads((rec.run_dir / "run_manifest.json").read_text())
    assert manifest["final_decision"] != "BUILD"
    assert manifest["review_verdict"] == "E1_APPROVED_TO_RECORD"


# 3 — target member A fails scope lock
def test_member_a_fails_scope_lock(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(_base_sources(), t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec, target_member="A"))
    e1 = _e1(rec)
    assert _gate(e1, "scope_lock")["status"] == "FAIL"
    assert e1["review_verdict"] == "E1_REVISE_BEFORE_RECORDING"
    assert e1["recording_status"] == "NOT_RECORDED"
    assert brief.review_verdict == "E1_REVISE_BEFORE_RECORDING"


# 4 — target member B fails scope lock
def test_member_b_fails_scope_lock(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(_base_sources(), t._GAP))
    asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec, target_member="Member B"))
    e1 = _e1(rec)
    assert _gate(e1, "scope_lock")["status"] == "FAIL"
    assert e1["review_verdict"] == "E1_REVISE_BEFORE_RECORDING"


# 5 — generic Notion-template evidence fails fit OR mechanism gap
def test_generic_template_fails_fit_or_mechanism(tmp_path):
    sources = [
        [_generic_src("signal", i) for i in range(4)],
        [_generic_src("quote", i, quote=True) for i in range(5)],
        [_generic_src("price", i, price=True) for i in range(4)],
        [_generic_src("competitor", i) for i in range(4)],
    ]
    rec = RunRecorder(_generic_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(sources, _GENERIC_GAP))
    brief = asyncio.run(orch.run_workflow(_generic_hypothesis(), recorder=rec))
    e1 = _e1(rec)
    fit = _gate(e1, "fit_to_andrew_authored_primitive")["status"]
    mech = _gate(e1, "specific_missing_mechanism_gap")["status"]
    assert fit == "FAIL" or mech == "FAIL"
    assert brief.review_verdict != "E1_APPROVED_TO_RECORD"


# 6 — fewer than 3 sources fails minimum_real_observed_evidence
def test_too_few_sources_fails_minimum(tmp_path):
    sources = [[t._src("signal", i) for i in range(2)]]  # phase 1 fails (need 3)
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(sources, t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    e1 = _e1(rec)
    assert _gate(e1, "minimum_real_observed_evidence")["status"] == "FAIL"
    assert e1["review_verdict"] == "E1_PARK"
    assert brief.review_verdict == "E1_PARK"


# 7 — fewer than 5 buyer-language phrases fails buyer_language_captured
def test_fewer_than_five_buyer_phrases_fails(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(_base_sources(quotes=4), t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    e1 = _e1(rec)
    assert _gate(e1, "buyer_language_captured")["status"] == "FAIL"
    assert e1["review_verdict"] == "E1_REVISE_BEFORE_RECORDING"
    assert brief.decision != Decision.BUILD


# 8 — three verified prices pass observed_price_band
def test_three_verified_prices_pass_price_band(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(_base_sources(prices=3), t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    e1 = _e1(rec)
    assert brief.phase_3_result.status == PhaseStatus.PASS
    assert len(_read_jsonl(rec.run_dir / "price_band_artifacts.jsonl")) >= 3
    assert _gate(e1, "observed_price_band")["status"] == "PASS"


# 9 — eleven leads, zero verified prices fail observed_price_band (price-band fix preserved)
def test_eleven_leads_fail_price_band(tmp_path):
    sources = [
        [t._src("signal", i) for i in range(4)],
        [t._src("quote", i, quote=True) for i in range(5)],
        [t._src("lead", i) for i in range(11)],  # no price -> leads, phase 3 FAIL
    ]
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(sources, t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    e1 = _e1(rec)
    assert brief.phase_3_result.status == PhaseStatus.FAIL
    assert _read_jsonl(rec.run_dir / "price_band_artifacts.jsonl") == []
    assert _gate(e1, "observed_price_band")["status"] == "FAIL"


# 10 — fewer than 3 competitors fails competitor_presence (unit-level)
def test_too_few_competitors_fails_gate():
    result = _unit_eval(competitors=2)
    assert result.gate_passed("competitor_presence") is False
    assert result.review_verdict == ReviewVerdict.E1_REVISE_BEFORE_RECORDING.value
    # And the inverse: 3 competitors pass.
    assert _unit_eval(competitors=3).gate_passed("competitor_presence") is True


# 11 — cosmetic / reskin gap fails specific_missing_mechanism_gap
def test_cosmetic_gap_fails_mechanism(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(_base_sources(), _GENERIC_GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    e1 = _e1(rec)
    assert _gate(e1, "specific_missing_mechanism_gap")["status"] == "FAIL"
    assert brief.review_verdict != "E1_APPROVED_TO_RECORD"
    # Unit inverse: a structural gap passes.
    assert _unit_eval(mm_status="unsupported", mm_structural=False).gate_passed(
        "specific_missing_mechanism_gap") is False


# 12 — Revenue OS payload is drafted, never recorded
def test_revenue_os_payload_is_draft_only(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(_base_sources(), t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    payload = brief.e1_review["revenue_os_payload_draft"]
    for marker in ("DRAFT ONLY", "NOT RECORDED",
                   "DO NOT WRITE TO REVENUE OS FROM THIS REPO", "HUMAN REVIEW REQUIRED"):
        assert marker in payload["_marking"]
    assert payload["authorship_primitives"]["name"] == "Governed Solo-Operator Launch OS"
    assert payload["authorship_primitives"]["recording_status"] == "DRAFT_ONLY_NOT_RECORDED"
    assert payload["demand_briefs"]["member_name"] == "Base — Retail Instant-Download OS"
    assert payload["demand_briefs"]["recording_status"] == "DRAFT_ONLY_NOT_RECORDED"
    # Markdown surfaces the do-not-write warning.
    md = (rec.run_dir / "demand_brief.md").read_text()
    assert "DO NOT WRITE TO REVENUE OS FROM THIS REPO" in md
    assert "## Revenue OS Recording Payload Draft" in md


# 13 — e1_review_gates.json always valid JSON, written for pass AND fail runs
def test_e1_review_gates_written_pass_and_fail(tmp_path):
    # Pass (approved) run.
    rec_pass = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path / "pass")
    orch_pass = ResearchOrchestrator(researcher=t._researcher(_base_sources(), t._GAP))
    asyncio.run(orch_pass.run_workflow(t._hypothesis(), recorder=rec_pass))
    e1_pass = json.loads((rec_pass.run_dir / "e1_review_gates.json").read_text())
    assert len(e1_pass["gates"]) == 9
    assert e1_pass["review_verdict"] == "E1_APPROVED_TO_RECORD"

    # Fail (parked) run — phase 1 collapses.
    rec_fail = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path / "fail")
    orch_fail = ResearchOrchestrator(researcher=t._researcher([[t._src("signal", 0)]], t._GAP))
    asyncio.run(orch_fail.run_workflow(t._hypothesis(), recorder=rec_fail))
    e1_fail = json.loads((rec_fail.run_dir / "e1_review_gates.json").read_text())
    assert isinstance(e1_fail, dict)
    assert len(e1_fail["gates"]) == 9
    assert e1_fail["review_verdict"] in {"E1_PARK", "E1_REVISE_BEFORE_RECORDING", "E1_KILL"}


# 14 — markdown surfaces the full hierarchy + state block
def test_markdown_has_hierarchy_and_states(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(_base_sources(), t._GAP))
    asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    md = (rec.run_dir / "demand_brief.md").read_text()
    for needle in (
        "Primitive: Governed Solo-Operator Launch OS",
        "Target Member: Base — Retail Instant-Download OS",
        "Excluded Members:",
        "Current State: E0_AUTHORED_CAPTURED",
        "Candidate State: E1_CANDIDATE",
        "Review Verdict:",
        "Recording Status:",
        "B2 Acceptance:",
        "B3 Status: LOCKED",
        "Public Execution Status: NONE",
    ):
        assert needle in md, f"missing from markdown: {needle!r}"
    # Member A/B are named as excluded and never approved.
    assert "Member A" in md and "Member B" in md
    assert "## Fit to Andrew's Authored Primitive" in md


# 15 — existing audit files remain valid JSON / JSONL
def test_existing_audit_files_remain_valid(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(_base_sources(), t._GAP))
    asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    for name in ("run_manifest.json", "evidence_scorecard.json", "demand_brief.json",
                 "missing_mechanism_gap.json", "e1_review_gates.json"):
        assert isinstance(json.loads((rec.run_dir / name).read_text()), dict)
    for name in ("source_ledger.jsonl", "claim_ledger.jsonl",
                 "buyer_language_artifacts.jsonl", "competitor_map.jsonl"):
        _read_jsonl(rec.run_dir / name)  # raises if any line is invalid JSON


# 16 — no-fabrication preserved: placeholder URLs never reach the accepted set
def test_no_fabrication_placeholder_rejected(tmp_path):
    bad = {
        "source_name": "Fake", "url": "https://example.com/fake", "platform": "Etsy",
        "price": None, "buyer_language": None, "is_direct_quote": None,
        "what_it_proves": "x", "what_it_does_not_prove": "y", "gap_note": None,
    }
    sources = [
        [t._src("signal", i) for i in range(4)] + [bad],
        [t._src("quote", i, quote=True) for i in range(5)],
        [t._src("price", i, price=True) for i in range(4)],
        [t._src("competitor", i) for i in range(4)],
    ]
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(sources, t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    # The placeholder is rejected upstream, so no accepted source uses example.com,
    # and the no_fabrication gate still passes on the clean accepted set.
    assert all("example.com" not in str(s.url) for s in brief.all_sources())
    rejected = _read_jsonl(rec.run_dir / "rejected_sources.jsonl")
    assert any("example.com" in r["url"] for r in rejected)
    e1 = _e1(rec)
    assert _gate(e1, "no_fabrication")["status"] == "PASS"


# Boundary claims (6–9) are written and clearly marked.
def test_claim_ledger_has_e1_boundary_claims(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(_base_sources(), t._GAP))
    asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    claims = _read_jsonl(rec.run_dir / "claim_ledger.jsonl")
    types = {c["claim_type"] for c in claims}
    assert "fit_to_primitive" in types
    assert "scope_boundary" in types
    assert "workflow_boundary" in types
    boundary = [c for c in claims if c["claim_type"] == "workflow_boundary"]
    assert any("recorded into Revenue OS" in c["claim"] for c in boundary)
    assert any("published" in c["claim"] for c in boundary)
