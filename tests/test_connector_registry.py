"""Tests for the free/public evidence-connector registry (Priority B).

The registry is a capability + audit layer only: it declares which evidence
surfaces a run can observe and how, records that honestly per run, and skips
unusable connectors safely. It is NOT a scraping layer — the web-search
research provider remains the only real collection mechanism — and it never
prompts for credentials, fabricates availability, crashes the workflow, or
feeds a gate.
"""

import asyncio
import json
import types

from demand_research.connectors import (
    CONNECTOR_STATUSES,
    ConnectorDescriptor,
    ConnectorRegistry,
    build_default_registry,
)
from demand_research.models import ProductHypothesis
from demand_research.research.claude_researcher import ClaudeResearcher
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.audit.recorder import RunRecorder


# --------------------------------------------------------------------------- #
# Shared workflow fakes
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
        product_format="Notion operating system",
        primary_channel="Etsy-first",
        missing_mechanism_hypothesis="Gates demand proof and listing readiness before launch",
    )


def _src(prefix, i):
    return {
        "source_name": f"{prefix} {i}",
        "url": f"https://www.etsy.com/listing/{prefix}-{i}",
        "platform": "Etsy",
        "what_it_proves": "A comparable product exists.",
        "what_it_does_not_prove": "Does not prove conversion demand.",
    }


_GAP = {"is_structural": True, "gap_statement": "Gates launch before motion.",
        "reason": "Competitors only track after the fact."}


# --------------------------------------------------------------------------- #
# 1. Registry behavior (unit)
# --------------------------------------------------------------------------- #
def test_default_registry_with_api_key_marks_surfaces_reachable():
    registry = build_default_registry(anthropic_api_key_present=True)
    audit = {r["name"]: r for r in registry.audit()}

    assert audit["anthropic_web_search"]["status"] == "available"
    assert audit["anthropic_web_search"]["kind"] == "research_provider"
    assert audit["anthropic_web_search"]["requires_credentials"] == ["ANTHROPIC_API_KEY"]

    for name in ("etsy_public", "gumroad_public", "notion_marketplace_public",
                 "reddit_public", "youtube_public", "google_trends_public"):
        assert audit[name]["status"] == "via_web_search"
        assert audit[name]["kind"] == "public_source"
        assert audit[name]["requires_credentials"] == []
        # Honest: a public surface is reached through the provider, no scraper
        # is claimed to exist.
        assert "no direct scraper" in audit[name]["detail"].lower()

    # Screenshot capability defaults to not_configured (off by default).
    assert audit["screenshot_capture"]["status"] == "not_configured"


def test_default_registry_without_api_key_reports_not_configured():
    registry = build_default_registry(anthropic_api_key_present=False)
    audit = {r["name"]: r for r in registry.audit()}
    assert audit["anthropic_web_search"]["status"] == "not_configured"
    # No credential prompt — the detail explicitly says so.
    assert "no credential prompt" in audit["anthropic_web_search"]["detail"].lower()
    assert audit["etsy_public"]["status"] == "unavailable"
    # Every record still has a valid status and timestamp (no crash).
    for r in registry.audit():
        assert r["status"] in CONNECTOR_STATUSES
        assert r["checked_utc"]


def test_usable_for_phase_skips_unusable_connectors():
    available = build_default_registry(anthropic_api_key_present=True)
    names = {r["name"] for r in available.usable_for_phase(2)}
    assert "reddit_public" in names and "youtube_public" in names
    assert "google_trends_public" not in names  # phase 1 only

    unconfigured = build_default_registry(anthropic_api_key_present=False)
    assert unconfigured.usable_for_phase(2) == []  # skipped, not an error


def test_broken_probe_never_crashes_the_registry():
    registry = ConnectorRegistry()

    def _exploding_probe():
        raise RuntimeError("boom")

    registry.register(ConnectorDescriptor(
        name="bad_connector", kind="capability", description="always explodes",
        phases=[1], probe=_exploding_probe,
    ))
    records = registry.audit()  # must not raise
    assert records[0]["status"] == "unavailable"
    assert "safely" in records[0]["detail"]


def test_registry_summary_buckets_usable_vs_unusable():
    registry = build_default_registry(anthropic_api_key_present=True)
    summary = registry.summary()
    assert summary["total"] == len(registry.names())
    assert "anthropic_web_search" in summary["usable"]
    assert "screenshot_capture" in summary["unusable"]  # not configured
    assert sum(summary["by_status"].values()) == summary["total"]


# --------------------------------------------------------------------------- #
# 2. Workflow integration: audit artifact written, run unaffected
# --------------------------------------------------------------------------- #
def _run_clean_workflow(tmp_path):
    sources_by_call = [
        [_src("signal", i) for i in range(4)],
        [], [], [],
    ]
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    return brief, rec


def test_run_writes_connector_registry_artifact(tmp_path):
    brief, rec = _run_clean_workflow(tmp_path)
    record = json.loads((rec.run_dir / "connector_registry.json").read_text())
    assert record["run_id"] == rec.run_id
    assert record["checked_utc"]
    names = {c["name"] for c in record["connectors"]}
    assert "anthropic_web_search" in names
    assert "etsy_public" in names
    assert "screenshot_capture" in names
    for c in record["connectors"]:
        assert c["status"] in CONNECTOR_STATUSES
        assert c["detail"]
    # Summary buckets are consistent.
    summary = record["summary"]
    assert summary["total"] == len(record["connectors"])
    # The connector audit also reaches the in-brief audit bundle.
    assert "connector_registry" in (brief.audit or {})


def test_markdown_brief_lists_evidence_connectors(tmp_path):
    _, rec = _run_clean_workflow(tmp_path)
    md = (rec.run_dir / "demand_brief.md").read_text()
    assert "## Evidence Connectors" in md
    assert "anthropic_web_search" in md
    assert "never feeds a gate" in md


def test_connector_status_never_changes_verdict(tmp_path):
    # Identical evidence with all-unconfigured connectors must yield the same
    # verdict: connector status is reporting, never a gate input.
    brief, _ = _run_clean_workflow(tmp_path / "a")

    sources_by_call = [[_src("signal", i) for i in range(4)], [], [], []]
    rec_b = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path / "b")
    orch_b = ResearchOrchestrator(researcher=_fake_researcher(sources_by_call, _GAP))
    # Force the registry to report everything unconfigured for run B.
    import demand_research.agents.orchestrator as orch_mod
    original = orch_mod.build_default_registry
    orch_mod.build_default_registry = (
        lambda **kw: original(anthropic_api_key_present=False)
    )
    try:
        brief_b = asyncio.run(orch_b.run_workflow(_hypothesis(), recorder=rec_b))
    finally:
        orch_mod.build_default_registry = original

    assert brief_b.decision == brief.decision
    assert brief_b.review_verdict == brief.review_verdict
