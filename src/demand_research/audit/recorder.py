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
    "claim_ledger.jsonl",
    "evidence_scorecard.json",
    "demand_brief.md",
    "demand_brief.json",
]


def _utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


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
        # even if a particular log has zero records.
        for name in ARTIFACT_FILES:
            (self.run_dir / name).touch(exist_ok=True)

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
        self._rejected_by_reason: Counter = Counter()
        self._rejected_examples: List[dict] = []
        self._buyer_artifacts: List[dict] = []
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
        path = self.run_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

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
        """Persist every model-reported / provider search attempt for a phase."""
        if not attempts:
            return
        for a in attempts:
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
        bundle["claims"] = [c.model_dump(mode="json") for c in claim_entries]
        bundle["artifact_paths"] = {name: name for name in ARTIFACT_FILES}
        brief.audit = bundle
        brief.run_id = self.run_id

        # Render + write the human-readable brief (lazy import avoids a cycle).
        from demand_research.outputs.markdown_generator import render_markdown
        markdown_text = render_markdown(brief)
        (self.run_dir / "demand_brief.md").write_text(markdown_text, encoding="utf-8")
        self._write_json("demand_brief.json", brief.model_dump(mode="json"))

        # Manifest last — it summarises everything above.
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
