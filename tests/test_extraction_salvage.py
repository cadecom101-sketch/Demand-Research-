"""Tests for source-object salvage + chunked extraction.

These reproduce the observed live failure (a truncated final source object in an
otherwise-valid `"sources": [...]` array) and prove the repo now keeps the valid
earlier objects instead of losing all evidence — while still discarding the
broken tail, never repairing it, and keeping every E1 fail-closed constraint.

NOTE: JSON snippets are synthetic parser fixtures; they are not real evidence.
"""

import asyncio
import json
import types

from demand_research.research.claude_researcher import (
    ClaudeResearcher,
    chunk_text,
    dedupe_raw_sources,
    parse_extraction_json,
    salvage_source_objects,
)
from demand_research.research import claude_researcher as cr
from demand_research.agents import phase_agents as pa
from demand_research.models import ProductHypothesis, PhaseStatus, Decision
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.audit.recorder import RunRecorder


# --------------------------------------------------------------------------- #
# Fixtures mirroring the observed malformed snippet pattern
# --------------------------------------------------------------------------- #
def _obj(name, url, **extra):
    base = {
        "source_name": name, "url": url,
        "what_it_proves": "x", "what_it_does_not_prove": "y",
    }
    base.update(extra)
    return json.dumps(base)


# Two complete objects, then a third whose final string is cut off (token limit).
_TRUNCATED_TAIL = (
    '{\n  "sources": [\n'
    f'    {_obj("Etsy listing A", "https://www.etsy.com/listing/a", price=12.0)},\n'
    f'    {_obj("Etsy listing B", "https://www.etsy.com/listing/b", price=18.0)},\n'
    '    {\n'
    '      "source_name": "Notion Marketplace: Ideas validation Template",\n'
    '      "url": "https://www.notion.so/templates/idea-validation",\n'
    '      "gap_note": "Aimed at general/startup founders, not Etsy sellers'  # cut off
)

# Balanced-but-invalid MIDDLE object (missing comma), valid objects on both sides.
_MALFORMED_MIDDLE = (
    '{"sources": [\n'
    f'  {_obj("A", "https://www.etsy.com/listing/a")},\n'
    '  {"source_name": "B" "url": "https://www.etsy.com/listing/b"},\n'  # invalid
    f'  {_obj("C", "https://www.etsy.com/listing/c")}\n'
    ']}'
)


# --------------------------------------------------------------------------- #
# 1–3, 5. Pure parser behavior
# --------------------------------------------------------------------------- #
def test_truncated_tail_salvages_earlier_complete_objects():
    val, stats = salvage_source_objects(_TRUNCATED_TAIL)
    assert val is not None
    names = [s["source_name"] for s in val["sources"]]
    assert names == ["Etsy listing A", "Etsy listing B"]
    assert stats["salvaged_source_objects"] == 2
    assert stats["discarded_malformed_source_objects"] == 1
    assert stats["parser_step"] == "source_object_salvage"


def test_unterminated_string_does_not_yield_zero_sources():
    # The whole-object parse fails, but salvage keeps the complete earlier ones.
    parsed = parse_extraction_json(_TRUNCATED_TAIL)
    assert parsed is not None
    assert len(parsed["sources"]) == 2


def test_malformed_middle_discarded_later_salvaged():
    val, stats = salvage_source_objects(_MALFORMED_MIDDLE)
    assert val is not None
    names = [s["source_name"] for s in val["sources"]]
    assert names == ["A", "C"]              # B (missing comma) dropped
    assert stats["salvaged_source_objects"] == 2
    assert stats["discarded_malformed_source_objects"] == 1


def test_no_complete_objects_returns_none():
    # A sources array whose very first object is already truncated -> nothing.
    raw = '{"sources": [ {"source_name": "only", "url": "https://x/a", "gap_note": "unterminated'
    val, stats = salvage_source_objects(raw)
    assert val is None
    assert stats["salvaged_source_objects"] == 0
    assert parse_extraction_json(raw) is None  # full failure -> None (fail closed)


def test_no_sources_array_is_not_salvaged():
    assert salvage_source_objects("totally not json")[0] is None
    assert salvage_source_objects('{"other": [1,2,3]}')[0] is None


def test_salvage_never_repairs_strings():
    # The recovered objects must be byte-identical complete JSON, with no invented
    # fields and no "completed" trailing string.
    val, _ = salvage_source_objects(_TRUNCATED_TAIL)
    for s in val["sources"]:
        assert "gap_note" not in s or s["gap_note"]  # never an invented empty repair
        assert set(s) <= {"source_name", "url", "price", "what_it_proves",
                          "what_it_does_not_prove"}


# --------------------------------------------------------------------------- #
# chunk + dedupe helpers
# --------------------------------------------------------------------------- #
def test_chunk_text_splits_long_input():
    text = "\n".join("x" * 100 for _ in range(400))  # ~40k chars
    chunks = chunk_text(text, 12000)
    assert len(chunks) > 1
    assert all(len(c) <= 12000 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_chunk_text_keeps_short_input_single():
    assert chunk_text("small", 12000) == ["small"]


def test_dedupe_raw_sources_by_url_and_name():
    a = {"url": "https://x/1", "source_name": "A"}
    dup = {"url": "https://x/1", "source_name": "A"}
    b = {"url": "https://x/2", "source_name": "B"}
    assert dedupe_raw_sources([a, dup, b, "junk", 5]) == [a, b]


# --------------------------------------------------------------------------- #
# extract() surfaces salvage + decode location
# --------------------------------------------------------------------------- #
class _OneShot:
    def __init__(self, text):
        self._text = text

    def create(self, **kwargs):
        block = types.SimpleNamespace(type="text", text=self._text, citations=None)
        return types.SimpleNamespace(content=[block], stop_reason="end_turn")


def test_extract_reports_salvage_and_decode_location():
    r = ClaudeResearcher(client=types.SimpleNamespace(messages=_OneShot(_TRUNCATED_TAIL)))
    out = r.extract("findings", "instruction", system="s")
    assert out.data is not None and len(out.data["sources"]) == 2
    assert out.parse_error is None           # not a full failure — partial recovery
    assert out.salvage is not None
    assert out.salvage["salvaged_source_objects"] == 2
    assert out.salvage["discarded_malformed_source_objects"] == 1
    # Decode location is attached for debugging the truncation point.
    assert out.salvage.get("char_position") is not None
    assert out.salvage.get("line") is not None
    assert isinstance(out.salvage.get("excerpt"), str)


# --------------------------------------------------------------------------- #
# Prompt hardening (#3)
# --------------------------------------------------------------------------- #
def test_extraction_prompt_rules_present():
    assert "at most 10" in pa.EXTRACT_SYSTEM.lower()
    assert "200 characters" in pa._EXTRACTION_RULES
    assert "no newline characters" in pa._EXTRACTION_RULES.lower()
    assert "json only" in pa._EXTRACTION_RULES.lower()


# --------------------------------------------------------------------------- #
# Integration: shared fakes
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


def _hypothesis():
    return ProductHypothesis(
        product_name="Etsy Seller Launch OS",
        target_buyer="Solo Etsy-first seller of instant-download digital products",
        buyer_job="Decide what digital product to make before wasting build time",
        product_format="Notion operating system (instant-download digital products)",
        primary_channel="Etsy-first",
        missing_mechanism_hypothesis="Gates demand proof and listing readiness before launch",
    )


# Extraction payload: 4 COMPLETE source objects then a truncated 5th.
_FOUR_PLUS_TRUNCATED = (
    '{"sources": [\n'
    + ",\n".join(
        _obj(f"Etsy {i}", f"https://www.etsy.com/listing/salv-{i}",
             price=10.0 + i, buyer_language="I wasted hours and got no sales",
             is_direct_quote=True)
        for i in range(4)
    )
    + ',\n  {"source_name": "cut off", "url": "https://www.etsy.com/listing/x", "gap_note": "trailing'
)


class _SalvageFake:
    """Research finds listings; every extraction returns the truncated payload."""

    def __init__(self):
        self._idx = 0

    def create(self, **kwargs):
        if kwargs.get("tools"):
            self._idx += 1
            items = [types.SimpleNamespace(url=f"https://www.etsy.com/listing/r{self._idx}-{j}")
                     for j in range(5)]
            return _Response([
                _Block(type="text", text="Found real Etsy listings."),
                _Block(type="web_search_tool_result", content=items),
            ])
        return _Response([_Block(type="text", text=_FOUR_PLUS_TRUNCATED)])


def test_salvage_integration_preserves_evidence_and_fails_closed(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    researcher = ClaudeResearcher(client=types.SimpleNamespace(messages=_SalvageFake()))
    brief = asyncio.run(ResearchOrchestrator(researcher=researcher).run_workflow(
        _hypothesis(), recorder=rec))

    # Evidence preserved: Phase 1 kept the 4 complete salvaged sources (not zero).
    assert len(brief.phase_1_result.sources_collected) == 4
    assert brief.phase_1_result.status == PhaseStatus.PASS

    # Salvage was logged with counts + parser step.
    errors = [json.loads(l) for l in
              (rec.run_dir / "extraction_errors.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    salv = [e for e in errors if e["error_type"] == "recovered_via_source_object_salvage"]
    assert salv, "expected a salvage record"
    assert salv[0]["salvaged_source_objects"] == 4
    assert salv[0]["discarded_malformed_source_objects"] == 1
    assert salv[0]["parser_step_failed"] == "source_object_salvage"

    # Truncation makes the run partial (degraded), and fail-closed holds.
    assert rec.run_status == "partial"
    assert brief.decision != Decision.BUILD
    e1 = brief.e1_review or {}
    assert e1.get("review_verdict") != "E1_APPROVED_TO_RECORD"
    assert e1.get("b3_status") == "LOCKED"
    assert e1.get("recording_status") != "RECORDED"
    assert e1.get("public_execution_status") == "NONE"


# --------------------------------------------------------------------------- #
# Chunked extraction: one bad chunk must not erase good chunks
# --------------------------------------------------------------------------- #
class _ChunkFake:
    """Extraction returns valid sources for the GOOD chunk, malformed for the
    BAD chunk, so chunked extraction can be exercised directly."""

    def create(self, **kwargs):
        content = kwargs["messages"][0]["content"]
        if "BADCHUNK" in content:
            return _Response([_Block(type="text", text="not json at all, totally broken")])
        if "GOODCHUNK" in content:
            payload = {"sources": [
                {"source_name": f"G{i}", "url": f"https://www.etsy.com/listing/g{i}",
                 "what_it_proves": "x", "what_it_does_not_prove": "y"} for i in range(3)
            ]}
            return _Response([_Block(type="text", text=json.dumps(payload))])
        return _Response([_Block(type="text", text='{"sources": []}')])


def test_chunked_extraction_keeps_good_chunk_when_another_fails(tmp_path):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    researcher = ClaudeResearcher(client=types.SimpleNamespace(messages=_ChunkFake()))
    agent = pa.Phase1Agent(researcher=researcher)

    findings = "GOODCHUNK\n" + "\n".join("x" * 100 for _ in range(140)) + "\nBADCHUNK\n"
    assert len(findings) > pa._EXTRACTION_CHUNK_CHAR_LIMIT  # forces chunking

    raw_sources = agent._run_extraction(findings, "instruction", rec)
    # The good chunk's 3 sources survive even though the other chunk failed.
    assert len(raw_sources) == 3

    # Per-chunk raw responses were written (compat + per-chunk filenames).
    assert (rec.run_dir / "raw_extraction_response_phase_1.txt").exists()
    assert (rec.run_dir / "raw_extraction_response_phase_1_chunk_1.txt").exists()
    assert (rec.run_dir / "raw_extraction_response_phase_1_chunk_2.txt").exists()

    # The failed chunk is logged with its chunk id; the good chunk is not an error.
    errors = [json.loads(l) for l in
              (rec.run_dir / "extraction_errors.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(e.get("chunk_id") in ("chunk_1", "chunk_2") for e in errors)
    assert rec.run_status == "partial"
