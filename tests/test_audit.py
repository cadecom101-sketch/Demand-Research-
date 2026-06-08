"""Audit / truth-layer tests.

These verify the run folder is actually written to disk (not mocked away): the
manifest, search log, rejected log, source ledger, buyer-language artifacts,
claim ledger, and scorecard. They also verify the hard gates and the brief's
audit sections.
"""

import asyncio
import json
import types

from demand_research.models import ProductHypothesis, Decision, PhaseStatus
from demand_research.research.claude_researcher import ClaudeResearcher
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.audit.recorder import RunRecorder, ARTIFACT_FILES


# --------------------------------------------------------------------------- #
# Fakes that emit real-shaped web_search blocks (server_tool_use + results)
# --------------------------------------------------------------------------- #
class _Blk:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.__dict__.setdefault("type", "text")
        self.__dict__.setdefault("citations", None)


class _Resp:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class _Item:
    def __init__(self, url):
        self.url = url


class _FakeMessages:
    def __init__(self, sources_by_call, gap_payload):
        self._sources_by_call = list(sources_by_call)
        self._gap_payload = gap_payload
        self._idx = 0

    def create(self, **kw):
        if kw.get("tools"):
            idx = min(self._idx, len(self._sources_by_call) - 1)
            self._idx += 1
            srcs = self._sources_by_call[idx]
            return _Resp([
                # A real search that found results...
                _Blk(type="server_tool_use", name="web_search",
                     input={"query": f'site:reddit.com "spent hours" call {idx}'}),
                _Blk(type="web_search_tool_result", content=[_Item(s["url"]) for s in srcs]),
                # ...and a second search that returned nothing (must still be logged).
                _Blk(type="server_tool_use", name="web_search",
                     input={"query": f'"no buyers" empty query {idx}'}),
                _Blk(type="web_search_tool_result", content=[]),
                _Blk(type="text", text="Found real listings and complaints in search."),
            ])
        prompt = kw["messages"][0]["content"]
        if '"is_structural"' in prompt:
            return _Resp([_Blk(text=json.dumps(self._gap_payload))])
        idx = min(self._idx - 1, len(self._sources_by_call) - 1)
        return _Resp([_Blk(text=json.dumps({"sources": self._sources_by_call[max(idx, 0)]}))])


def _researcher(sources_by_call, gap_payload):
    client = types.SimpleNamespace(messages=_FakeMessages(sources_by_call, gap_payload))
    return ClaudeResearcher(client=client)


def _hypothesis():
    return ProductHypothesis(
        product_name="Governed Etsy Launch OS",
        target_buyer="Solo Etsy digital-product seller",
        buyer_job="Decide which product ideas deserve build time",
        product_format="Notion template",
        primary_channel="Etsy",
        missing_mechanism_hypothesis="Forces demand+fee gates before launch",
    )


def _src(prefix, i, *, price=False, quote=False):
    return {
        "source_name": f"{prefix} {i}",
        "url": f"https://www.etsy.com/listing/{prefix}-{i}",
        "platform": "Etsy",
        "price": (12.0 + i) if price else None,
        "buyer_language": ("I wasted hours building things no one buys" if quote else None),
        "is_direct_quote": (True if quote else None),
        "what_it_proves": "A comparable product / real complaint exists.",
        "what_it_does_not_prove": "Does not prove conversion demand.",
        "gap_note": "Tracks listings but does not gate launch readiness.",
    }


def _strong_sources():
    # Phase 1 includes one fabricated placeholder source that must be rejected.
    bad = {
        "source_name": "Fake", "url": "https://example.com/fake", "platform": "Etsy",
        "price": None, "buyer_language": None, "is_direct_quote": None,
        "what_it_proves": "x", "what_it_does_not_prove": "y", "gap_note": None,
    }
    return [
        [_src("signal", i) for i in range(4)] + [bad],          # phase 1
        [_src("quote", i, quote=True) for i in range(4)],        # phase 2
        [_src("price", i, price=True) for i in range(4)],        # phase 3
        [_src("competitor", i) for i in range(4)],               # phase 4
    ]


_GAP = {"is_structural": True, "gap_statement": "Gates launch before motion.",
        "reason": "Competitors only track after the fact."}


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_run_folder_and_all_artifacts_exist(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="claude-opus-4-8", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(_strong_sources(), _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))

    assert rec.run_dir.exists()
    for name in ARTIFACT_FILES:
        assert (rec.run_dir / name).exists(), f"missing artifact: {name}"
    # Manifest + scorecard are valid JSON.
    json.loads((rec.run_dir / "run_manifest.json").read_text())
    json.loads((rec.run_dir / "evidence_scorecard.json").read_text())
    assert brief.run_id == rec.run_id


def test_brief_includes_audit_sections(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(_strong_sources(), _GAP))
    asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    md = (rec.run_dir / "demand_brief.md").read_text()
    assert "Evidence Quality Score Breakdown" in md
    assert "Hard Gate Results" in md
    assert "Audit Artifacts" in md
    assert "Claim Ledger Summary" in md


def test_zero_result_searches_are_logged(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(_strong_sources(), _GAP))
    asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    searches = _read_jsonl(rec.run_dir / "search_log.jsonl")
    assert searches, "expected search attempts to be logged"
    assert any(s["status"] == "zero_results" for s in searches)
    assert any(s["status"] == "results_found" for s in searches)


def test_rejected_sources_are_written(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(_strong_sources(), _GAP))
    asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    rejected = _read_jsonl(rec.run_dir / "rejected_sources.jsonl")
    assert rejected, "the placeholder source should have been rejected and logged"
    assert any("example.com" in r["url"] for r in rejected)


def test_source_ledger_grades_buyer_language_as_B(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(_strong_sources(), _GAP))
    asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    ledger = _read_jsonl(rec.run_dir / "source_ledger.jsonl")
    assert ledger
    b_grade = [e for e in ledger if e["evidence_grade"] == "B"]
    assert b_grade, "verbatim buyer-language sources should be Grade B"
    artifacts = _read_jsonl(rec.run_dir / "buyer_language_artifacts.jsonl")
    assert len(artifacts) >= 3


def test_zero_buyer_language_forbids_build(tmp_path):
    # No quotes anywhere -> Phase 2 fails -> cannot be BUILD.
    sources = [[_src("signal", i) for i in range(4)] for _ in range(4)]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(sources, _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    assert brief.decision != Decision.BUILD
    assert brief.phase_2_result.status == PhaseStatus.FAIL


def test_unsupported_claim_without_source_ids(tmp_path):
    # Phase 2 fails -> buyer_pain claim must NOT be 'supported'.
    sources = [[_src("signal", i) for i in range(4)] for _ in range(4)]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(sources, _GAP))
    asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    claims = _read_jsonl(rec.run_dir / "claim_ledger.jsonl")
    pain = [c for c in claims if c["claim_type"] == "buyer_pain"]
    assert pain and pain[0]["status"] != "supported"
    assert pain[0]["supporting_source_ids"] == []


def test_partial_run_forbids_build_and_marks_status(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    rec.mark_partial("simulated tool failure during research")
    orch = ResearchOrchestrator(researcher=_researcher(_strong_sources(), _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    assert brief.run_status == "partial"
    assert brief.decision != Decision.BUILD
    manifest = json.loads((rec.run_dir / "run_manifest.json").read_text())
    assert manifest["run_status"] == "partial"


def _grade_c_run():
    # Phase 1 passes (Grade-C signals); Phase 2 returns generic content with no
    # verbatim quotes -> zero buyer-language artifacts -> Phase 2 fails.
    return [
        [_src("signal", i) for i in range(4)],    # phase 1 (valid Grade-C)
        [_src("content", i) for i in range(4)],   # phase 2 (no quotes)
    ]


def test_search_log_deduplicates_provider_and_model_reported(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    attempts = [
        {"query": "Spent Hours  no sales", "status": "results_found", "result_count": 2,
         "result_urls": ["u1", "u2"], "search_provider": "anthropic_web_search"},
        # Same normalized query, model-reported -> must be dropped in favor of provider.
        {"query": "spent hours no sales", "status": "results_found", "result_count": 2,
         "result_urls": ["u1", "u2"], "search_provider": "model_reported_web_search"},
        {"query": "different query", "status": "zero_results", "result_count": 0,
         "result_urls": [], "search_provider": "anthropic_web_search"},
    ]
    rec.log_searches("phase_2", "Buyer Language Mining", attempts)
    records = _read_jsonl(rec.run_dir / "search_log.jsonl")
    assert len(records) == 2, "duplicate query should be written once"
    spent = [r for r in records if r["search_query"].lower().strip() == "spent hours  no sales".strip()
             or r["search_query"].lower().startswith("spent hours")]
    assert spent and spent[0]["search_provider"] == "anthropic_web_search"
    # Re-logging the same query in the same phase does not write it again.
    rec.log_searches("phase_2", "Buyer Language Mining", attempts)
    assert len(_read_jsonl(rec.run_dir / "search_log.jsonl")) == 2


def test_phase2_rejects_candidates_when_no_artifacts(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(_grade_c_run(), _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    assert brief.phase_2_result.status == PhaseStatus.FAIL
    rejected = _read_jsonl(rec.run_dir / "rejected_sources.jsonl")
    phase2_rej = [r for r in rejected if r["phase_id"] == "phase_2"]
    assert len(phase2_rej) >= 3, "non-qualifying Phase 2 candidates must be logged as rejected"
    assert all(r["rejection_reason"] in {
        "no_direct_buyer_language", "missing_required_quote", "not_seller_authored",
        "generic_content", "wrong_artifact_type", "grade_c_not_allowed_for_phase_2",
        "inaccessible", "duplicate", "other",
    } for r in phase2_rej)
    # buyer_language_artifacts file exists but is empty.
    assert _read_jsonl(rec.run_dir / "buyer_language_artifacts.jsonl") == []


def test_grade_c_only_zero_buyer_language_is_park(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(_grade_c_run(), _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    assert brief.decision == Decision.PARK


def test_hard_gate_overrides_explain_park(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(_grade_c_run(), _GAP))
    asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    sc = json.loads((rec.run_dir / "evidence_scorecard.json").read_text())
    overrides = sc["hard_gate_overrides"]
    # The buyer-language / grade / phase-2 gates must explain PARK (not REVISE).
    assert overrides, "PARK case must record explanatory gate overrides"
    assert "buyer_language_missing -> PARK" in overrides
    assert "phase_2_failed -> PARK" in overrides
    assert "no_grade_a_or_b_evidence -> PARK" in overrides
    assert "grade_c_only_ceiling -> PARK" in overrides
    # None of those four conservative gates may be mislabeled as REVISE.
    for g in ("buyer_language_missing", "no_grade_a_or_b_evidence",
              "grade_c_only_ceiling", "phase_2_failed"):
        assert f"{g} -> REVISE" not in overrides


def test_json_artifacts_parse_cleanly(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(_strong_sources(), _GAP))
    asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    for name in ("run_manifest.json", "evidence_scorecard.json", "demand_brief.json"):
        with (rec.run_dir / name).open() as f:
            payload = json.load(f)  # raises if invalid / truncated
        assert isinstance(payload, dict)
    # No leftover temp files from the atomic writes.
    assert not list(rec.run_dir.glob("*.tmp"))


def test_strong_evidence_can_build(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_researcher(_strong_sources(), _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    # All gates clear, strong score -> BUILD.
    assert brief.decision == Decision.BUILD
    manifest = json.loads((rec.run_dir / "run_manifest.json").read_text())
    assert manifest["final_decision"] == "BUILD"
    assert manifest["validated_source_count"] >= 12
    assert manifest["rejected_source_count"] >= 1
