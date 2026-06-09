"""RunRecorder — owns a run's durable truth-layer directory.

Every research run creates `runs/{run_id}/` and writes all audit artifacts
there as they happen. Search attempts and rejected sources are streamed to
`.jsonl` files during the phases; the source ledger, buyer-language artifacts,
claim ledger, scorecard, manifest, and brief are written as the run completes.

Nothing here is best-effort-to-stdout: if it matters for an audit, it is
written to disk.
"""

import json
import logging
import os
import re
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from slugify import slugify

from demand_research.audit.models import (
    BuyerLanguageArtifact,
    ClaimLedgerEntry,
    EvidenceScorecard,
    RejectedSourceEntry,
    RunManifest,
    SearchLogEntry,
    SourceLedgerEntry,
)
from demand_research.models import DemandBrief, ProductHypothesis

logger = logging.getLogger(__name__)

ARTIFACT_FILES = [
    "run_manifest.json",
    "search_log.jsonl",
    "rejected_sources.jsonl",
    "source_ledger.jsonl",
    "buyer_language_artifacts.jsonl",
    "price_band_artifacts.jsonl",
    "competitor_map.jsonl",
    "missing_mechanism_gap.json",
    "claim_ledger.jsonl",
    "evidence_scorecard.json",
    "e1_review_gates.json",
    "demand_brief.md",
    "demand_brief.json",
]

# .json artifacts must always be valid JSON, even before they are populated.
_EMPTY_JSON_ARTIFACTS = {
    "run_manifest.json", "evidence_scorecard.json",
    "missing_mechanism_gap.json", "e1_review_gates.json", "demand_brief.json",
}


def _utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_query(query: str) -> str:
    """Lowercase + collapse whitespace so trivially-different spellings of the
    same provider query collapse to one dedup key."""
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def _repo_commit_hash() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 - git may be absent
        return None
    return None


class RunRecorder:
    """Creates and populates one `runs/{run_id}/` truth-layer directory."""

    def __init__(
        self,
        hypothesis: ProductHypothesis,
        model_name: str,
        model_provider: str = "anthropic",
        base_dir: Optional[Path] = None,
    ):
        self.hypothesis = hypothesis
        self.model_name = model_name
        self.model_provider = model_provider
        self.created_at = _utc_now_iso()

        self.product_slug = slugify(hypothesis.product_name) or "product"
        stamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
        self.run_id = f"{stamp}_{self.product_slug}"

        root = base_dir if base_dir is not None else (Path.cwd() / "runs")
        self.run_dir = Path(root) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # Pre-create every artifact file so an auditor always finds the full set,
        # even if a particular log has zero records. .json artifacts are seeded
        # with valid JSON so they parse even before finalization.
        for name in ARTIFACT_FILES:
            path = self.run_dir / name
            if name in _EMPTY_JSON_ARTIFACTS:
                path.write_text("{}", encoding="utf-8")
            else:
                path.touch(exist_ok=True)

        # Accumulators for the manifest / summaries.
        self.phase_prompts_sent: List[dict] = []
        self.notes: List[str] = []
        self.raw_source_count = 0
        self.validated_source_count = 0
        self.rejected_source_count = 0
        self.buyer_language_artifact_count = 0
        self.run_status = "success"

        self._search_counts: Counter = Counter()
        self._search_total = 0
        self._search_seen: set = set()  # (phase_id, normalized_query) already written
        self._rejected_by_reason: Counter = Counter()
        self._rejected_examples: List[dict] = []
        self._buyer_artifacts: List[dict] = []
        self._price_bands: List[dict] = []
        self._competitors: List[dict] = []
        self._missing_mechanism: dict = {}
        self._e1_review_gates: dict = {}
        self._source_entries: List[dict] = []
        self._source_counter = 0
        self._artifact_counter = 0

    # ------------------------------------------------------------------ #
    # Low-level append helpers
    # ------------------------------------------------------------------ #
    def _append_jsonl(self, filename: str, record: dict) -> None:
        path = self.run_dir / filename
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _write_json(self, filename: str, payload: Any) -> None:
        # Atomic: write to a temp file in the same dir, then os.replace so a
        # reader never sees a half-written / unclosed JSON document.
        path = self.run_dir / filename
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _write_text(self, filename: str, text: str) -> None:
        path = self.run_dir / filename
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------------ #
    # During-phase logging
    # ------------------------------------------------------------------ #
    def log_phase_prompt(self, phase_id: str, phase_name: str, prompt: str) -> None:
        self.phase_prompts_sent.append(
            {"phase_id": phase_id, "phase_name": phase_name, "prompt": prompt}
        )

    def add_note(self, note: str) -> None:
        if note not in self.notes:
            self.notes.append(note)

    def mark_partial(self, reason: str) -> None:
        if self.run_status == "success":
            self.run_status = "partial"
        self.add_note(reason)

    def bump_raw(self, count: int) -> None:
        self.raw_source_count += max(0, count)

    def log_searches(self, phase_id: str, phase_name: str, attempts: List[dict]) -> None:
        """Persist search attempts for a phase, de-duplicated.

        The same run_id + phase_id + normalized query is never written twice. If
        both a provider-level (anthropic_web_search) and a model-reported attempt
        exist for the same query, the provider-level record wins and the
        model-reported duplicate is dropped.
        """
        if not attempts:
            return

        # Collapse same-query attempts within this call, preferring provider-level.
        chosen: dict = {}
        order: List[str] = []
        for a in attempts:
            q = _normalize_query(a.get("query", ""))
            existing = chosen.get(q)
            if existing is None:
                chosen[q] = a
                order.append(q)
            else:
                ex_provider = existing.get("search_provider", "")
                provider = a.get("search_provider", "anthropic_web_search")
                if ex_provider != "anthropic_web_search" and provider == "anthropic_web_search":
                    chosen[q] = a  # upgrade to the provider-level record

        for q in order:
            run_key = (phase_id, q)
            if run_key in self._search_seen:
                continue  # already written for this phase (cross-call duplicate)
            self._search_seen.add(run_key)
            a = chosen[q]
            status = a.get("status", "unknown")
            self._search_counts[status] += 1
            self._search_total += 1
            entry = SearchLogEntry(
                run_id=self.run_id,
                phase_id=phase_id,
                phase_name=phase_name,
                timestamp_utc=_utc_now_iso(),
                search_provider=a.get("search_provider", "anthropic_web_search"),
                search_query=a.get("query", ""),
                result_count_returned=int(a.get("result_count", 0) or 0),
                result_urls=list(a.get("result_urls", []) or []),
                status=status,
                error_message=a.get("error_message"),
            )
            self._append_jsonl("search_log.jsonl", entry.model_dump(mode="json"))

    def log_rejected(
        self,
        phase_id: str,
        phase_name: str,
        raw: dict,
        reason: str,
        validator_rule: str = "",
    ) -> None:
        """Durably record a dropped source — never silently discard."""
        self.rejected_source_count += 1
        self._rejected_by_reason[reason] += 1
        url = str(raw.get("url", "")) if isinstance(raw, dict) else ""
        title = str(raw.get("source_name", "")) if isinstance(raw, dict) else ""
        platform = str(raw.get("platform", "")) if isinstance(raw, dict) else ""
        excerpt = ""
        if isinstance(raw, dict):
            excerpt = str(raw.get("what_it_proves") or raw.get("buyer_language") or "")[:300]
        entry = RejectedSourceEntry(
            run_id=self.run_id, phase_id=phase_id, phase_name=phase_name,
            timestamp_utc=_utc_now_iso(), url=url, title=title, platform=platform,
            rejection_reason=reason, validator_rule=validator_rule, raw_excerpt=excerpt,
        )
        record = entry.model_dump(mode="json")
        self._append_jsonl("rejected_sources.jsonl", record)
        if len(self._rejected_examples) < 10:
            self._rejected_examples.append(record)

    def log_source(self, entry: SourceLedgerEntry) -> str:
        """Write a validated source to the ledger; returns the assigned source_id."""
        self.validated_source_count += 1
        self._append_jsonl("source_ledger.jsonl", entry.model_dump(mode="json"))
        self._source_entries.append(entry.model_dump(mode="json"))
        return entry.source_id

    def next_source_id(self) -> str:
        self._source_counter += 1
        return f"S{self._source_counter:03d}"

    def log_buyer_language(self, artifact: BuyerLanguageArtifact) -> None:
        self.buyer_language_artifact_count += 1
        record = artifact.model_dump(mode="json")
        self._append_jsonl("buyer_language_artifacts.jsonl", record)
        self._buyer_artifacts.append(record)

    def next_artifact_id(self) -> str:
        self._artifact_counter += 1
        return f"BL{self._artifact_counter:03d}"

    def log_price_band(self, record: dict) -> None:
        """Append one structured Phase 3 price-band artifact."""
        self._price_bands.append(record)
        self._append_jsonl("price_band_artifacts.jsonl", record)

    def log_competitor(self, record: dict) -> None:
        """Append one structured Phase 4 competitor-map artifact."""
        self._competitors.append(record)
        self._append_jsonl("competitor_map.jsonl", record)

    def write_missing_mechanism(self, record: dict) -> None:
        """Write the single Phase 5 missing-mechanism gap artifact (valid JSON)."""
        self._missing_mechanism = record
        self._write_json("missing_mechanism_gap.json", record)

    def write_e1_review_gates(self, record: dict) -> None:
        """Write the E1 review gates artifact (valid JSON; pass AND fail runs)."""
        self._e1_review_gates = record
        self._write_json("e1_review_gates.json", record)

    # ------------------------------------------------------------------ #
    # Summaries for the bundle / markdown
    # ------------------------------------------------------------------ #
    def search_summary(self) -> dict:
        return {
            "total": self._search_total,
            "results_found": self._search_counts.get("results_found", 0),
            "zero_results": self._search_counts.get("zero_results", 0),
            "tool_error": self._search_counts.get("tool_error", 0),
            "rate_limited": self._search_counts.get("rate_limited", 0),
            "unknown": self._search_counts.get("unknown", 0),
        }

    def rejected_summary(self) -> dict:
        return {
            "total": self.rejected_source_count,
            "by_reason": dict(self._rejected_by_reason),
            "examples": self._rejected_examples,
        }

    # ------------------------------------------------------------------ #
    # Finalization
    # ------------------------------------------------------------------ #
    def finalize(
        self,
        brief: DemandBrief,
        audit_core: dict,
        claim_entries: List[ClaimLedgerEntry],
    ) -> str:
        """Assemble the full audit bundle, write all remaining artifacts, and
        return the rendered markdown brief."""
        # Claim ledger.
        for c in claim_entries:
            self._append_jsonl("claim_ledger.jsonl", c.model_dump(mode="json"))

        scorecard_dict = audit_core.get("scorecard", {})
        scorecard = EvidenceScorecard(run_id=self.run_id, **{
            k: scorecard_dict.get(k)
            for k in ("formula_version", "components", "total_score",
                      "decision_thresholds", "hard_gate_overrides")
            if scorecard_dict.get(k) is not None
        })
        self._write_json("evidence_scorecard.json", scorecard.model_dump(mode="json"))

        # Assemble the bundle the markdown renderer reads.
        bundle = dict(audit_core)
        bundle["run_id"] = self.run_id
        bundle["run_status"] = self.run_status
        bundle["search_summary"] = self.search_summary()
        bundle["rejected_summary"] = self.rejected_summary()
        bundle["buyer_artifacts"] = self._buyer_artifacts
        bundle["price_bands"] = self._price_bands
        bundle["competitors"] = self._competitors
        bundle["missing_mechanism"] = self._missing_mechanism
        bundle["e1_review_gates"] = self._e1_review_gates
        bundle["claims"] = [c.model_dump(mode="json") for c in claim_entries]
        bundle["artifact_paths"] = {name: name for name in ARTIFACT_FILES}
        brief.audit = bundle
        brief.run_id = self.run_id

        # Render + write the human-readable brief (lazy import avoids a cycle).
        from demand_research.outputs.markdown_generator import render_markdown
        markdown_text = render_markdown(brief)
        self._write_text("demand_brief.md", markdown_text)
        self._write_json("demand_brief.json", brief.model_dump(mode="json"))

        # Manifest last — it summarises everything above (incl. E1 review state).
        e1 = self._e1_review_gates or {}
        manifest = RunManifest(
            run_id=self.run_id,
            product_slug=self.product_slug,
            created_at_utc=self.created_at,
            completed_at_utc=_utc_now_iso(),
            repo_commit_hash=_repo_commit_hash(),
            model_name=self.model_name,
            model_provider=self.model_provider,
            run_status=self.run_status,
            hypothesis={
                "product_name": self.hypothesis.product_name,
                "target_buyer": self.hypothesis.target_buyer,
                "buyer_job": self.hypothesis.buyer_job,
                "product_format": self.hypothesis.product_format,
                "primary_channel": self.hypothesis.primary_channel,
                "missing_mechanism_hypothesis": self.hypothesis.missing_mechanism_hypothesis,
            },
            phases_requested=[p["phase_id"] for p in self.phase_prompts_sent],
            phase_prompts_sent=self.phase_prompts_sent,
            raw_source_count_before_validation=self.raw_source_count,
            validated_source_count=self.validated_source_count,
            rejected_source_count=self.rejected_source_count,
            buyer_language_artifact_count=self.buyer_language_artifact_count,
            claim_count=len(claim_entries),
            final_decision=brief.decision.value,
            evidence_quality_score=brief.evidence_quality_score,
            fatal_gaps=audit_core.get("fatal_gaps", []),
            notes=self.notes,
            primitive_name=e1.get("primitive_name", ""),
            target_member=e1.get("target_member", ""),
            excluded_members=e1.get("excluded_members", []),
            current_state=e1.get("current_state", ""),
            candidate_state=e1.get("candidate_state", ""),
            review_verdict=e1.get("review_verdict", ""),
            recording_status=e1.get("recording_status", ""),
            b2_acceptance_status=e1.get("b2_acceptance_status", ""),
            b3_status=e1.get("b3_status", ""),
            public_execution_status=e1.get("public_execution_status", ""),
            e1_review_gates_path="e1_review_gates.json" if self._e1_review_gates else "",
        )
        if _repo_commit_hash() is None:
            self.add_note("repo_commit_hash unavailable (git not present); reproducibility reduced.")
            manifest.notes = self.notes
        self._write_json("run_manifest.json", manifest.model_dump(mode="json"))
        return markdown_text

    def write_failed_manifest(self, reason: str) -> None:
        """Write a manifest for a run that could not produce a verdict."""
        self.run_status = "failed"
        self.add_note(reason)
        manifest = RunManifest(
            run_id=self.run_id,
            product_slug=self.product_slug,
            created_at_utc=self.created_at,
            completed_at_utc=_utc_now_iso(),
            repo_commit_hash=_repo_commit_hash(),
            model_name=self.model_name,
            model_provider=self.model_provider,
            run_status="failed",
            hypothesis={
                "product_name": self.hypothesis.product_name,
                "target_buyer": self.hypothesis.target_buyer,
                "buyer_job": self.hypothesis.buyer_job,
                "product_format": self.hypothesis.product_format,
                "primary_channel": self.hypothesis.primary_channel,
                "missing_mechanism_hypothesis": self.hypothesis.missing_mechanism_hypothesis,
            },
            phases_requested=[p["phase_id"] for p in self.phase_prompts_sent],
            phase_prompts_sent=self.phase_prompts_sent,
            raw_source_count_before_validation=self.raw_source_count,
            validated_source_count=self.validated_source_count,
            rejected_source_count=self.rejected_source_count,
            buyer_language_artifact_count=self.buyer_language_artifact_count,
            claim_count=0,
            final_decision="",
            evidence_quality_score=0.0,
            fatal_gaps=[reason],
            notes=self.notes,
        )
        self._write_json("run_manifest.json", manifest.model_dump(mode="json"))
