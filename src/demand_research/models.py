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


class Decision(str, Enum):
    """Enum for final product decision."""
    BUILD = "BUILD"
    TEST = "TEST"
    REVISE = "REVISE"
    PARK = "PARK"
    KILL = "KILL"


class EvidenceStage(str, Enum):
    """Where the idea sits on the E0 -> E1 -> post-E1 ladder.

    The demand brief usually decides whether an idea earns the next cheapest
    TEST (E1-candidate), not whether it should be fully built.
    """
    E0 = "E0"                      # unproven; stays E0 (KILL / PARK / REVISE)
    E1_CANDIDATE = "E1_CANDIDATE"  # earns a cheap external test (TEST)
    POST_E1 = "POST_E1"            # past validation; build-justified (BUILD)


# Decision -> evidence stage. BUILD is deliberately the only POST_E1 mapping.
DECISION_TO_STAGE = {
    Decision.KILL: EvidenceStage.E0,
    Decision.PARK: EvidenceStage.E0,
    Decision.REVISE: EvidenceStage.E0,
    Decision.TEST: EvidenceStage.E1_CANDIDATE,
    Decision.BUILD: EvidenceStage.POST_E1,
}


def evidence_stage_for(decision: Decision) -> EvidenceStage:
    """Map a final decision to its evidence stage."""
    return DECISION_TO_STAGE.get(decision, EvidenceStage.E0)


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
    evidence_stage: EvidenceStage = EvidenceStage.E0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

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
