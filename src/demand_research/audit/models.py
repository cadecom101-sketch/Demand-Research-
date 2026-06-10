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
    # True when this source was collected during diagnostic-only continuation
    # (after an earlier hard-gate phase failed). It is preserved for future
    # cycles but can never satisfy a gate or claim in the run that wrote it.
    diagnostic_only: bool = False
    # Screenshot audit documentation. Screenshots never satisfy a gate and
    # never invalidate evidence; they mark audit completeness only.
    screenshot_filename: Optional[str] = None
    screenshot_sha256: Optional[str] = None
    # audit_complete       -> screenshot captured for this accepted source;
    # audit_incomplete     -> capture was available but no screenshot exists;
    # capture_unavailable  -> capture not configured/available this run.
    audit_status: str = "capture_unavailable"


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
    """The full, visible scoring breakdown for a run.

    The clarity fields below explain what the score does and does not mean.
    They change nothing about how the score is computed or used: the score
    alone never approves E1, hard gates always dominate, and a partial /
    tool-failed run's score is explicitly NOT a clean market score.
    """

    run_id: str
    formula_version: str = "v1"
    components: List[ScoreComponent] = Field(default_factory=list)
    total_score: float = 0.0
    decision_thresholds: dict = Field(default_factory=dict)
    hard_gate_overrides: List[str] = Field(default_factory=list)
    # Scorecard clarity (diagnostic text/labels; never inputs to any decision).
    score_meaning: str = (
        "Weighted evidence-quality score (0–1) over the ACCEPTED evidence mix. "
        "It measures evidence quality, not market truth, and by itself it never "
        "approves E1 — every hard gate must independently pass on real documented "
        "evidence."
    )
    hard_gate_caps: List[str] = Field(default_factory=list)
    partial_run_caps: Optional[str] = None
    why_score_does_not_approve: Optional[str] = None
    evidence_not_observed_due_to_tooling: List[str] = Field(default_factory=list)
    clean_vs_partial: str = "clean"
    # Set when later phases ran in diagnostic-only continuation: their evidence
    # is excluded from this score and from every gate (reporting only).
    diagnostic_continuation_note: Optional[str] = None


class GateResult(BaseModel):
    """The outcome of one deterministic hard decision gate."""

    gate: str
    status: str  # pass | fail
    reason: str = ""


class E1ReviewGate(BaseModel):
    """The outcome of one of the nine E1 demand-brief review gates.

    Serialized into `e1_review_gates.json`. Status is upper-case PASS/FAIL to
    match the review artifact schema, and every gate links the source IDs it
    was judged against (empty for workflow-boundary gates)."""

    gate_id: str
    status: str = "FAIL"  # PASS | FAIL
    reason: str = ""
    supporting_source_ids: List[str] = Field(default_factory=list)


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

    # E1 demand-brief review (primitive/member hierarchy + recording readiness).
    primitive_name: str = ""
    target_member: str = ""
    excluded_members: List[str] = Field(default_factory=list)
    current_state: str = ""
    candidate_state: str = ""
    review_verdict: str = ""
    recording_status: str = ""
    b2_acceptance_status: str = ""
    b3_status: str = ""
    public_execution_status: str = ""
    e1_review_gates_path: str = ""

    extra: dict = Field(default_factory=dict)
