"""Tests for the decision/search-intelligence patch.

These prove the algorithms got smarter WITHOUT any governance change:
  - E1 gates/thresholds unchanged; B3 LOCKED; E1_RECORDED never produced.
  - Generic satisfaction quotes do not satisfy buyer pain.
  - Query expansion creates multiple evidence-seeking families without renaming
    the product.
  - PARK/REVISE verdicts include a useful next-evidence plan.
  - Missing evidence still parks / fails closed.
  - Better discovery != weaker approval logic.

NOTE: any quote strings below are synthetic parser/classifier fixtures, not real
demand evidence.
"""

import asyncio
import json
import types

from demand_research.models import ProductHypothesis, PhaseStatus, Decision
from demand_research.research.claude_researcher import ClaudeResearcher
from demand_research.research.evidence_validator import EvidenceValidator
from demand_research.research.query_planner import (
    build_query_families,
    build_search_plan,
    prompt_appendix,
)
from demand_research.next_evidence import (
    build_next_evidence_plan,
    needs_next_evidence_plan,
)
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.audit.recorder import RunRecorder
import demand_research.e1_review as e1mod
import demand_research.decision_engine as demod


# --------------------------------------------------------------------------- #
# Shared fakes (mirror the existing workflow test harness)
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
    def __init__(self, sources_by_call, gap_payload):
        self._sources_by_call = list(sources_by_call)
        self._gap_payload = gap_payload
        self._research_idx = 0

    def create(self, **kwargs):
        if kwargs.get("tools"):
            idx = min(self._research_idx, len(self._sources_by_call) - 1)
            self._research_idx += 1
            srcs = self._sources_by_call[idx]
            items = [types.SimpleNamespace(url=s["url"]) for s in srcs]
            return _Response([
                _Block(type="text", text="Found real listings in search."),
                _Block(type="web_search_tool_result", content=items),
            ])
        prompt = kwargs["messages"][0]["content"]
        if '"is_structural"' in prompt:
            payload = self._gap_payload
        else:
            idx = min(self._research_idx - 1, len(self._sources_by_call) - 1)
            payload = {"sources": self._sources_by_call[max(idx, 0)]}
        return _Response([_Block(type="text", text=json.dumps(payload))])


def _fake_researcher(sources_by_call, gap_payload):
    client = types.SimpleNamespace(messages=_FakeMessages(sources_by_call, gap_payload))
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


# --------------------------------------------------------------------------- #
# 1. E1 gates/thresholds unchanged + governance constants
# --------------------------------------------------------------------------- #
def test_e1_thresholds_and_governance_constants_unchanged():
    # Recording-readiness bars are exactly as specified — not relaxed.
    assert e1mod.MIN_OBSERVED_SOURCES == 3
    assert e1mod.MIN_BUYER_LANGUAGE_PHRASES == 5
    assert e1mod.MIN_VERIFIED_PRICES == 3
    assert e1mod.MIN_COMPETITORS == 3
    assert e1mod.MIN_FIT_MARKERS == 2
    # The conservative engine's per-phase floor is unchanged.
    assert demod.DecisionEngine().min_sources_per_phase == 3
    # BUILD still the top of the ladder (disabled/capped elsewhere, not removed).
    assert set(demod.DECISION_THRESHOLDS) == {"BUILD", "TEST", "REVISE", "PARK", "KILL"}


# --------------------------------------------------------------------------- #
# 2 & 3. Query expansion: multiple families, no product rename, channel-aware
# --------------------------------------------------------------------------- #
def test_query_expansion_creates_families_without_renaming_product():
    h = _hypothesis()
    plan = build_search_plan(h)
    # Product name is echoed verbatim, never rewritten.
    assert plan["product_name"] == h.product_name == "Base — Retail Instant-Download OS"

    p2 = build_query_families(h, 2)
    assert len(p2) >= 4, "expected several diversified Phase 2 families"
    all_q = [q for fam in p2 for q in fam.queries]
    assert len(all_q) >= 12
    blob = " ".join(all_q).lower()
    # Natural seller/buyer language, derived from fields (not the product name).
    assert "no sales" in blob
    assert "spent hours" in blob or "wasted time" in blob
    assert "reddit.com" in blob          # site-scoped reddit
    assert "community.etsy.com" in blob  # known channel community
    assert "youtube comments" in blob    # comment-mining surface
    # The product NAME is not forced into the buyer-language queries.
    assert "retail instant-download os" not in blob


def test_prompt_appendix_widens_without_lowering_bar():
    h = _hypothesis()
    appendix = prompt_appendix(h, 2)
    assert "SUGGESTED SEARCH FAMILIES" in appendix
    assert "Never fabricate" in appendix
    # Generic, reusable: a totally different product still yields real families.
    other = ProductHypothesis(
        product_name="DevDeps Sentinel",
        target_buyer="Indie SaaS developer",
        buyer_job="Keep dependencies from breaking builds",
        product_format="CLI tool",
        primary_channel="GitHub",
        missing_mechanism_hypothesis="Blocks merges on risky upgrades",
    )
    fams = build_query_families(other, 2)
    assert fams and all(fam.queries for fam in fams)
    assert "github" in " ".join(q for f in fams for q in f.queries).lower()


# --------------------------------------------------------------------------- #
# 4. Generic satisfaction quotes do not prove target pain
# --------------------------------------------------------------------------- #
def test_generic_satisfaction_quote_classifier():
    v = EvidenceValidator()
    # Praise / satisfaction -> not pain.
    for praise in ("I love it, works perfectly!", "Great product, highly recommend",
                   "Exactly what I needed, 5 stars"):
        assert v.is_generic_satisfaction_quote(praise) is True
        assert v.proves_target_pain(praise) is False
    # Articulated pain / unmet need -> kept (even if politely phrased).
    for pain in (_PAIN, "I wish this tracked fees", "so tired of guessing what to make"):
        assert v.is_generic_satisfaction_quote(pain) is False
        assert v.proves_target_pain(pain) is True


def test_generic_satisfaction_quotes_do_not_satisfy_phase2(tmp_path):
    # Phase 2 returns only glowing satisfaction quotes -> zero pain artifacts.
    praise = "I love it, works perfectly and highly recommend"
    sources_by_call = [
        [_src("signal", i) for i in range(4)],                         # phase 1
        [_src("happy", i, quote=praise) for i in range(5)],            # phase 2 (praise only)
        [_src("price", i, price=True) for i in range(4)],              # phase 3
        [_src("competitor", i) for i in range(4)],                     # phase 4
    ]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))

    assert brief.phase_2_result.status == PhaseStatus.FAIL  # satisfaction != pain
    assert brief.phase_2_result.sources_collected == []
    # Rejected for the right, explicit reason.
    rejected = [json.loads(l) for l in
                (rec.run_dir / "rejected_sources.jsonl").read_text().splitlines() if l.strip()]
    assert any(r["rejection_reason"] == "generic_satisfaction_not_pain" for r in rejected)
    # Fails closed: never approved, B3 LOCKED, never RECORDED.
    assert brief.decision != Decision.BUILD
    e1 = brief.e1_review or {}
    assert e1.get("review_verdict") != "E1_APPROVED_TO_RECORD"
    assert e1.get("b3_status") == "LOCKED"
    assert e1.get("recording_status") != "RECORDED"


# --------------------------------------------------------------------------- #
# 5. PARK/REVISE produce a useful next-evidence plan
# --------------------------------------------------------------------------- #
def test_next_evidence_plan_written_for_parking_run(tmp_path):
    # Phase 1 passes but Phase 2 yields no buyer language -> PARK.
    sources_by_call = [
        [_src("signal", i) for i in range(4)],   # phase 1 pass
        [],                                       # phase 2 -> no buyer language
        [], [],
    ]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))

    verdict = (brief.e1_review or {}).get("review_verdict")
    assert verdict in ("E1_PARK", "E1_REVISE_BEFORE_RECORDING")

    plan = json.loads((rec.run_dir / "next_evidence_plan.json").read_text())
    assert plan["applies"] is True
    assert plan["product_name"] == _hypothesis().product_name  # no rename
    assert plan["primary_blocker"]
    assert plan["failed_gates_in_priority_order"]
    # Has concrete query families + source types + would-not-count guidance.
    assert plan["targeted_search_plan"], "expected a targeted search plan"
    first = plan["targeted_search_plan"][0]
    assert first["search_families"] and first["search_families"][0]["queries"]
    assert first["would_satisfy_source_types"]
    assert first["would_not_count"]
    # Markdown plan was rendered and is non-empty.
    md = (rec.run_dir / "next_evidence_plan.md").read_text()
    assert "Next Evidence Plan" in md and "`" in md


def test_next_evidence_plan_absent_when_no_failure_needed():
    # Helper contract: only PARK/REVISE get a plan.
    assert needs_next_evidence_plan("E1_PARK") is True
    assert needs_next_evidence_plan("E1_REVISE_BEFORE_RECORDING") is True
    assert needs_next_evidence_plan("E1_APPROVED_TO_RECORD") is False
    assert needs_next_evidence_plan("E1_KILL") is False


# --------------------------------------------------------------------------- #
# 6. Diagnostics: missing-evidence vs tooling-failure; per-phase gate mapping
# --------------------------------------------------------------------------- #
def test_decision_diagnostics_distinguish_missing_vs_tooling(tmp_path):
    sources_by_call = [[_src("signal", i) for i in range(4)], [], [], []]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text())
    # Clean run with empty Phase 2 = genuinely missing evidence, not a tool error.
    assert diag["failure_mode"] == "evidence_missing"
    assert diag["phases"] and any("buyer_language_captured" in p["supports_e1_gates"]
                                  for p in diag["phases"] if p["phase"] == 2)


# --------------------------------------------------------------------------- #
# 7 & 8. Missing evidence fails closed; better discovery != easier approval
# --------------------------------------------------------------------------- #
def test_category_only_evidence_still_parks(tmp_path):
    # Strong category/competitor evidence everywhere, but ZERO buyer language and
    # no verified prices -> must not approve. Discovery breadth cannot substitute
    # for the missing buyer-pain / price gates.
    sources_by_call = [
        [_src("signal", i) for i in range(6)],     # phase 1 (category only)
        [_src("listing", i) for i in range(6)],    # phase 2 (no quotes -> fail)
        [_src("lead", i) for i in range(6)],       # phase 3 (no prices)
        [_src("competitor", i) for i in range(6)],
    ]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    e1 = brief.e1_review or {}
    assert e1.get("review_verdict") != "E1_APPROVED_TO_RECORD"
    assert brief.decision != Decision.BUILD
    assert e1.get("b3_status") == "LOCKED"
    assert e1.get("public_execution_status") == "NONE"


def test_full_chain_still_required_for_approval_path(tmp_path):
    # Real pain quotes + verified prices + competitors + structural gap: the
    # repo may reach at most E1_APPROVED_TO_RECORD — never BUILD, never RECORDED.
    sources_by_call = [
        [_src("signal", i) for i in range(5)],
        [_src("pain", i, quote=f"{_PAIN} #{i}") for i in range(6)],
        [_src("price", i, price=True) for i in range(5)],
        [_src("competitor", i) for i in range(5)],
    ]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    e1 = brief.e1_review or {}
    # Approval is the CEILING; recording/B3 remain external/locked regardless.
    assert brief.decision != Decision.BUILD
    assert e1.get("review_verdict") in (
        "E1_APPROVED_TO_RECORD", "E1_REVISE_BEFORE_RECORDING")
    assert e1.get("recording_status") in ("READY_TO_RECORD", "NOT_RECORDED")
    assert e1.get("recording_status") != "RECORDED"
    assert e1.get("b3_status") == "LOCKED"
    assert e1.get("public_execution_status") == "NONE"
