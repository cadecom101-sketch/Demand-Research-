"""End-to-end workflow tests using a fake Anthropic client.

These exercise the full research -> extract -> validate -> decide -> render
pipeline without any network access, by injecting a fake client into
ClaudeResearcher. This proves the integration logic (loop handling, JSON
parsing, source validation, decision routing, output rendering) is correct.
"""

import asyncio
import json
import types

import pytest

from demand_research.models import ProductHypothesis, PhaseStatus, Decision
from demand_research.research.claude_researcher import (
    ClaudeResearcher,
    ResearchUnavailableError,
)
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.outputs.markdown_generator import MarkdownGenerator


# --------------------------------------------------------------------------- #
# Fakes
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
    """Returns scripted responses; research calls (with tools) yield web
    findings + citations, extraction calls (no tools) yield JSON."""

    def __init__(self, sources_by_call, gap_payload):
        self._sources_by_call = list(sources_by_call)
        self._gap_payload = gap_payload
        self._research_idx = 0

    def create(self, **kwargs):
        if kwargs.get("tools"):
            # Research pass — return prose + a web_search_tool_result block.
            idx = min(self._research_idx, len(self._sources_by_call) - 1)
            self._research_idx += 1
            srcs = self._sources_by_call[idx]
            result_items = [types.SimpleNamespace(url=s["url"]) for s in srcs]
            return _Response([
                _Block(type="text", text="Found real listings in search."),
                _Block(type="web_search_tool_result", content=result_items),
            ])
        # Extraction pass — decide which payload by the prompt content.
        prompt = kwargs["messages"][0]["content"]
        if '"is_structural"' in prompt:
            payload = self._gap_payload
        else:
            # Echo back whichever source set the most recent research produced.
            idx = min(self._research_idx - 1, len(self._sources_by_call) - 1)
            payload = {"sources": self._sources_by_call[max(idx, 0)]}
        return _Response([_Block(type="text", text=json.dumps(payload))])


def _fake_researcher(sources_by_call, gap_payload):
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


def _good_sources(prefix, n=3, with_price=False, with_quote=False):
    out = []
    for i in range(n):
        s = {
            "source_name": f"{prefix} {i}",
            "url": f"https://www.etsy.com/listing/{prefix}-{i}",
            "platform": "Etsy",
            "price": (10.0 + i) if with_price else None,
            "buyer_language": ("I wish this tracked fees" if with_quote else None),
            "is_direct_quote": (True if with_quote else None),
            "what_it_proves": "A comparable product exists.",
            "what_it_does_not_prove": "Does not prove conversion demand.",
            "gap_note": "Tracks listings but does not gate launch readiness.",
        }
        out.append(s)
    return out


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_full_workflow_builds_when_evidence_strong(tmp_path):
    # Plenty of priced, quoted sources across phases -> all phases pass.
    sources_by_call = [
        _good_sources("signal", 5),                       # phase 1
        _good_sources("quote", 5, with_quote=True),       # phase 2
        _good_sources("price", 5, with_price=True),       # phase 3
        _good_sources("competitor", 5),                   # phase 4
    ]
    gap = {"is_structural": True, "gap_statement": "Gates launch before motion.",
           "reason": "Competitors only track after the fact."}
    researcher = _fake_researcher(sources_by_call, gap)

    brief = asyncio.run(ResearchOrchestrator(researcher=researcher).run_workflow(_hypothesis()))

    assert brief.phase_1_result.status == PhaseStatus.PASS
    assert brief.phase_2_result.status == PhaseStatus.PASS
    assert brief.phase_3_result.status == PhaseStatus.PASS
    assert brief.phase_4_result.status == PhaseStatus.PASS
    assert brief.phase_5_result.status == PhaseStatus.PASS
    # BUILD is disabled in the E1 demand-brief workflow; strong evidence caps to TEST.
    assert brief.decision in (Decision.TEST, Decision.REVISE)
    assert brief.decision != Decision.BUILD
    # Every source carries a real-looking URL and a date.
    for s in brief.all_sources():
        assert str(s.url).startswith("https://")
        assert s.date_observed is not None


def test_phase1_failure_routes_to_kill():
    # Research returns no sources at all -> phase 1 FAIL -> KILL.
    researcher = _fake_researcher([[]], {"is_structural": False, "gap_statement": "", "reason": ""})
    brief = asyncio.run(ResearchOrchestrator(researcher=researcher).run_workflow(_hypothesis()))
    assert brief.phase_1_result.status == PhaseStatus.FAIL
    assert brief.decision == Decision.KILL


def test_fabricated_placeholder_urls_are_dropped():
    bad = [{
        "source_name": "Fake",
        "url": "https://example.com/fake",   # placeholder domain -> rejected
        "platform": "Etsy",
        "price": None, "buyer_language": None, "is_direct_quote": None,
        "what_it_proves": "x", "what_it_does_not_prove": "y", "gap_note": None,
    }]
    researcher = _fake_researcher([bad], {"is_structural": False, "gap_statement": "", "reason": ""})
    brief = asyncio.run(ResearchOrchestrator(researcher=researcher).run_workflow(_hypothesis()))
    # The placeholder source is filtered, so phase 1 has zero valid sources.
    assert brief.phase_1_result.sources_collected == []
    assert brief.decision == Decision.KILL


def test_markdown_output_renders(tmp_path):
    sources_by_call = [_good_sources("signal", 4)] * 4
    gap = {"is_structural": True, "gap_statement": "Gates before launch.", "reason": "ok"}
    researcher = _fake_researcher(sources_by_call, gap)
    brief = asyncio.run(ResearchOrchestrator(researcher=researcher).run_workflow(_hypothesis()))

    gen = MarkdownGenerator()
    path = gen.generate(brief)
    text = path.read_text(encoding="utf-8")
    assert "Demand Brief" in text
    assert brief.decision.value in text
    path.unlink()  # clean up generated artifact


def test_missing_credentials_raise_research_unavailable(monkeypatch):
    # No client and no API key -> the lazy client construction must raise a
    # clear ResearchUnavailableError, never a fabricated verdict.
    # Isolate this test from a developer shell that may have a live API key set.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    researcher = ClaudeResearcher(client=None)
    with pytest.raises(ResearchUnavailableError):
        researcher.research("hi", system="s")


def test_json_parsing_is_defensive():
    r = ClaudeResearcher(client=types.SimpleNamespace(messages=None))
    assert r._parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert r._parse_json('noise {"a": 2} trailing') == {"a": 2}
    assert r._parse_json("not json at all") == {}
