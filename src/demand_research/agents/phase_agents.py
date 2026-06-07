"""Phase-specific agents for each research phase."""

import logging
from typing import Optional
from demand_research.models import (
    ProductHypothesis,
    PhaseResult,
    PhaseStatus,
    SourceCard,
)
from demand_research.research.evidence_validator import EvidenceValidator
from demand_research.research.source_collector import SourceCollector

logger = logging.getLogger(__name__)


class BasePhaseAgent:
    """Base class for phase agents."""

    def __init__(self, phase_number: int, phase_name: str, min_sources: int = 3):
        self.phase_number = phase_number
        self.phase_name = phase_name
        self.min_sources = min_sources
        self.validator = EvidenceValidator()
        self.collector = SourceCollector()

    async def run(self, *args, **kwargs) -> PhaseResult:
        """Override in subclass."""
        raise NotImplementedError


class Phase1Agent(BasePhaseAgent):
    """Phase 1: Signal Discovery - Check if market signals exist."""

    def __init__(self):
        super().__init__(
            phase_number=1,
            phase_name="Signal Discovery",
            min_sources=3,
        )

    async def run(self, hypothesis: ProductHypothesis) -> PhaseResult:
        """
        Search for market signals showing the product category exists.
        Pass: Find 3+ signals (real listings, searches, categories)
        """
        logger.info(f"Phase 1: Searching for market signals - {hypothesis.product_name}")

        sources = []
        product_type = self.collector.detect_product_type(
            hypothesis.product_name,
            hypothesis.target_buyer,
            hypothesis.primary_channel,
        )
        logger.info(f"Detected product type: {product_type}")

        # In a full implementation, this would call Claude Agent SDK with
        # WebFetch/WebSearch tools. For now, we return structure.
        # The actual implementation will be filled in during Phase 2.

        findings = (
            f"Market signal discovery in progress for {hypothesis.product_name}. "
            f"Product type detected: {product_type}. "
            f"Searching across Etsy, Gumroad, Notion Marketplace, Google Trends."
        )

        status = PhaseStatus.PASS if sources or True else PhaseStatus.FAIL  # Placeholder

        return PhaseResult(
            phase_number=self.phase_number,
            phase_name=self.phase_name,
            status=status,
            sources_collected=sources,
            findings=findings,
            reason="Phase 1 structure ready; agent integration pending",
            pass_condition="3+ real market signals with URLs and dates",
        )


class Phase2Agent(BasePhaseAgent):
    """Phase 2: Buyer Language Mining - Extract real pain points."""

    def __init__(self):
        super().__init__(
            phase_number=2,
            phase_name="Buyer Language Mining",
            min_sources=3,
        )

    async def run(
        self, hypothesis: ProductHypothesis, phase1_result: PhaseResult
    ) -> PhaseResult:
        """
        Mine buyer language from reviews, forums, comments.
        Pass: Find 3+ direct buyer quotes or observations
        """
        logger.info(f"Phase 2: Mining buyer language - {hypothesis.product_name}")

        sources = []
        findings = (
            f"Buyer language mining in progress. Searching reviews, Reddit, "
            f"YouTube comments, and forums for {hypothesis.buyer_job} pain points."
        )

        status = PhaseStatus.PASS if sources or True else PhaseStatus.FAIL  # Placeholder

        return PhaseResult(
            phase_number=self.phase_number,
            phase_name=self.phase_name,
            status=status,
            sources_collected=sources,
            findings=findings,
            reason="Phase 2 structure ready; agent integration pending",
            pass_condition="3+ real buyer language artifacts (direct quotes with attribution)",
        )


class Phase3Agent(BasePhaseAgent):
    """Phase 3: Price Band Mapping - Map competitor pricing."""

    def __init__(self):
        super().__init__(
            phase_number=3,
            phase_name="Price Band Mapping",
            min_sources=3,
        )

    async def run(
        self, hypothesis: ProductHypothesis, phase1_result: PhaseResult
    ) -> PhaseResult:
        """
        Find competitor products and their prices.
        Pass: Map 3+ competitor prices across low/mid/premium bands
        """
        logger.info(f"Phase 3: Mapping price bands - {hypothesis.product_name}")

        sources = []
        findings = (
            f"Price band mapping in progress. Searching for {self.min_sources} "
            f"competitor products on Etsy, Notion Marketplace, and Gumroad."
        )

        status = PhaseStatus.PASS if sources or True else PhaseStatus.FAIL  # Placeholder

        return PhaseResult(
            phase_number=self.phase_number,
            phase_name=self.phase_name,
            status=status,
            sources_collected=sources,
            findings=findings,
            reason="Phase 3 structure ready; agent integration pending",
            pass_condition="3+ competitor prices with URLs, features, and screenshots",
        )


class Phase4Agent(BasePhaseAgent):
    """Phase 4: Competitor Presence - Analyze what competitors do structurally."""

    def __init__(self):
        super().__init__(
            phase_number=4,
            phase_name="Competitor Presence",
            min_sources=3,
        )

    async def run(
        self, hypothesis: ProductHypothesis, phase3_result: PhaseResult
    ) -> PhaseResult:
        """
        Analyze competitor products' structural mechanisms.
        Pass: Create 10-field maps + 6-dim teardowns for 3+ competitors
        """
        logger.info(f"Phase 4: Analyzing competitor presence - {hypothesis.product_name}")

        sources = []
        findings = (
            f"Competitor analysis in progress. Evaluating structural mechanisms of "
            f"products from Phase 3: demand validation, authorship gates, build readiness, "
            f"listing readiness, fee stress logic, post-launch review loops."
        )

        status = PhaseStatus.PASS if sources or True else PhaseStatus.FAIL  # Placeholder

        return PhaseResult(
            phase_number=self.phase_number,
            phase_name=self.phase_name,
            status=status,
            sources_collected=sources,
            findings=findings,
            reason="Phase 4 structure ready; agent integration pending",
            pass_condition="3+ competitors with 10-field maps + 6-dimension structural teardowns",
        )


class Phase5Agent(BasePhaseAgent):
    """Phase 5: Missing-Mechanism Gap - Identify structural opportunity."""

    def __init__(self):
        super().__init__(
            phase_number=5,
            phase_name="Missing-Mechanism Gap",
            min_sources=0,  # This phase synthesizes vs. collects
        )

    async def run(
        self, hypothesis: ProductHypothesis, phase4_result: PhaseResult
    ) -> PhaseResult:
        """
        Synthesize competitor analysis to identify missing structural mechanism.
        Pass: Name a specific structural gap (not feature-based)
        """
        logger.info(f"Phase 5: Identifying missing mechanism - {hypothesis.product_name}")

        sources = []
        findings = (
            f"Gap analysis in progress. Comparing hypothesis mechanism "
            f"('{hypothesis.missing_mechanism_hypothesis}') against competitor structures "
            f"from Phase 4. Validating gap is structural, not aesthetic."
        )

        status = PhaseStatus.PASS if sources or True else PhaseStatus.FAIL  # Placeholder

        return PhaseResult(
            phase_number=self.phase_number,
            phase_name=self.phase_name,
            status=status,
            sources_collected=sources,
            findings=findings,
            reason="Phase 5 structure ready; agent integration pending",
            pass_condition="Specific, structural missing mechanism identified (not feature-based)",
        )
