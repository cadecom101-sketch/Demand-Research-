"""Tests for hardened extraction parsing + raw-response audit artifacts.

These cover the extraction-debuggability patch:
  - parse_extraction_json tolerates plain / fenced / prose-wrapped JSON
  - malformed extraction returns None and is logged to extraction_errors.jsonl
  - raw_research_findings_phase_<n>.txt and raw_extraction_response_phase_<n>.txt
    are written for a run
  - E1 still parks / fails closed when extraction parsing fails (no fabrication)

NOTE on fixtures: the JSON snippets below are DUMMY parser-unit inputs. They are
deliberately synthetic strings used only to exercise the parser/audit plumbing;
none of them represents real demand evidence, a real source, or a real URL.
"""

import asyncio
import json
import types

from demand_research.research.claude_researcher import (
    ClaudeResearcher,
    ExtractionResult,
    parse_extraction_json,
)
from demand_research.models import ProductHypothesis, PhaseStatus, Decision
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.audit.recorder import RunRecorder


# --------------------------------------------------------------------------- #
# D. Hardened JSON parsing (pure-unit; no network, no recorder)
# --------------------------------------------------------------------------- #
def test_plain_json_extraction_parses():
    raw = '{"sources": [{"url": "https://example-marketplace.test/x"}]}'
    parsed = parse_extraction_json(raw)
    assert isinstance(parsed, dict)
    assert parsed["sources"][0]["url"] == "https://example-marketplace.test/x"


def test_fenced_json_extraction_parses():
    raw = '```json\n{"sources": [{"price": 19.99}]}\n```'
    parsed = parse_extraction_json(raw)
    assert parsed == {"sources": [{"price": 19.99}]}


def test_bare_fenced_json_extraction_parses():
    raw = '```\n{"ok": true}\n```'
    assert parse_extraction_json(raw) == {"ok": True}


def test_prose_wrapped_json_extraction_parses():
    raw = (
        "Sure! Here is the JSON you asked for:\n\n"
        '{"sources": [{"source_name": "dummy", "url": "https://x.test/1"}]}\n\n'
        "Let me know if you need anything else."
    )
    parsed = parse_extraction_json(raw)
    assert parsed["sources"][0]["source_name"] == "dummy"


def test_prose_wrapped_json_array_parses():
    raw = 'Here you go: [1, 2, {"a": 3}] -- done.'
    assert parse_extraction_json(raw) == [1, 2, {"a": 3}]


def test_largest_balanced_object_is_chosen_over_noise_braces():
    # A tiny noise object precedes the real, larger payload.
    raw = 'prefix {} then {"sources": [{"url": "https://x.test/2"}]} suffix'
    parsed = parse_extraction_json(raw)
    assert isinstance(parsed, dict)
    assert parsed.get("sources")


def test_malformed_extraction_returns_none():
    assert parse_extraction_json("not json at all") is None
    assert parse_extraction_json("") is None
    assert parse_extraction_json("   ") is None
    # Truncated / unbalanced object cannot be recovered -> None (never invented).
    assert parse_extraction_json('{"sources": [{"url": "https://x.test/3"') is None


def test_parse_json_backcompat_wrapper_returns_dict():
    r = ClaudeResearcher(client=types.SimpleNamespace(messages=None))
    assert r._parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert r._parse_json('noise {"a": 2} trailing') == {"a": 2}
    assert r._parse_json("not json at all") == {}


# --------------------------------------------------------------------------- #
# extract() now returns ExtractionResult with raw_text + parse_error
# --------------------------------------------------------------------------- #
class _OneShotMessages:
    """Returns a single scripted extraction response (no tools used)."""

    def __init__(self, text):
        self._text = text

    def create(self, **kwargs):
        block = types.SimpleNamespace(type="text", text=self._text, citations=None)
        return types.SimpleNamespace(content=[block], stop_reason="end_turn")


def test_extract_returns_rawtext_and_no_error_on_good_json():
    researcher = ClaudeResearcher(client=types.SimpleNamespace(
        messages=_OneShotMessages('{"sources": []}')))
    out = researcher.extract("findings", "instruction", system="s")
    assert isinstance(out, ExtractionResult)
    assert out.raw_text == '{"sources": []}'
    assert out.data == {"sources": []}
    assert out.parse_error is None


def test_extract_returns_parse_error_on_bad_json():
    researcher = ClaudeResearcher(client=types.SimpleNamespace(
        messages=_OneShotMessages("this is not json")))
    out = researcher.extract("findings", "instruction", system="s")
    assert out.raw_text == "this is not json"
    assert out.data is None
    assert out.parse_error is not None
    assert out.parse_error["parser_step"] in ("balanced_extraction", "empty_input")


# --------------------------------------------------------------------------- #
# Integration: a fake client whose EXTRACTION output is unparseable prose,
# but whose RESEARCH pass still "finds" listings. Proves fail-closed + audit.
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


class _ResearchOkExtractionBrokenMessages:
    """Research pass returns prose + real-looking citations; extraction pass
    returns UNPARSEABLE prose (the exact bug being patched)."""

    def create(self, **kwargs):
        if kwargs.get("tools"):
            items = [types.SimpleNamespace(url="https://etsy-like.test/listing/1")]
            return _Response([
                _Block(type="text", text="I found several real listings and reviews."),
                _Block(type="web_search_tool_result", content=items),
            ])
        # Extraction pass: model rambles, emits no JSON at all.
        return _Response([_Block(
            type="text",
            text="I think there is demand here but I will not format JSON.",
        )])


def _hypothesis():
    return ProductHypothesis(
        product_name="Base — Retail Instant-Download OS",
        target_buyer="Solo Etsy-first seller of instant-download digital products",
        buyer_job="Move an idea through demand proof to a sales/feedback loop",
        product_format="Notion operating system",
        primary_channel="Etsy-first",
        missing_mechanism_hypothesis="Gates every state transition before and after launch",
    )


def test_raw_artifacts_written_and_extraction_error_logged(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="claude-opus-4-8", base_dir=tmp_path)
    researcher = ClaudeResearcher(
        client=types.SimpleNamespace(messages=_ResearchOkExtractionBrokenMessages()))
    brief = asyncio.run(
        ResearchOrchestrator(researcher=researcher).run_workflow(_hypothesis(), recorder=rec)
    )

    # E. raw_research + raw_extraction files exist for phase 1 (research ran).
    assert (rec.run_dir / "raw_research_findings_phase_1.txt").exists()
    assert (rec.run_dir / "raw_extraction_response_phase_1.txt").exists()

    # A. raw research text is the EXACT model output (not summarised/cleaned).
    research_txt = (rec.run_dir / "raw_research_findings_phase_1.txt").read_text()
    assert research_txt == "I found several real listings and reviews."
    # citation sidecar carries the real-looking URL the search surfaced.
    sidecar = json.loads(
        (rec.run_dir / "raw_research_findings_phase_1.citations.json").read_text())
    assert "https://etsy-like.test/listing/1" in sidecar["citations"]

    # B. raw extraction response is captured verbatim even though parsing failed.
    extraction_txt = (rec.run_dir / "raw_extraction_response_phase_1.txt").read_text()
    assert extraction_txt == "I think there is demand here but I will not format JSON."

    # C. the failed parse is logged to extraction_errors.jsonl with required fields.
    errors = [
        json.loads(line)
        for line in (rec.run_dir / "extraction_errors.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert errors, "expected an extraction error to be logged"
    e = errors[0]
    for field in ("timestamp_utc", "phase", "error_type", "error_message",
                  "parser_step_failed", "raw_preview"):
        assert field in e
    assert e["phase"] == "phase_1"
    assert len(e["raw_preview"]) <= 300
    # No secrets leaked into the ledger.
    blob = json.dumps(errors).lower()
    assert "api_key" not in blob and "sk-ant" not in blob

    # F.7 / fail-closed: no parsed sources -> empty ledgers -> E1 parks, never BUILD.
    assert brief.phase_1_result.sources_collected == []
    assert (rec.run_dir / "source_ledger.jsonl").read_text().strip() == ""
    assert brief.decision != Decision.BUILD
    e1 = brief.e1_review or {}
    assert e1.get("review_verdict") != "E1_APPROVED_TO_RECORD"
    # Governance constraints hold on a parse-failed run.
    assert e1.get("b3_status", "LOCKED") == "LOCKED"
    assert e1.get("recording_status") != "E1_RECORDED"
    assert e1.get("public_execution_status", "NONE") == "NONE"


def test_extraction_error_marks_run_partial_and_parks(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    researcher = ClaudeResearcher(
        client=types.SimpleNamespace(messages=_ResearchOkExtractionBrokenMessages()))
    brief = asyncio.run(
        ResearchOrchestrator(researcher=researcher).run_workflow(_hypothesis(), recorder=rec)
    )
    # A parse failure is a tooling failure, not "no market" -> fail closed at PARK.
    assert brief.phase_1_result.status == PhaseStatus.FAIL
    assert brief.decision in (Decision.PARK, Decision.KILL)
    assert rec.run_status == "partial"
    # manifest reflects the partial, evidence-empty run.
    manifest = json.loads((rec.run_dir / "run_manifest.json").read_text())
    assert manifest["run_status"] == "partial"
    assert manifest["validated_source_count"] == 0
