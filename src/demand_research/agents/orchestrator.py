"""Orchestrator agent that manages the 5-phase research workflow.

Beyond running the phases and routing to the decision engine, the orchestrator
drives the audit/truth layer: it threads the RunRecorder through every phase
(search + rejected-source logging), then after the phases writes the source
ledger, buyer-language artifacts, claim ledger, scorecard, and manifest.
"""

import logging
from typing import List, Optional

from demand_research.audit.claims import build_claims
from demand_research.audit.grading import (
    PHASE_EVIDENCE_TYPE,
    classify_pain_type,
    compute_signals,
    grade_card,
)
from demand_research.audit.models import BuyerLanguageArtifact, SourceLedgerEntry
from demand_research.decision_engine import FORMULA_VERSION, DecisionEngine
from demand_research.models import (
    Decision,
    DemandBrief,
    PhaseResult,
    PhaseStatus,
    ProductHypothesis,
)
from demand_research.research.claude_researcher import ClaudeResearcher, ResearchUnavailableError
from demand_research.agents.phase_agents import (
    Phase1Agent,
    Phase2Agent,
    Phase3Agent,
    Phase4Agent,
    Phase5Agent,
)

logger = logging.getLogger(__name__)


class ResearchOrchestrator:
    """Manages all 5 phases of demand research workflow plus the audit trail."""

    def __init__(self, researcher: Optional[ClaudeResearcher] = None):
        from demand_research.config import settings

        self._model_name = settings.anthropic_model
        shared = researcher or ClaudeResearcher(
            model=settings.anthropic_model, effort=settings.anthropic_effort
        )
        self.phase1 = Phase1Agent(researcher=shared)
        self.phase2 = Phase2Agent(researcher=shared)
        self.phase3 = Phase3Agent(researcher=shared)
        self.phase4 = Phase4Agent(researcher=shared)
        self.phase5 = Phase5Agent(researcher=shared)
        self.decision_engine = DecisionEngine()

    async def run_workflow(
        self, hypothesis: ProductHypothesis, recorder: Optional[object] = None
    ) -> DemandBrief:
        """Execute the workflow, stopping at the first phase failure.

        When a `recorder` is supplied, every run produces a durable
        `runs/{run_id}/` truth-layer directory. Without one, the audit bundle is
        still computed in-memory (so the brief carries the scorecard/gates) but
        no files are written.
        """
        logger.info("Starting research workflow for: %s", hypothesis.product_name)

        brief = DemandBrief(
            product_hypothesis=hypothesis,
            decision=Decision.PARK,
            decision_reasoning="Workflow in progress",
            evidence_quality_score=0.0,
        )

        try:
            completed: List[PhaseResult] = []

            phase1_result = await self.phase1.run(hypothesis, recorder=recorder)
            brief.phase_1_result = phase1_result
            completed.append(phase1_result)
            if phase1_result.status == PhaseStatus.FAIL:
                return self._finalize(brief, hypothesis, completed, recorder)

            phase2_result = await self.phase2.run(hypothesis, phase1_result, recorder=recorder)
            brief.phase_2_result = phase2_result
            completed.append(phase2_result)
            if phase2_result.status == PhaseStatus.FAIL:
                return self._finalize(brief, hypothesis, completed, recorder)

            phase3_result = await self.phase3.run(hypothesis, phase2_result, recorder=recorder)
            brief.phase_3_result = phase3_result
            completed.append(phase3_result)
            if phase3_result.status == PhaseStatus.FAIL:
                return self._finalize(brief, hypothesis, completed, recorder)

            phase4_result = await self.phase4.run(hypothesis, phase3_result, recorder=recorder)
            brief.phase_4_result = phase4_result
            completed.append(phase4_result)
            if phase4_result.status == PhaseStatus.FAIL:
                return self._finalize(brief, hypothesis, completed, recorder)

            phase5_result = await self.phase5.run(hypothesis, phase4_result, recorder=recorder)
            brief.phase_5_result = phase5_result
            completed.append(phase5_result)
            return self._finalize(brief, hypothesis, completed, recorder)

        except ResearchUnavailableError as exc:
            # Infrastructure failure — never a fake verdict. Record what we can.
            if recorder is not None:
                recorder.write_failed_manifest(f"Research unavailable: {exc}")
            raise

    # ------------------------------------------------------------------ #
    # Finalization: decision + full audit trail
    # ------------------------------------------------------------------ #
    def _finalize(
        self,
        brief: DemandBrief,
        hypothesis: ProductHypothesis,
        phase_results: List[PhaseResult],
        recorder: Optional[object],
    ) -> DemandBrief:
        run_status = recorder.run_status if recorder is not None else "success"
        phase_by_num = {p.phase_number: p for p in phase_results}
        all_sources = brief.all_sources()

        # Write the source ledger + buyer-language artifacts and get graded sources.
        graded = self._build_graded_sources(brief, phase_results, recorder)

        signals = compute_signals(phase_by_num, run_status)
        outcome = self.decision_engine.decide(hypothesis, phase_results, all_sources, signals)

        brief.decision = outcome.decision
        brief.decision_reasoning = outcome.reasoning
        brief.evidence_quality_score = outcome.score
        brief.run_status = run_status
        brief.fatal_gaps = outcome.fatal_gaps

        run_id = recorder.run_id if recorder is not None else "local"
        claims = build_claims(run_id, brief, graded, min_count=self.decision_engine.min_sources_per_phase)

        audit_core = {
            "scorecard": {
                "formula_version": FORMULA_VERSION,
                "components": [c.model_dump(mode="json") for c in outcome.components],
                "total_score": outcome.score,
                "decision_thresholds": outcome.thresholds,
                "hard_gate_overrides": outcome.overrides,
            },
            "gates": [g.model_dump(mode="json") for g in outcome.gates],
            "fatal_gaps": outcome.fatal_gaps,
            "run_status": run_status,
            "next_experiment": _next_experiment(outcome.decision, hypothesis),
            "what_proves": _what_proves(claims),
            "what_not_proves": _what_not_proves(claims),
            "what_would_change": _what_would_change(outcome),
        }

        if recorder is not None:
            recorder.finalize(brief, audit_core, claims)
        else:
            # In-memory bundle (no files). Markdown still renders audit sections.
            brief.run_id = run_id
            brief.audit = {
                **audit_core,
                "run_id": run_id,
                "claims": [c.model_dump(mode="json") for c in claims],
                "search_summary": {"total": 0, "results_found": 0, "zero_results": 0,
                                   "tool_error": 0, "rate_limited": 0},
                "rejected_summary": {"total": 0, "by_reason": {}, "examples": []},
                "buyer_artifacts": [],
                "artifact_paths": {},
            }

        logger.info(
            "Workflow complete. Decision: %s (quality: %.2f, status: %s)",
            brief.decision.value, brief.evidence_quality_score, run_status,
        )
        return brief

    def _build_graded_sources(
        self,
        brief: DemandBrief,
        phase_results: List[PhaseResult],
        recorder: Optional[object],
    ) -> List[dict]:
        """Grade every validated source, write the ledger + buyer-language
        artifacts (when recording), and return the graded-source index used by
        the claim ledger. Phase 5 is synthesis and reuses Phase 4's sources, so
        it is not re-ledgered."""
        graded: List[dict] = []
        counter = 0
        for phase in phase_results:
            num = phase.phase_number
            if num == 5:
                continue
            etype = PHASE_EVIDENCE_TYPE.get(num, "other")
            for card in phase.sources_collected:
                counter += 1
                grade, conf = grade_card(etype, card)
                if recorder is not None:
                    sid = recorder.next_source_id()
                    entry = SourceLedgerEntry(
                        run_id=recorder.run_id, source_id=sid,
                        phase_id=f"phase_{num}", phase_name=phase.phase_name,
                        url=str(card.url), title=card.source_name, platform=card.platform,
                        date_observed=card.date_observed.isoformat(),
                        search_query=card.search_phrase_used or "",
                        retrieved_excerpt=(card.what_this_proves or "")[:300],
                        buyer_language_captured=card.buyer_language_captured or "",
                        evidence_type=etype, evidence_grade=grade,
                        claim_supported=card.what_this_proves or "",
                        claim_not_supported=card.what_this_does_not_prove or "",
                        confidence=conf,
                    )
                    recorder.log_source(entry)
                    if card.is_direct_quote and card.buyer_language_captured:
                        recorder.log_buyer_language(BuyerLanguageArtifact(
                            run_id=recorder.run_id, artifact_id=recorder.next_artifact_id(),
                            phase_id=f"phase_{num}", source_id=sid, url=str(card.url),
                            platform=card.platform, quote=card.buyer_language_captured,
                            speaker_type="seller",
                            pain_type=classify_pain_type(card.buyer_language_captured),
                            strength="strong", why_it_matters=card.what_this_proves or "",
                            what_it_does_not_prove=card.what_this_does_not_prove or "",
                        ))
                else:
                    sid = f"S{counter:03d}"
                graded.append({"source_id": sid, "phase": num, "grade": grade, "evidence_type": etype})
        return graded


# ---------------------------------------------------------------------- #
# Deterministic narrative helpers (no model calls; fully reproducible)
# ---------------------------------------------------------------------- #
def _next_experiment(decision: Decision, hypothesis: ProductHypothesis) -> str:
    if decision == Decision.BUILD:
        return ("Build a minimal version and list it on "
                f"{hypothesis.primary_channel}; instrument conversion from view to purchase.")
    if decision == Decision.TEST:
        return ("Run a fake-door / pre-order test (landing page or single listing) to measure "
                "real purchase intent before committing build time.")
    if decision == Decision.REVISE:
        return ("Tighten the target buyer and the missing-mechanism statement, then re-run the "
                "buyer-language phase with broader, problem-first search queries.")
    if decision == Decision.PARK:
        return ("Re-run later with broader buyer-language searches and seek behavioral (Grade A) "
                "evidence before investing.")
    return ("Do not pursue as-is. Only revisit if the hypothesis (buyer, job, or mechanism) "
            "materially changes.")


def _what_proves(claims: list) -> str:
    supported = [c.claim for c in claims if c.status == "supported"]
    if not supported:
        return "No claim reached 'supported' status against its required evidence grade."
    return " ".join(f"- {c}" for c in supported)


def _what_not_proves(claims: list) -> str:
    weak = [f"{c.claim} ({c.status})" for c in claims if c.status != "supported"]
    if not weak:
        return "All assessed claims were supported at their required grade."
    return " ".join(f"- {w}" for w in weak)


def _what_would_change(outcome) -> str:
    if outcome.overrides:
        return ("Clearing these capped gates would raise the verdict: "
                + "; ".join(outcome.overrides) + ".")
    if outcome.decision in (Decision.BUILD, Decision.TEST):
        return "A failed behavioral/fake-door test would lower the verdict back to REVISE/PARK."
    return ("Stronger evidence — more verbatim buyer-language artifacts (Grade B) or behavioral "
            "purchase signals (Grade A) — would raise the verdict.")
