"""Tests for optional screenshot support + audit completeness (Priority C).

Screenshots document accepted public evidence; they are never the evidence:

  - capture success alone can never satisfy a gate;
  - capture failure alone can never invent or invalidate evidence;
  - when capture is available, accepted public evidence without a screenshot
    is marked audit-incomplete (reporting only);
  - when capture is unavailable / not configured, that is recorded honestly
    and the run continues — no crash, no fabricated screenshots.

No browser is required: tests use fakes; the Playwright path is only probed
for graceful degradation.
"""

import asyncio
import hashlib
import json
import types
from pathlib import Path

from demand_research.models import ProductHypothesis
from demand_research.research.claude_researcher import ClaudeResearcher
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.audit.recorder import RunRecorder
from demand_research.screenshots import (
    NoOpScreenshotCapture,
    PlaywrightScreenshotCapture,
    ScreenshotCapture,
    ScreenshotResult,
    build_screenshot_capture,
    build_screenshot_coverage,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeScreenshotCapture(ScreenshotCapture):
    """Writes a real file with a real hash — no browser involved."""

    configured = True

    def __init__(self, fail_urls=(), explode=False):
        self._fail_urls = set(fail_urls)
        self._explode = explode
        self.capture_calls = 0

    def is_available(self) -> bool:
        return True

    def availability_detail(self) -> str:
        return "Fake capture (test double) is available."

    def capture(self, url: str, out_dir):
        self.capture_calls += 1
        if self._explode:
            raise RuntimeError("capture exploded")
        if url in self._fail_urls:
            return ScreenshotResult(
                url=url, attempted=True, success=False,
                error_reason="TimeoutError: navigation timed out",
            )
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"shot-{hashlib.sha256(url.encode()).hexdigest()[:12]}.png"
        data = f"PNGFAKE::{url}".encode()
        (out_dir / name).write_bytes(data)
        return ScreenshotResult(
            url=url, attempted=True, success=True,
            file_path=f"screenshots/{name}",
            sha256=hashlib.sha256(data).hexdigest(),
            captured_utc="2026-06-10T00:00:00Z",
            browser="fake",
        )


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

_FULL_SOURCES = [
    [_src("signal", i) for i in range(4)],
    [_src("pain", i, quote=f"{_PAIN} #{i}") for i in range(4)],
    [_src("price", i, price=True) for i in range(4)],
    [_src("competitor", i) for i in range(4)],
]


def _run(tmp_path, screenshotter=None, sources=None):
    rec = RunRecorder(_hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(
        researcher=_fake_researcher(sources or _FULL_SOURCES, _GAP),
        screenshotter=screenshotter,
    )
    brief = asyncio.run(orch.run_workflow(_hypothesis(), recorder=rec))
    return brief, rec


# --------------------------------------------------------------------------- #
# 1. Factory / availability honesty (no browser required)
# --------------------------------------------------------------------------- #
def test_factory_disabled_by_default_returns_noop():
    capture = build_screenshot_capture(enabled=False)
    assert isinstance(capture, NoOpScreenshotCapture)
    assert capture.configured is False
    assert capture.is_available() is False
    result = capture.capture("https://example-public-page.com/x", None)
    assert result.attempted is False and result.success is False
    assert "disabled" in (result.skipped_reason or "").lower()


def test_factory_enabled_without_playwright_degrades_honestly():
    # Playwright is not installed in the test environment: requesting capture
    # must yield a configured-but-unavailable no-op, never an exception.
    capture = build_screenshot_capture(enabled=True)
    if isinstance(capture, PlaywrightScreenshotCapture):
        return  # environment has playwright; degradation path not exercisable
    assert capture.configured is True
    assert capture.is_available() is False
    assert "not installed" in capture.availability_detail().lower()


def test_playwright_capture_without_playwright_skips_not_raises():
    capture = PlaywrightScreenshotCapture()
    if capture.is_available():
        return
    result = capture.capture("https://example-public-page.com/x", Path("/tmp"))
    assert result.attempted is False and result.success is False
    assert result.skipped_reason


# --------------------------------------------------------------------------- #
# 2. Default run (capture off): honest records, nothing fabricated
# --------------------------------------------------------------------------- #
def test_default_run_records_skips_per_accepted_source(tmp_path):
    brief, rec = _run(tmp_path)  # default: capture disabled
    shots = [json.loads(l) for l in
             (rec.run_dir / "screenshots.jsonl").read_text().splitlines() if l.strip()]
    assert shots, "every accepted source gets a screenshot record, even skips"
    for s in shots:
        assert s["attempted"] is False and s["success"] is False
        assert s["audit_status"] == "capture_unavailable"
        assert s["file_path"] is None and s["sha256"] is None
    # Ledger entries carry the same honest audit status.
    entries = [json.loads(l) for l in
               (rec.run_dir / "source_ledger.jsonl").read_text().splitlines() if l.strip()]
    assert all(e["audit_status"] == "capture_unavailable" for e in entries)
    assert all(e["screenshot_filename"] is None for e in entries)
    # Coverage says exactly why the audit is not complete.
    cov = brief.audit["screenshot_coverage"]
    assert cov["audit_complete_for_e1_recording"] is False
    assert "capture_not_configured" in cov["reason"]
    assert cov["screenshots_captured"] == 0


def test_capture_unavailable_never_blocks_or_changes_verdict(tmp_path):
    brief_off, _ = _run(tmp_path / "off")
    brief_on, _ = _run(tmp_path / "on", screenshotter=FakeScreenshotCapture())
    # Same evidence, with and without screenshots -> identical verdicts.
    assert brief_on.decision == brief_off.decision
    assert brief_on.review_verdict == brief_off.review_verdict
    gates_on = {g["gate_id"]: g["status"] for g in brief_on.e1_review["gates"]}
    gates_off = {g["gate_id"]: g["status"] for g in brief_off.e1_review["gates"]}
    assert gates_on == gates_off


# --------------------------------------------------------------------------- #
# 3. Capture available: metadata, linkage, coverage
# --------------------------------------------------------------------------- #
def test_successful_capture_records_full_metadata(tmp_path):
    fake = FakeScreenshotCapture()
    brief, rec = _run(tmp_path, screenshotter=fake)
    shots = [json.loads(l) for l in
             (rec.run_dir / "screenshots.jsonl").read_text().splitlines() if l.strip()]
    assert shots and all(s["success"] for s in shots)
    for s in shots:
        assert s["source_id"].startswith("S")
        assert s["phase_id"].startswith("phase_")
        assert s["audit_status"] == "audit_complete"
        assert s["file_path"].startswith("screenshots/")
        assert s["sha256"] and len(s["sha256"]) == 64
        assert s["captured_utc"]
        # The file really exists and the hash really matches its bytes.
        data = (rec.run_dir / s["file_path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == s["sha256"]


def test_accepted_price_and_competitor_artifacts_link_screenshots(tmp_path):
    _, rec = _run(tmp_path, screenshotter=FakeScreenshotCapture())
    prices = [json.loads(l) for l in
              (rec.run_dir / "price_band_artifacts.jsonl").read_text().splitlines()
              if l.strip()]
    assert prices and all(
        (r["screenshot_filename"] or "").startswith("screenshots/") for r in prices
    )
    entries = [json.loads(l) for l in
               (rec.run_dir / "source_ledger.jsonl").read_text().splitlines() if l.strip()]
    assert all(e["audit_status"] == "audit_complete" for e in entries)
    assert all((e["screenshot_filename"] or "").startswith("screenshots/")
               for e in entries)
    assert all(e["screenshot_sha256"] for e in entries)


def test_full_capture_marks_audit_complete_for_e1(tmp_path):
    brief, rec = _run(tmp_path, screenshotter=FakeScreenshotCapture())
    cov = brief.audit["screenshot_coverage"]
    assert cov["capture_available"] is True
    required = cov["screenshots_required_for_accepted_public_sources"]
    assert required > 0
    assert cov["screenshots_attempted"] == required
    assert cov["screenshots_captured"] == required
    assert cov["screenshots_failed"] == 0
    assert cov["accepted_sources_without_screenshots"] == []
    assert cov["audit_complete_for_e1_recording"] is True
    # The E1 artifact carries audit completeness SEPARATELY from the gates.
    e1 = json.loads((rec.run_dir / "e1_review_gates.json").read_text())
    assert e1["audit_completeness"]["audit_complete_for_e1_recording"] is True
    assert "gates" in e1  # gate validity remains its own dimension


def test_partial_capture_failure_marks_audit_incomplete_not_invalid(tmp_path):
    failing_url = "https://www.etsy.com/listing/price-1"
    fake = FakeScreenshotCapture(fail_urls={failing_url})
    brief, rec = _run(tmp_path, screenshotter=fake)

    cov = brief.audit["screenshot_coverage"]
    assert cov["screenshots_failed"] >= 1
    assert cov["audit_complete_for_e1_recording"] is False
    assert "captures_failed_or_missing" in cov["reason"]
    assert cov["accepted_sources_without_screenshots"]

    # The failed-capture source is audit-incomplete but its EVIDENCE stands:
    # still accepted, still in the ledger, phase still passed.
    entries = [json.loads(l) for l in
               (rec.run_dir / "source_ledger.jsonl").read_text().splitlines() if l.strip()]
    failed = [e for e in entries if e["url"].rstrip("/") == failing_url]
    assert failed and all(e["audit_status"] == "audit_incomplete" for e in failed)
    assert brief.phase_3_result.status.value == "PASS"
    # Verdict identical to the all-captures-succeed run (screenshots never gate).
    brief_ok, _ = _run(tmp_path / "ok", screenshotter=FakeScreenshotCapture())
    assert brief.decision == brief_ok.decision
    assert brief.review_verdict == brief_ok.review_verdict


def test_exploding_capture_never_crashes_the_run(tmp_path):
    brief, rec = _run(tmp_path, screenshotter=FakeScreenshotCapture(explode=True))
    assert brief.review_verdict  # run completed
    shots = [json.loads(l) for l in
             (rec.run_dir / "screenshots.jsonl").read_text().splitlines() if l.strip()]
    assert shots and all(not s["success"] for s in shots)
    assert all("capture raised unexpectedly" in (s["error_reason"] or "") for s in shots)


def test_one_capture_per_url_even_when_reused_across_phases(tmp_path):
    fake = FakeScreenshotCapture()
    _run(tmp_path, screenshotter=fake)
    # Phase 4 falls back to phase 3 cards when thin; the URL cache must keep
    # one capture per unique URL regardless of how many ledger rows reuse it.
    unique_urls = {f"https://www.etsy.com/listing/{p}-{i}"
                   for p in ("signal", "pain", "price", "competitor") for i in range(4)}
    assert fake.capture_calls <= len(unique_urls)


# --------------------------------------------------------------------------- #
# 4. Coverage unit + surfaces
# --------------------------------------------------------------------------- #
def test_coverage_vacuous_when_no_accepted_sources():
    cov = build_screenshot_coverage(
        capture=NoOpScreenshotCapture("disabled"),
        screenshot_records=[], accepted_public_source_ids=[],
    )
    assert cov["audit_complete_for_e1_recording"] is True
    assert "No accepted public sources" in cov["reason"]
    assert cov["screenshots_required_for_accepted_public_sources"] == 0


def test_coverage_governance_text_forbids_gate_use():
    cov = build_screenshot_coverage(
        capture=None, screenshot_records=[], accepted_public_source_ids=["S001"],
    )
    gov = cov["governance"].lower()
    assert "cannot satisfy a gate" in gov
    assert "never change the verdict" in gov


def test_markdown_and_diagnostics_surface_screenshot_coverage(tmp_path):
    failing_url = "https://www.etsy.com/listing/price-1"
    _, rec = _run(tmp_path, screenshotter=FakeScreenshotCapture(fail_urls={failing_url}))
    md = (rec.run_dir / "demand_brief.md").read_text()
    assert "## Screenshot Audit Coverage" in md
    assert "Audit complete for E1 recording:" in md
    assert "never satisfies a gate" in md
    diag = json.loads((rec.run_dir / "decision_diagnostics.json").read_text())
    assert diag["screenshot_coverage"]["screenshots_failed"] >= 1


def test_connector_registry_reports_fake_capture_available(tmp_path):
    _, rec = _run(tmp_path, screenshotter=FakeScreenshotCapture())
    reg = json.loads((rec.run_dir / "connector_registry.json").read_text())
    by_name = {c["name"]: c for c in reg["connectors"]}
    assert by_name["screenshot_capture"]["status"] == "available"
