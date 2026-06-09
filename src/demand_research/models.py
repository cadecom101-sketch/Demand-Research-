"""Pydantic models for demand research workflow."""

from datetime import datetime
from typing import Optional, List
from enum import Enum
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, HttpUrl


class PhaseStatus(str, Enum):
    """Enum for phase pass/fail status."""
    PASS = "PASS"
    FAIL = "FAIL"


# --------------------------------------------------------------------------- #
# Primitive / member hierarchy (this repo validates the FIRST member only).
#
#   Governed Solo-Operator Launch OS            (PARENT primitive — Andrew-authored)
#   └── Base — Retail Instant-Download OS       (FIRST member — validated here)
#       └── first E1 demand brief being created now
#
# Member A and Member B are deferred at E0 and are NOT validated by this repo.
# --------------------------------------------------------------------------- #
PRIMITIVE_NAME = "Governed Solo-Operator Launch OS"
BASE_MEMBER_NAME = "Base — Retail Instant-Download OS"
MEMBER_A_NAME = "Member A — Recurring Code & Integration Toolsmith"
MEMBER_B_NAME = "Member B — High-Touch Productized Build Operator"
EXCLUDED_MEMBERS = [MEMBER_A_NAME, MEMBER_B_NAME]


class Decision(str, Enum):
    """Internal score-tier decision from the evidence engine.

    In the `e1-demand-brief` workflow BUILD is disabled (a five-phase desk
    research run can never justify BUILD); it is capped to TEST before it
    becomes a review verdict. The enum is retained because the scorecard and
    conservative hard gates are expressed in these terms.
    """
    BUILD = "BUILD"
    TEST = "TEST"
    REVISE = "REVISE"
    PARK = "PARK"
    KILL = "KILL"


class EvidenceStage(str, Enum):
    """Where the member sits on the recording ladder.

    The repo may reach E1_APPROVED_TO_RECORD at most. E1_RECORDED happens
    OUTSIDE this repo, in Revenue OS, and may only ever be an imported status.
    """
    E0_AUTHORED_CAPTURED = "E0_AUTHORED_CAPTURED"      # authored draft, not recorded
    E1_CANDIDATE = "E1_CANDIDATE"                      # candidate brief under review
    E1_APPROVED_TO_RECORD = "E1_APPROVED_TO_RECORD"    # passed E1 review; ready to record
    E1_RECORDED = "E1_RECORDED"                        # EXTERNAL only (Revenue OS)


class ReviewVerdict(str, Enum):
    """The E1 demand-brief review verdict (replaces BUILD/TEST as the output)."""
    E1_APPROVED_TO_RECORD = "E1_APPROVED_TO_RECORD"
    E1_REVISE_BEFORE_RECORDING = "E1_REVISE_BEFORE_RECORDING"
    E1_PARK = "E1_PARK"
    E1_KILL = "E1_KILL"


class RecordingStatus(str, Enum):
    """Whether the member is ready to be recorded into Revenue OS."""
    NOT_RECORDED = "NOT_RECORDED"
    READY_TO_RECORD = "READY_TO_RECORD"
    RECORDED = "RECORDED"  # EXTERNAL only (Revenue OS)


class B2Status(str, Enum):
    """B2 acceptance. ACCEPTED happens outside this repo, in Revenue OS."""
    NOT_MET = "NOT_MET"
    READY_FOR_ACCEPTANCE = "READY_FOR_ACCEPTANCE"
    ACCEPTED_OUTSIDE_REPO = "ACCEPTED_OUTSIDE_REPO"  # EXTERNAL only


class B3Status(str, Enum):
    """B3 expansion. This repo never unlocks B3; it stays LOCKED."""
    LOCKED = "LOCKED"
    ELIGIBLE_AFTER_RECORDING = "ELIGIBLE_AFTER_RECORDING"


# Internal score-tier decision -> review verdict. BUILD is treated exactly like
# TEST here because BUILD is disabled/capped in the demand-brief workflow.
DECISION_TO_VERDICT = {
    Decision.KILL: ReviewVerdict.E1_KILL,
    Decision.PARK: ReviewVerdict.E1_PARK,
    Decision.REVISE: ReviewVerdict.E1_REVISE_BEFORE_RECORDING,
    Decision.TEST: ReviewVerdict.E1_APPROVED_TO_RECORD,
    Decision.BUILD: ReviewVerdict.E1_APPROVED_TO_RECORD,
}


def evidence_stage_for(verdict: ReviewVerdict) -> EvidenceStage:
    """Map an E1 review verdict to its evidence-ladder stage.

    Only an approved verdict advances past the authored-capture stage, and only
    as far as E1_APPROVED_TO_RECORD — recording itself is external.
    """
    if verdict == ReviewVerdict.E1_APPROVED_TO_RECORD:
        return EvidenceStage.E1_APPROVED_TO_RECORD
    return EvidenceStage.E0_AUTHORED_CAPTURED


class ProductHypothesis(BaseModel):
    """Product idea input before research."""
    product_id: UUID = Field(default_factory=uuid4)
    product_name: str
    target_buyer: str
    buyer_job: str
    product_format: str
    primary_channel: str
    missing_mechanism_hypothesis: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SourceCard(BaseModel):
    """Single research source with evidence."""
    source_number: int
    source_name: str
    url: HttpUrl
    date_observed: datetime
    platform: str
    search_phrase_used: Optional[str] = None
    price_observed: Optional[float] = None
    buyer_language_captured: Optional[str] = None
    competitor_features_observed: Optional[List[str]] = None
    what_this_proves: str
    what_this_does_not_prove: str
    screenshot_filename: Optional[str] = None
    gap_note: Optional[str] = None
    is_direct_quote: Optional[bool] = None
    # Evidence classification (also written to source_ledger.jsonl). Populated
    # at ledger time by the deterministic grader; carried here so a source card
    # is self-describing.
    evidence_type: Optional[str] = None
    evidence_grade: Optional[str] = None
    # Phase-specific structured extras (price band fields, competitor teardown).
    details: Optional[dict] = None


class PhaseResult(BaseModel):
    """Result from a single research phase."""
    phase_number: int
    phase_name: str
    status: PhaseStatus
    sources_collected: List[SourceCard]
    findings: str
    reason: str
    pass_condition: str
    # Phase-specific structured output (e.g. Phase 5 missing-mechanism gap).
    details: Optional[dict] = None


class DemandBrief(BaseModel):
    """Complete demand brief with all phases and decision."""
    product_hypothesis: ProductHypothesis
    phase_1_result: Optional[PhaseResult] = None
    phase_2_result: Optional[PhaseResult] = None
    phase_3_result: Optional[PhaseResult] = None
    phase_4_result: Optional[PhaseResult] = None
    phase_5_result: Optional[PhaseResult] = None
    decision: Decision
    decision_reasoning: str
    evidence_quality_score: float = Field(ge=0.0, le=1.0)
    evidence_stage: EvidenceStage = EvidenceStage.E0_AUTHORED_CAPTURED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # E1 demand-brief review (primitive/member hierarchy + recording readiness).
    # The repo validates ONE member (Base) under the primitive and produces a
    # reviewable verdict; it never records into Revenue OS or unlocks B3.
    primitive_name: str = PRIMITIVE_NAME
    target_member: str = BASE_MEMBER_NAME
    review_verdict: Optional[str] = None
    e1_review: Optional[dict] = None

    # Audit / provenance (populated by the orchestrator + RunRecorder). The
    # `audit` bundle carries the scorecard, hard-gate results, claim ledger,
    # and search/rejected summaries that the markdown brief surfaces.
    run_id: Optional[str] = None
    run_status: str = "success"  # success | partial | failed
    fatal_gaps: List[str] = Field(default_factory=list)
    audit: Optional[dict] = None

    def all_sources(self) -> List[SourceCard]:
        """Collect all sources from all phases."""
        sources = []
        for phase in [self.phase_1_result, self.phase_2_result, self.phase_3_result,
                      self.phase_4_result, self.phase_5_result]:
            if phase:
                sources.extend(phase.sources_collected)
        return sources
