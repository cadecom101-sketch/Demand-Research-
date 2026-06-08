"""Pydantic models for the audit/provenance artifacts.

These are deliberately serialization-friendly: each maps 1:1 to a record in a
durable on-disk artifact (`.jsonl` line or `.json` document). Every field that
ends up in an audit file is declared here so the schema is explicit and
reviewable rather than implied by ad-hoc dict construction.
"""

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class SearchLogEntry(BaseModel):
    """One search attempt — logged whether it found results, returned zero, or errored."""

    run_id: str
    phase_id: str
    phase_name: str
    timestamp_utc: str
    search_provider: str
    search_query: str
    result_count_returned: int = 0
    result_urls: List[str] = Field(default_factory=list)
    status: str = "unknown"  # results_found | zero_results | tool_error | rate_limited | unknown
    error_message: Optional[str] = None


class RejectedSourceEntry(BaseModel):
    """A source that entered the pipeline but did not survive validation."""

    run_id: str
    phase_id: str
    phase_name: str
    timestamp_utc: str
    url: str = ""
    title: str = ""
    platform: str = ""
    rejection_reason: str = "other"
    validator_rule: str = ""
    raw_excerpt: str = ""
    source_snapshot: Optional[str] = None


class SourceLedgerEntry(BaseModel):
    """A validated source that survived into the brief."""

    run_id: str
    source_id: str
    phase_id: str
    phase_name: str
    url: str
    title: str = ""
    platform: str = ""
    date_observed: str = ""
    search_query: str = ""
    retrieved_excerpt: str = ""
    buyer_language_captured: str = ""
    evidence_type: str = "other"
    evidence_grade: str = "C"
    claim_supported: str = ""
    claim_not_supported: str = ""
    confidence: float = 0.0


class BuyerLanguageArtifact(BaseModel):
    """A verbatim buyer-language quote, linked back to its source."""

    run_id: str
    artifact_id: str
    phase_id: str
    source_id: str
    url: str = ""
    platform: str = ""
    quote: str = ""
    speaker_type: str = "unknown"  # seller | buyer | creator | unknown
    pain_type: str = "other"
    strength: str = "medium"  # strong | medium | weak
    why_it_matters: str = ""
    what_it_does_not_prove: str = ""


class ClaimLedgerEntry(BaseModel):
    """A meaningful claim in the brief, with its supporting/contradicting sources."""

    claim_id: str
    run_id: str
    claim: str
    claim_type: str = "other"
    required_evidence_grade: str = "C"
    supporting_source_ids: List[str] = Field(default_factory=list)
    contradicting_source_ids: List[str] = Field(default_factory=list)
    status: str = "unsupported"  # supported | partially_supported | unsupported
    confidence: float = 0.0
    reason: str = ""


class ScoreComponent(BaseModel):
    """One weighted component of the evidence-quality score."""

    name: str
    weight: float
    raw_score: float
    weighted_score: float
    rationale: str = ""


class EvidenceScorecard(BaseModel):
    """The full, visible scoring breakdown for a run."""

    run_id: str
    formula_version: str = "v1"
    components: List[ScoreComponent] = Field(default_factory=list)
    total_score: float = 0.0
    decision_thresholds: dict = Field(default_factory=dict)
    hard_gate_overrides: List[str] = Field(default_factory=list)


class GateResult(BaseModel):
    """The outcome of one deterministic hard decision gate."""

    gate: str
    status: str  # pass | fail
    reason: str = ""


class RunManifest(BaseModel):
    """The run conditions needed to reproduce / compare a run."""

    run_id: str = ""
    product_slug: str = ""
    created_at_utc: str = ""
    completed_at_utc: str = ""
    repo_commit_hash: Optional[str] = None
    model_name: str = ""
    model_provider: str = ""
    run_status: str = "success"  # success | partial | failed
    hypothesis: dict = Field(default_factory=dict)
    phases_requested: List[str] = Field(default_factory=list)
    phase_prompts_sent: List[dict] = Field(default_factory=list)
    raw_source_count_before_validation: int = 0
    validated_source_count: int = 0
    rejected_source_count: int = 0
    buyer_language_artifact_count: int = 0
    claim_count: int = 0
    final_decision: str = ""
    evidence_quality_score: float = 0.0
    fatal_gaps: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)
