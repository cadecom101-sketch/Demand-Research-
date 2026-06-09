"""E0 -> E1 workflow integration tests.

These verify the workflow layer (phase names, evidence stage, structured phase
artifacts, acceptance gate) on top of the audit layer. They reuse the fake
Anthropic client helpers from test_audit so they run fully offline.
"""

import asyncio
import json

from demand_research.models import (
    Decision,
    EvidenceStage,
    ProductHypothesis,
    PhaseStatus,
    ReviewVerdict,
    evidence_stage_for,
)
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.agents.phase_agents import (
    Phase1Agent, Phase2Agent, Phase3Agent, Phase4Agent, Phase5Agent,
)
from demand_research.audit.recorder import RunRecorder

import test_audit as t  # reuse fakes: _researcher, _hypothesis, _strong_sources, _grade_c_run, _GAP, _src


def _read_jsonl(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# 1 — fixed phase names, including "Price Band Mapping"
def test_fixed_phase_names():
    assert Phase1Agent().phase_name == "Signal Discovery"
    assert Phase2Agent().phase_name == "Buyer Language Mining"
    assert Phase3Agent().phase_name == "Price Band Mapping"
    assert Phase4Agent().phase_name == "Competitor Presence"
    assert Phase5Agent().phase_name == "Missing-Mechanism Gap"


# 2 — DemandBrief carries evidence_stage
def test_brief_has_evidence_stage(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(t._strong_sources(), t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    assert isinstance(brief.evidence_stage, EvidenceStage)
    assert json.loads((rec.run_dir / "demand_brief.json").read_text())["evidence_stage"] in {
        "E0_AUTHORED_CAPTURED", "E1_CANDIDATE", "E1_APPROVED_TO_RECORD", "E1_RECORDED"
    }


# 3 — review verdict -> evidence stage mapping (only approval advances the ladder)
def test_verdict_to_stage_mapping():
    assert evidence_stage_for(ReviewVerdict.E1_KILL) == EvidenceStage.E0_AUTHORED_CAPTURED
    assert evidence_stage_for(ReviewVerdict.E1_PARK) == EvidenceStage.E0_AUTHORED_CAPTURED
    assert evidence_stage_for(
        ReviewVerdict.E1_REVISE_BEFORE_RECORDING) == EvidenceStage.E0_AUTHORED_CAPTURED
    assert evidence_stage_for(
        ReviewVerdict.E1_APPROVED_TO_RECORD) == EvidenceStage.E1_APPROVED_TO_RECORD


# 4/5/6 — structured phase artifacts are created
def test_structured_phase_artifacts_created(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(t._strong_sources(), t._GAP))
    asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    price = _read_jsonl(rec.run_dir / "price_band_artifacts.jsonl")
    comp = _read_jsonl(rec.run_dir / "competitor_map.jsonl")
    assert len(price) >= 3 and all("price_observed" in r and "source_id" in r for r in price)
    assert len(comp) >= 3 and all("demand_validation_score" in r for r in comp)
    mech = json.loads((rec.run_dir / "missing_mechanism_gap.json").read_text())
    assert mech["status"] in {"supported", "partially_supported", "unsupported"}
    assert mech["supporting_source_ids"]  # linked to Phase 4 competitor sources


# 7/8/9 — markdown includes the new sections
def test_markdown_has_workflow_sections(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(t._strong_sources(), t._GAP))
    asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    md = (rec.run_dir / "demand_brief.md").read_text()
    assert "## Primitive / Member Hierarchy" in md
    assert "## Current State / Candidate State" in md
    assert "## E1 Review Verdict" in md
    assert "## E1 Review Gate Results" in md
    assert "## Price Band Mapping" in md
    assert "## Competitor Presence Map" in md
    assert "## Missing-Mechanism Gap" in md


# 10 — E1_CANDIDATE impossible when buyer language missing and no Grade A
def test_e1_candidate_impossible_without_buyer_language(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(t._grade_c_run(), t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    assert brief.evidence_stage == EvidenceStage.E0_AUTHORED_CAPTURED
    assert brief.decision not in (Decision.TEST, Decision.BUILD)


# 11 — BUILD impossible from Grade-C-only evidence
def test_build_impossible_grade_c_only(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(t._grade_c_run(), t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    assert brief.decision != Decision.BUILD


# 12 — source cards carry "what this source does NOT prove"
def test_source_cards_have_does_not_prove(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(t._strong_sources(), t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    sources = brief.all_sources()
    assert sources
    assert all(s.what_this_does_not_prove for s in sources)
    # And the ledger maps the field.
    ledger = _read_jsonl(rec.run_dir / "source_ledger.jsonl")
    assert all("claim_not_supported" in e for e in ledger)


# 13 — aesthetic-only gaps are marked unsupported, not BUILD
def test_aesthetic_gap_is_unsupported(tmp_path):
    aesthetic_gap = {
        "is_structural": True,  # the model may claim structural, but markers say otherwise
        "gap_statement": "Mine looks better and is cleaner with more pages.",
        "missing_mechanism": "nicer design",
        "reason": "better design and cheaper",
    }
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(t._strong_sources(), aesthetic_gap))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    mech = json.loads((rec.run_dir / "missing_mechanism_gap.json").read_text())
    assert mech["status"] == "unsupported"
    assert mech["is_structural"] is False
    assert brief.phase_5_result.status == PhaseStatus.FAIL
    assert brief.decision != Decision.BUILD


# 14 — JSON artifacts parse cleanly (incl. new missing_mechanism_gap.json)
def test_json_artifacts_parse_cleanly(tmp_path):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(t._strong_sources(), t._GAP))
    asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    for name in ("run_manifest.json", "evidence_scorecard.json",
                 "demand_brief.json", "missing_mechanism_gap.json",
                 "e1_review_gates.json"):
        with (rec.run_dir / name).open() as f:
            assert isinstance(json.load(f), dict)
