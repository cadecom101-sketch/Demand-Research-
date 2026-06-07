"""Orchestrator agent that manages the 5-phase research workflow."""

import logging
from typing import Optional
from demand_research.models import (
    ProductHypothesis,
    DemandBrief,
    Decision,
    PhaseResult,
    PhaseStatus,
)
from demand_research.decision_engine import DecisionEngine
from demand_research.agents.phase_agents import (
    Phase1Agent,
    Phase2Agent,
    Phase3Agent,
    Phase4Agent,
    Phase5Agent,
)

logger = logging.getLogger(__name__)


class ResearchOrchestrator:
    """Manages all 5 phases of demand research workflow."""

    def __init__(self):
        self.phase1 = Phase1Agent()
        self.phase2 = Phase2Agent()
        self.phase3 = Phase3Agent()
        self.phase4 = Phase4Agent()
        self.phase5 = Phase5Agent()
        self.decision_engine = DecisionEngine()

    async def run_workflow(self, hypothesis: ProductHypothesis) -> DemandBrief:
        """
        Execute complete 5-phase demand research workflow.
        Stops at phase failure and routes to decision engine.
        """
        logger.info(f"Starting research workflow for: {hypothesis.product_name}")

        brief = DemandBrief(
            product_hypothesis=hypothesis,
            decision=Decision.PARK,
            decision_reasoning="Workflow in progress",
            evidence_quality_score=0.0,
        )

        # Phase 1: Signal Discovery
        logger.info("Running Phase 1: Signal Discovery")
        phase1_result = await self.phase1.run(hypothesis)
        brief.phase_1_result = phase1_result

        if phase1_result.status == PhaseStatus.FAIL:
            logger.warning("Phase 1 failed: No market signals found")
            decision, reasoning, quality = self.decision_engine.decide(
                hypothesis, [phase1_result], []
            )
            brief.decision = decision
            brief.decision_reasoning = reasoning
            brief.evidence_quality_score = quality
            return brief

        # Phase 2: Buyer Language
        logger.info("Running Phase 2: Buyer Language Mining")
        phase2_result = await self.phase2.run(hypothesis, phase1_result)
        brief.phase_2_result = phase2_result

        if phase2_result.status == PhaseStatus.FAIL:
            logger.warning("Phase 2 failed: No buyer language found")
            all_sources = phase1_result.sources_collected + phase2_result.sources_collected
            decision, reasoning, quality = self.decision_engine.decide(
                hypothesis, [phase1_result, phase2_result], all_sources
            )
            brief.decision = decision
            brief.decision_reasoning = reasoning
            brief.evidence_quality_score = quality
            return brief

        # Phase 3: Price Band Mapping
        logger.info("Running Phase 3: Price Band Mapping")
        phase3_result = await self.phase3.run(hypothesis, phase2_result)
        brief.phase_3_result = phase3_result

        if phase3_result.status == PhaseStatus.FAIL:
            logger.warning("Phase 3 failed: No competitor pricing found")
            all_sources = (
                phase1_result.sources_collected
                + phase2_result.sources_collected
                + phase3_result.sources_collected
            )
            decision, reasoning, quality = self.decision_engine.decide(
                hypothesis, [phase1_result, phase2_result, phase3_result], all_sources
            )
            brief.decision = decision
            brief.decision_reasoning = reasoning
            brief.evidence_quality_score = quality
            return brief

        # Phase 4: Competitor Presence
        logger.info("Running Phase 4: Competitor Presence Analysis")
        phase4_result = await self.phase4.run(hypothesis, phase3_result)
        brief.phase_4_result = phase4_result

        if phase4_result.status == PhaseStatus.FAIL:
            logger.warning("Phase 4 failed: Could not analyze competitors")
            all_sources = (
                phase1_result.sources_collected
                + phase2_result.sources_collected
                + phase3_result.sources_collected
                + phase4_result.sources_collected
            )
            decision, reasoning, quality = self.decision_engine.decide(
                hypothesis, [phase1_result, phase2_result, phase3_result, phase4_result], all_sources
            )
            brief.decision = decision
            brief.decision_reasoning = reasoning
            brief.evidence_quality_score = quality
            return brief

        # Phase 5: Missing Mechanism Gap
        logger.info("Running Phase 5: Missing-Mechanism Gap Analysis")
        phase5_result = await self.phase5.run(hypothesis, phase4_result)
        brief.phase_5_result = phase5_result

        # All phases complete: Make final decision
        all_phases = [phase1_result, phase2_result, phase3_result, phase4_result, phase5_result]
        all_sources = brief.all_sources()

        decision, reasoning, quality = self.decision_engine.decide(hypothesis, all_phases, all_sources)
        brief.decision = decision
        brief.decision_reasoning = reasoning
        brief.evidence_quality_score = quality

        logger.info(
            f"Workflow complete. Decision: {decision.value} (quality: {quality:.2f})"
        )
        return brief
