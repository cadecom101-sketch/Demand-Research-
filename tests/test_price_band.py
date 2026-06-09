"""Price-band claim integrity tests (live-run audit fix).

A price-band claim may only be 'supported' by VERIFIED price artifacts (specific
competitor URL + observed price). Generic Phase 3 competitor leads and general
market-pricing articles (SendOwl-style) must never satisfy it. These tests run
offline using the fake client helpers from test_audit.
"""

import asyncio
import json

from demand_research.models import Decision, PhaseStatus
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.audit.recorder import RunRecorder

import test_audit as t


def _read_jsonl(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _run(sources, gap=None, tmp_path=None):
    rec = RunRecorder(t._hypothesis(), model_name="m", base_dir=tmp_path)
    orch = ResearchOrchestrator(researcher=t._researcher(sources, gap or t._GAP))
    brief = asyncio.run(orch.run_workflow(t._hypothesis(), recorder=rec))
    return rec, brief


def _channel_claim(rec):
    for c in _read_jsonl(rec.run_dir / "claim_ledger.jsonl"):
        if c["claim_type"] == "channel_viability" and "workable price band" in c["claim"]:
            return c
    return None


# 1 — 11 Phase 3 competitor leads, 0 verified prices -> claim unsupported
def test_price_claim_unsupported_with_only_leads(tmp_path):
    sources = [
        [t._src("signal", i) for i in range(4)],
        [t._src("quote", i, quote=True) for i in range(4)],
        [t._src("lead", i) for i in range(11)],   # 11 leads, no price -> Phase 3 FAIL
    ]
    rec, brief = _run(sources, tmp_path=tmp_path)
    assert brief.phase_3_result.status == PhaseStatus.FAIL
    assert _read_jsonl(rec.run_dir / "price_band_artifacts.jsonl") == []
    claim = _channel_claim(rec)
    assert claim["status"] == "unsupported"
    assert claim["supporting_source_ids"] == []
    assert "competitor leads do not satisfy" in claim["reason"].lower()


# 2 — "What This Proves" must not claim a workable price band when 0 artifacts
def test_what_proves_excludes_price_band_when_zero_artifacts(tmp_path):
    sources = [
        [t._src("signal", i) for i in range(4)],
        [t._src("quote", i, quote=True) for i in range(4)],
        [t._src("lead", i) for i in range(11)],
    ]
    rec, _ = _run(sources, tmp_path=tmp_path)
    md = (rec.run_dir / "demand_brief.md").read_text()
    proves = md[md.find("## What This Proves"):md.find("## What This Does NOT Prove")]
    assert "price band" not in proves.lower()
    not_proves = md[md.find("## What This Does NOT Prove"):md.find("## What Would Change")]
    assert "price band" in not_proves.lower()


# 3 — SendOwl-style general pricing article is NOT a competitor price artifact
def test_directional_article_not_a_price_artifact(tmp_path):
    sendowl = {
        "source_name": "SendOwl Pricing Guide",
        "url": "https://www.sendowl.com/blog/digital-product-pricing",
        "platform": "blog", "price": 7.0,
        "buyer_language": None, "is_direct_quote": None,
        "what_it_proves": "General market pricing ranges for digital products.",
        "what_it_does_not_prove": "x", "gap_note": None,
    }
    sources = [
        [t._src("signal", i) for i in range(4)],
        [t._src("quote", i, quote=True) for i in range(4)],
        [dict(sendowl, source_name=f"SendOwl Pricing Guide {i}") for i in range(3)],
    ]
    rec, brief = _run(sources, tmp_path=tmp_path)
    # Directional articles do not satisfy Phase 3 and are not price artifacts.
    assert brief.phase_3_result.status == PhaseStatus.FAIL
    assert _read_jsonl(rec.run_dir / "price_band_artifacts.jsonl") == []
    # A weaker directional claim is recorded instead.
    directional = [c for c in _read_jsonl(rec.run_dir / "claim_ledger.jsonl")
                   if "directional market pricing" in c["claim"].lower()]
    assert directional and directional[0]["status"] == "partially_supported"


# 4 — Phase 3 sources marked "exact price not captured" don't count as priced
def test_not_captured_price_excluded(tmp_path):
    notcap = {
        "source_name": "Competitor with hidden price",
        "url": "https://www.etsy.com/listing/hidden-1",
        "platform": "Etsy", "price": 19.99,        # price present...
        "buyer_language": None, "is_direct_quote": None,
        "what_it_proves": "A competitor exists.",
        "what_it_does_not_prove": "exact price not captured",   # ...but flagged not captured
        "gap_note": None,
    }
    sources = [
        [t._src("signal", i) for i in range(4)],
        [t._src("quote", i, quote=True) for i in range(4)],
        [dict(notcap, url=f"https://www.etsy.com/listing/hidden-{i}") for i in range(3)],
    ]
    rec, brief = _run(sources, tmp_path=tmp_path)
    assert brief.phase_3_result.status == PhaseStatus.FAIL
    assert _read_jsonl(rec.run_dir / "price_band_artifacts.jsonl") == []
    assert _channel_claim(rec)["status"] == "unsupported"


# 5 — 3 verified price artifacts -> price-band claim can be supported
def test_price_claim_supported_with_verified_prices(tmp_path):
    sources = [
        [t._src("signal", i) for i in range(4)],
        [t._src("quote", i, quote=True) for i in range(4)],
        [t._src("price", i, price=True) for i in range(3)],   # 3 real prices
        [t._src("competitor", i) for i in range(4)],
    ]
    rec, brief = _run(sources, tmp_path=tmp_path)
    assert brief.phase_3_result.status == PhaseStatus.PASS
    artifacts = _read_jsonl(rec.run_dir / "price_band_artifacts.jsonl")
    assert len(artifacts) >= 3
    claim = _channel_claim(rec)
    assert claim["status"] == "supported"
    assert len(claim["supporting_source_ids"]) >= 3
