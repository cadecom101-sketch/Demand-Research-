"""Decision engine for determining BUILD/REVISE/PARK/KILL."""

from typing import List
from demand_research.models import (
    ProductHypothesis,
    PhaseResult,
    SourceCard,
    Decision,
    PhaseStatus,
)


class DecisionEngine:
    """Determines final decision based on research phases."""

    def __init__(self, min_sources_per_phase: int = 3):
        self.min_sources_per_phase = min_sources_per_phase

    def decide(
        self,
        hypothesis: ProductHypothesis,
        phase_results: List[PhaseResult],
        all_sources: List[SourceCard],
    ) -> tuple[Decision, str, float]:
        """
        Determine BUILD/REVISE/PARK/KILL decision.
        Returns: (decision, reasoning, evidence_quality_score)
        """
        # Check which phases passed
        passed_phases = [p for p in phase_results if p.status == PhaseStatus.PASS]
        failed_phase = next((p for p in phase_results if p.status == PhaseStatus.FAIL), None)

        # Phase 1 failure: No market signals
        if failed_phase and failed_phase.phase_number == 1:
            return (
                Decision.KILL,
                "Phase 1 failed: No real market signals found. No evidence of buyer activity "
                "or category existence.",
                0.1,
            )

        # Phase 2 failure: No buyer language
        if failed_phase and failed_phase.phase_number == 2:
            return (
                Decision.PARK,
                "Phase 2 failed: Could not find direct buyer language or pain points. "
                "Market may exist but buyer pain is unclear. Revisit later with different search terms.",
                0.25,
            )

        # Phase 3 failure: No pricing data
        if failed_phase and failed_phase.phase_number == 3:
            return (
                Decision.REVISE,
                "Phase 3 failed: Could not map competitor pricing. This may indicate the market "
                "is nascent or the category is too broad. Revise product definition.",
                0.3,
            )

        # Phase 4 failure: No competitors
        if failed_phase and failed_phase.phase_number == 4:
            return (
                Decision.REVISE,
                "Phase 4 failed: Could not identify 3+ real competitors. Either the market "
                "is too new or the product concept is too vague. Narrow the scope.",
                0.35,
            )

        # Phase 5 failure: No structural gap
        if failed_phase and failed_phase.phase_number == 5:
            return (
                Decision.REVISE,
                "Phase 5 failed: Could not identify a structural missing mechanism. "
                "The product may be a feature add-on or aesthetic re-skin. Define what "
                "structural decision it forces that competitors don't.",
                0.4,
            )

        # All phases passed: Calculate evidence quality and decide
        if len(passed_phases) == 5:
            quality_score = self._calculate_quality_score(phase_results, all_sources)

            # Check for strong signals across all dimensions
            phase_1 = phase_results[0]
            phase_2 = phase_results[1]
            phase_3 = phase_results[2]
            phase_4 = phase_results[3]
            phase_5 = phase_results[4]

            # BUILD: Strong evidence across all phases
            if quality_score >= 0.75:
                return (
                    Decision.BUILD,
                    f"All 5 phases passed with strong evidence (quality: {quality_score:.2f}). "
                    f"Real market signals ({len(phase_1.sources_collected)} sources), "
                    f"buyer pain identified ({len(phase_2.sources_collected)} sources), "
                    f"viable price band found, {len(phase_4.sources_collected)} competitors analyzed, "
                    f"and structural gap named. Market is validated enough to build.",
                    quality_score,
                )

            # REVISE: Moderate evidence but gaps exist
            if 0.5 <= quality_score < 0.75:
                return (
                    Decision.REVISE,
                    f"All 5 phases passed but evidence quality is moderate ({quality_score:.2f}). "
                    f"Market exists but may be too broad, price band unclear, or competitors "
                    f"mostly track/organize rather than govern. Narrow target buyer, clarify "
                    f"the structural decision forcing mechanism, or validate pricing with more sources.",
                    quality_score,
                )

            # PARK: Weak evidence in all phases
            if quality_score < 0.5:
                return (
                    Decision.PARK,
                    f"Evidence quality is weak ({quality_score:.2f}). All phases technically passed "
                    f"but with minimal sources or weak signals. Keep researching. This market may be "
                    f"emerging; revisit in 3-6 months with more data.",
                    quality_score,
                )

        # Default to PARK if somehow we reached here
        return (
            Decision.PARK,
            "Research incomplete or inconclusive. Insufficient evidence to commit to build.",
            0.3,
        )

    def _calculate_quality_score(
        self, phase_results: List[PhaseResult], all_sources: List[SourceCard]
    ) -> float:
        """
        Calculate evidence quality score (0-1).
        Factors: source count, source diversity, date recency, quote authenticity.
        """
        score = 0.0

        # Base: minimum 1 point for each passed phase
        passed_count = sum(1 for p in phase_results if p.status == PhaseStatus.PASS)
        score += (passed_count / 5) * 0.4

        # Source count: up to 0.3 points
        min_sources = 3
        max_sources = 15
        actual_sources = len(all_sources)
        if actual_sources >= min_sources:
            source_score = min(1.0, (actual_sources - min_sources) / (max_sources - min_sources))
            score += source_score * 0.3

        # Recent sources: up to 0.2 points
        recent_threshold_days = 90
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(days=recent_threshold_days)
        recent_sources = sum(1 for s in all_sources if s.date_observed >= cutoff)
        if actual_sources > 0:
            recent_ratio = recent_sources / actual_sources
            score += recent_ratio * 0.2

        # Direct quotes (not composite): up to 0.1 points
        direct_quotes = sum(
            1 for s in all_sources
            if s.is_direct_quote is True or (s.buyer_language_captured and s.is_direct_quote is not False)
        )
        if actual_sources > 0:
            quote_ratio = direct_quotes / actual_sources
            score += min(quote_ratio * 0.1, 0.1)

        return min(1.0, score)
