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
from demand_research.e1_review import (
    build_e1_claims,
    evaluate_e1_review,
    next_step_for,
    normalize_target_member,
    what_would_change_for,
)
from demand_research.models import (
    Decision,
    DemandBrief,
    PhaseResult,
    PhaseStatus,
    ProductHypothesis,
)
from demand_research.next_evidence import (
    build_decision_diagnostics,
    build_next_evidence_plan,
    render_next_evidence_markdown,
)
from demand_research.tool_failure import build_belief_state, detect_run_tool_failures
from demand_research.research.query_planner import build_search_plan
from demand_research.research.claude_researcher import ClaudeResearcher, ResearchUnavailableError
from demand_research.agents.phase_agents import (
    Phase1Agent,
    Phase2Agent,
    Phase3Agent,
    Phase4Agent,
    Phase5Agent,
    classify_price_source,
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
        # Defaults for the E1 demand-brief workflow (Base member only).
        self._workflow_mode = "e1-demand-brief"
        self._target_member = "Base"

    async def run_workflow(
        self,
        hypothesis: ProductHypothesis,
        recorder: Optional[object] = None,
        *,
        target_member: str = "Base",
        workflow_mode: str = "e1-demand-brief",
    ) -> DemandBrief:
        """Execute the workflow, stopping at the first phase failure.

        When a `recorder` is supplied, every run produces a durable
        `runs/{run_id}/` truth-layer directory. Without one, the audit bundle is
        still computed in-memory (so the brief carries the scorecard/gates) but
        no files are written.

        `target_member` selects which member under the primitive is being
        validated. This repo validates the Base member only; Member A/B fail the
        scope-lock gate. `workflow_mode` defaults to `e1-demand-brief`, in which
        BUILD is disabled and the output is an E1 recording-readiness verdict.
        """
        logger.info("Starting research workflow for: %s", hypothesis.product_name)
        self._workflow_mode = workflow_mode
        self._target_member = target_member

        brief = DemandBrief(
            product_hypothesis=hypothesis,
            decision=Decision.PARK,
            decision_reasoning="Workflow in progress",
            evidence_quality_score=0.0,
            target_member=normalize_target_member(target_member)[0],
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
        # Research/tool-failure detection FIRST: a phase whose raw research
        # reports a tool/search failure (or whose searches errored) marks the
        # run partial BEFORE run_status is read, so the existing `tool_failure`
        # hard gate caps the verdict at PARK. Detection only ever lowers — a
        # tool failure is neither evidence for nor against demand.
        search_failures = (
            recorder.search_failures_by_phase() if recorder is not None else {}
        )
        tool_failures = detect_run_tool_failures(phase_results, search_failures)
        if recorder is not None:
            for failure in tool_failures:
                recorder.log_research_tool_failure(failure)
            run_status = recorder.run_status
        else:
            run_status = "partial" if tool_failures else "success"

        phase_by_num = {p.phase_number: p for p in phase_results}
        all_sources = brief.all_sources()

        # Write the source ledger + buyer-language artifacts and get graded sources.
        graded, phase_cards = self._build_graded_sources(brief, phase_results, recorder)

        run_id = recorder.run_id if recorder is not None else "local"

        # Structured phase artifacts (Patches 4/5/6): price bands, competitor
        # map, missing-mechanism gap — derived from the graded sources so they
        # link by source_id.
        price_bands, price_lead_count, directional = self._build_price_bands(
            run_id, brief, phase_cards, recorder
        )
        competitors = self._build_competitor_map(run_id, brief, phase_cards, recorder)
        missing_mechanism = self._build_missing_mechanism(run_id, brief, phase_cards, recorder)

        signals = compute_signals(phase_by_num, run_status)
        outcome = self.decision_engine.decide(hypothesis, phase_results, all_sources, signals)

        # BUILD is disabled in the E1 demand-brief workflow: a five-phase desk
        # research run can never justify BUILD. Cap it to TEST before it becomes
        # a review verdict. The conservative gates/score are NOT changed.
        capped_decision = outcome.decision
        build_capped = (
            self._workflow_mode == "e1-demand-brief" and outcome.decision == Decision.BUILD
        )
        if build_capped:
            capped_decision = Decision.TEST

        brief.decision = capped_decision
        brief.decision_reasoning = outcome.reasoning + (
            " BUILD is disabled in the E1 demand-brief workflow; capped to TEST "
            "(E1_APPROVED_TO_RECORD candidate). Recording remains external."
            if build_capped else ""
        )
        brief.evidence_quality_score = outcome.score
        brief.run_status = run_status
        brief.fatal_gaps = outcome.fatal_gaps

        # The price-band claim is computed from verified price artifacts ONLY,
        # never from generic Phase 3 competitor-lead source cards.
        claims = build_claims(
            run_id, brief, graded,
            min_count=self.decision_engine.min_sources_per_phase,
            price_artifacts=price_bands,
            price_lead_count=price_lead_count,
            directional=directional,
        )

        # E1 review: nine recording-readiness gates -> verdict + recording/B2/B3
        # status. Approval is additionally coupled to the conservative TEST bar
        # so it can never be easier than the existing engine's TEST.
        e1 = evaluate_e1_review(
            hypothesis=hypothesis, brief=brief, signals=signals, graded=graded,
            price_bands=price_bands, directional=directional, competitors=competitors,
            missing_mechanism=missing_mechanism, target_member=self._target_member,
            decision_cleared_test=capped_decision in (Decision.TEST, Decision.BUILD),
        )
        brief.evidence_stage = e1.evidence_stage
        brief.review_verdict = e1.review_verdict
        brief.target_member = e1.target_member
        brief.e1_review = {
            **e1.to_artifact(run_id),
            "revenue_os_payload_draft": e1.revenue_os_payload_draft,
        }

        # E1 claims 6–9 (fit + workflow-boundary) extend the ledger.
        claims = claims + build_e1_claims(
            run_id, e1, start_index=len(claims),
            fit_gate_passed=e1.gate_passed("fit_to_andrew_authored_primitive"),
            scope_gate_passed=e1.gate_passed("scope_lock"),
            fit_source_ids=e1.gate_source_ids("fit_to_andrew_authored_primitive"),
        )

        what_not_proves = _what_not_proves(claims)
        if brief.phase_3_result is not None and len(price_bands) == 0:
            what_not_proves += (
                " Exact competitor price bands were not established "
                f"(0 verified competitor prices captured; {price_lead_count} unpriced leads)."
            )

        # Decision diagnostics + next-evidence plan (additive; explain/guide
        # only — they never change a gate, threshold, or verdict). The search
        # plan is the diversified evidence-seeking plan from the query planner.
        e1_artifact = e1.to_artifact(run_id)
        rejected_summary = (
            recorder.rejected_summary() if recorder is not None
            else {"total": 0, "by_reason": {}, "examples": []}
        )
        extraction_errors = recorder.extraction_error_count if recorder is not None else 0
        salvage_events = recorder.extraction_salvage_count if recorder is not None else 0
        search_plan = build_search_plan(hypothesis)
        belief_state = build_belief_state(
            phase_results=phase_results, e1_artifact=e1_artifact,
            signals=signals, tool_failures=tool_failures,
        )
        diagnostics = build_decision_diagnostics(
            brief=brief, e1_artifact=e1_artifact, signals=signals,
            phase_results=phase_results, rejected_summary=rejected_summary,
            run_status=run_status, extraction_error_count=extraction_errors,
            extraction_salvage_count=salvage_events,
            tool_failures=tool_failures, belief_state=belief_state,
        )
        next_plan = build_next_evidence_plan(
            hypothesis=hypothesis, brief=brief, e1_artifact=e1_artifact, signals=signals,
            tool_failures=tool_failures,
            phases_not_run=diagnostics.get("phases_not_run", []),
        )
        next_plan_md = render_next_evidence_markdown(next_plan)

        # Scorecard clarity: label whether the score came from a clean or a
        # partial/tool-failed observation, and restate why score alone never
        # approves. Pure reporting — the formula, gates, and thresholds are
        # untouched.
        if tool_failures:
            clean_vs_partial = "partial_tool_failure"
            partial_caps = (
                "This run had detected research/tool failure(s); the score is NOT a "
                "clean market score. The tool_failure hard gate caps a partial run at "
                "PARK regardless of score."
            )
        elif run_status in ("partial", "failed"):
            clean_vs_partial = "partial_extraction"
            partial_caps = (
                "This run was partial (extraction parse/salvage events); the score is "
                "NOT a clean market score. The tool_failure hard gate caps a partial "
                "run at PARK regardless of score."
            )
        else:
            clean_vs_partial = "clean"
            partial_caps = None
        tooling_starved_gates = diagnostics.get(
            "gates_not_fully_evaluable_due_to_tooling", []
        )

        audit_core = {
            "evidence_stage": brief.evidence_stage.value,
            "scorecard": {
                "formula_version": FORMULA_VERSION,
                "components": [c.model_dump(mode="json") for c in outcome.components],
                "total_score": outcome.score,
                "decision_thresholds": outcome.thresholds,
                "hard_gate_overrides": outcome.overrides,
                "hard_gate_caps": outcome.overrides,
                "partial_run_caps": partial_caps,
                "why_score_does_not_approve": (
                    "Approval requires every E1 gate to pass on real documented "
                    "evidence. A high score with a failed hard gate (e.g. missing "
                    "verbatim buyer pain) or a partial/tool-failed observation can "
                    "never approve; source count and screenshots never compensate "
                    "for a missing hard gate."
                ),
                "evidence_not_observed_due_to_tooling": tooling_starved_gates,
                "clean_vs_partial": clean_vs_partial,
            },
            "gates": [g.model_dump(mode="json") for g in outcome.gates],
            "fatal_gaps": outcome.fatal_gaps,
            "run_status": run_status,
            "next_experiment": next_step_for(e1),
            "what_proves": _what_proves(claims),
            "what_not_proves": what_not_proves,
            "what_would_change": what_would_change_for(e1),
            "price_leads": price_lead_count,
            "directional": directional,
            # E1 review surface (read by the markdown renderer + manifest).
            "e1_review": e1_artifact,
            "revenue_os_payload_draft": e1.revenue_os_payload_draft,
            "primitive_name": e1.primitive_name,
            "target_member": e1.target_member,
            "excluded_members": e1.excluded_members,
            # Decision-making intelligence (diagnostics + planning).
            "search_plan": search_plan,
            "decision_diagnostics": diagnostics,
            "next_evidence_plan": next_plan,
        }

        if recorder is not None:
            recorder.write_search_plan(search_plan)
            recorder.write_decision_diagnostics(diagnostics)
            recorder.write_next_evidence_plan(next_plan, next_plan_md)
            # Write the E1 gates artifact (pass AND fail runs) before finalize so
            # the manifest can summarise the review state.
            recorder.write_e1_review_gates(e1.to_artifact(run_id))
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
                "price_bands": price_bands,
                "competitors": competitors,
                "missing_mechanism": missing_mechanism,
                "e1_review_gates": e1.to_artifact(run_id),
                "artifact_paths": {},
            }

        logger.info(
            "Workflow complete. Verdict: %s -> stage %s (quality: %.2f, status: %s)",
            brief.review_verdict, brief.evidence_stage.value, brief.evidence_quality_score, run_status,
        )
        return brief

    def _build_graded_sources(
        self,
        brief: DemandBrief,
        phase_results: List[PhaseResult],
        recorder: Optional[object],
    ) -> tuple[List[dict], dict]:
        """Grade every validated source, write the ledger + buyer-language
        artifacts (when recording), and return (graded_index, phase_cards).

        `phase_cards` maps phase number -> [(source_id, SourceCard)] so the
        structured phase artifacts can link by source_id. Phase 5 is synthesis
        and reuses Phase 4's sources, so it is not re-ledgered."""
        graded: List[dict] = []
        phase_cards: dict[int, list] = {}
        counter = 0
        for phase in phase_results:
            num = phase.phase_number
            if num == 5:
                continue
            etype = PHASE_EVIDENCE_TYPE.get(num, "other")
            phase_cards.setdefault(num, [])
            for card in phase.sources_collected:
                counter += 1
                grade, conf = grade_card(etype, card)
                # Make the card self-describing too.
                card.evidence_type = etype
                card.evidence_grade = grade
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
                phase_cards[num].append((sid, card))
                graded.append({"source_id": sid, "phase": num, "grade": grade, "evidence_type": etype})
        return graded, phase_cards

    # ------------------------------------------------------------------ #
    # Structured phase artifacts
    # ------------------------------------------------------------------ #
    def _build_price_bands(self, run_id, brief, phase_cards, recorder):
        """Return (verified_price_records, lead_count, directional_records).

        Only verified competitor prices are written to price_band_artifacts.jsonl.
        Competitor leads (no observed price) and general market-pricing articles
        (directional) are NOT written there and do NOT count as price artifacts.
        """
        records: List[dict] = []
        directional: List[dict] = []
        lead_count = 0
        if brief.phase_3_result is None:
            return records, lead_count, directional
        for sid, card in phase_cards.get(3, []):
            kind = classify_price_source(card)
            if kind == "lead":
                lead_count += 1
                continue
            d = card.details or {}
            record = {
                "run_id": run_id, "source_id": sid,
                "competitor_name": card.source_name, "url": str(card.url),
                "date_observed": card.date_observed.isoformat(), "platform": card.platform,
                "price_observed": card.price_observed,
                "currency": d.get("currency", "USD"),
                "product_type": d.get("product_type", card.platform),
                "what_it_promises": d.get("what_it_promises", card.what_this_proves),
                "features_included": card.competitor_features_observed or d.get("features_included", []),
                "screenshot_filename": card.screenshot_filename,
                "price_tier": _tier_for(card.price_observed),
            }
            if kind == "directional":
                record["directional"] = True
                directional.append(record)
                continue
            # Verified priced competitor — the only kind that is a price artifact.
            records.append(record)
            if recorder is not None:
                recorder.log_price_band(record)
        return records, lead_count, directional

    def _build_competitor_map(self, run_id, brief, phase_cards, recorder) -> List[dict]:
        records: List[dict] = []
        if brief.phase_4_result is None:
            return records
        dims = (
            "demand_validation", "authorship_evidence", "build_readiness_gate",
            "listing_readiness_gate", "fee_stress_logic", "post_launch_decision_loop",
        )
        for sid, card in phase_cards.get(4, []):
            d = card.details or {}
            teardown = d.get("teardown") or {}
            record = {
                "run_id": run_id, "source_id": sid,
                "competitor_name": card.source_name, "url": str(card.url),
                "date_observed": card.date_observed.isoformat(),
                "price": card.price_observed,
                "target_buyer": d.get("target_buyer", ""),
                "product_format": d.get("product_type", card.platform),
                "main_promise": d.get("main_promise", ""),
                "features_included": card.competitor_features_observed or d.get("features_included", []),
                "what_it_structurally_does": d.get("what_it_structurally_does", ""),
                "what_it_does_not_appear_to_govern": d.get("what_it_does_not_govern", card.gap_note or ""),
            }
            for dim in dims:
                record[f"{dim}_score"] = teardown.get(dim, "unknown")
            records.append(record)
            if recorder is not None:
                recorder.log_competitor(record)
        return records

    def _build_missing_mechanism(self, run_id, brief, phase_cards, recorder) -> dict:
        if brief.phase_5_result is None:
            return {}
        mech = dict(brief.phase_5_result.details or {})
        mech["run_id"] = run_id
        # Link to the competitor sources the gap was judged against.
        mech["supporting_source_ids"] = [sid for sid, _ in phase_cards.get(4, [])]
        mech.setdefault("status", "unsupported")
        mech.setdefault("gap_statement", brief.phase_5_result.findings)
        if recorder is not None:
            recorder.write_missing_mechanism(mech)
        return mech


# ---------------------------------------------------------------------- #
# Deterministic narrative helpers (no model calls; fully reproducible)
# ---------------------------------------------------------------------- #
def _tier_for(price: float) -> str:
    """Low $0–$12 / Mid $15–$24.99 (here <$29) / Premium $29+ (workflow doc)."""
    if price <= 12:
        return "low"
    if price < 29:
        return "mid"
    return "premium"


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
