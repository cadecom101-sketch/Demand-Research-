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
from demand_research.connectors import build_default_registry
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

        # Evidence-connector audit: record up front which connectors/capabilities
        # this run can use. Capability reporting only — unavailable connectors
        # are skipped safely (no prompt, no crash) and never fabricated.
        self._connector_registry = build_default_registry(
            screenshot_capture=getattr(self, "_screenshotter", None),
        )
        self._connector_audit = self._connector_registry.audit()
        self._connector_summary = self._connector_registry.summary()
        if recorder is not None:
            recorder.write_connector_registry(
                self._connector_audit, self._connector_summary
            )

        brief = DemandBrief(
            product_hypothesis=hypothesis,
            decision=Decision.PARK,
            decision_reasoning="Workflow in progress",
            evidence_quality_score=0.0,
            target_member=normalize_target_member(target_member)[0],
        )

        try:
            completed: List[PhaseResult] = []
            # Diagnostic continuation: once a hard-gate phase (2/3/4) fails, the
            # remaining phases still run, but every result they produce is
            # downgraded to DIAGNOSTIC_ONLY — collected for future cycles, never
            # able to satisfy a gate, feed the score, or raise the verdict.
            # Phase 1 failure still stops the run: with no category signal at
            # all there is nothing meaningful to observe downstream.
            diagnostic_trigger: Optional[dict] = None

            def _enter_diagnostic(failed: PhaseResult) -> None:
                nonlocal diagnostic_trigger
                if diagnostic_trigger is None:
                    diagnostic_trigger = {
                        "phase": failed.phase_number,
                        "phase_name": failed.phase_name,
                        "reason": failed.reason,
                    }

            def _maybe_diagnostic(result: PhaseResult) -> PhaseResult:
                if diagnostic_trigger is None:
                    return result
                details = dict(result.details or {})
                details["diagnostic_underlying_status"] = result.status.value
                result.details = details
                result.status = PhaseStatus.DIAGNOSTIC_ONLY
                result.diagnostic_reason = (
                    f"Phase {diagnostic_trigger['phase']} "
                    f"({diagnostic_trigger['phase_name']}) failed first; this phase ran "
                    "in diagnostic-only continuation. Its evidence is recorded for "
                    "future cycles but cannot satisfy any gate or raise the verdict "
                    "in this run."
                )
                return result

            phase1_result = await self.phase1.run(hypothesis, recorder=recorder)
            brief.phase_1_result = phase1_result
            completed.append(phase1_result)
            if phase1_result.status == PhaseStatus.FAIL:
                return self._finalize(brief, hypothesis, completed, recorder)

            phase2_result = await self.phase2.run(hypothesis, phase1_result, recorder=recorder)
            brief.phase_2_result = phase2_result
            completed.append(phase2_result)
            if phase2_result.status == PhaseStatus.FAIL:
                _enter_diagnostic(phase2_result)

            phase3_result = _maybe_diagnostic(
                await self.phase3.run(hypothesis, phase2_result, recorder=recorder)
            )
            brief.phase_3_result = phase3_result
            completed.append(phase3_result)
            if phase3_result.status == PhaseStatus.FAIL:
                _enter_diagnostic(phase3_result)

            phase4_result = _maybe_diagnostic(
                await self.phase4.run(hypothesis, phase3_result, recorder=recorder)
            )
            brief.phase_4_result = phase4_result
            completed.append(phase4_result)
            if phase4_result.status == PhaseStatus.FAIL:
                _enter_diagnostic(phase4_result)

            phase5_result = _maybe_diagnostic(
                await self.phase5.run(hypothesis, phase4_result, recorder=recorder)
            )
            brief.phase_5_result = phase5_result
            completed.append(phase5_result)
            return self._finalize(
                brief, hypothesis, completed, recorder,
                diagnostic_trigger=diagnostic_trigger,
            )

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
        diagnostic_trigger: Optional[dict] = None,
    ) -> DemandBrief:
        # Research/tool-failure detection FIRST: a phase whose raw research
        # reports a tool/search failure (or whose searches errored) marks the
        # run partial BEFORE run_status is read, so the existing `tool_failure`
        # hard gate caps the verdict at PARK. Detection only ever lowers — a
        # tool failure is neither evidence for nor against demand. Diagnostic
        # phases are scanned too: a tool failure during diagnostic continuation
        # still means the observation was incomplete.
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

        # Partition: gating phases (PASS/FAIL — they decide) vs diagnostic
        # phases (DIAGNOSTIC_ONLY — recorded, never decide). Everything that
        # feeds a gate, a claim, or the score sees GATING evidence only, so the
        # verdict is identical to a run that stopped at the failed phase.
        diagnostic_results = [
            p for p in phase_results if p.status == PhaseStatus.DIAGNOSTIC_ONLY
        ]
        gating_results = [
            p for p in phase_results if p.status != PhaseStatus.DIAGNOSTIC_ONLY
        ]
        diagnostic_phases = {p.phase_number for p in diagnostic_results}

        phase_by_num = {p.phase_number: p for p in gating_results}
        all_sources = [s for p in gating_results for s in p.sources_collected]

        diagnostic_continuation: Optional[dict] = None
        if diagnostic_trigger is not None:
            diagnostic_continuation = {
                "triggered_by_phase": diagnostic_trigger["phase"],
                "triggered_by_phase_name": diagnostic_trigger["phase_name"],
                "trigger_reason": diagnostic_trigger["reason"],
                "diagnostic_phases": sorted(diagnostic_phases),
                "diagnostic_source_counts": {
                    f"phase_{p.phase_number}": len(p.sources_collected)
                    for p in diagnostic_results
                },
                "governance": (
                    "Diagnostic-only evidence is durably recorded for future cycles "
                    "but is excluded from every gate, claim, score, and the E1 "
                    "review in this run. The failed hard gate stands; the verdict "
                    "is the same as if the run had stopped at the failed phase."
                ),
            }
            if recorder is not None:
                recorder.write_diagnostic_continuation(diagnostic_continuation)

        # Write the source ledger + buyer-language artifacts and get graded sources.
        graded, phase_cards = self._build_graded_sources(
            brief, phase_results, recorder, diagnostic_phases=diagnostic_phases
        )
        graded_gating = [g for g in graded if not g.get("diagnostic_only")]

        run_id = recorder.run_id if recorder is not None else "local"

        # Structured phase artifacts (Patches 4/5/6): price bands, competitor
        # map, missing-mechanism gap — derived from the graded sources so they
        # link by source_id.
        price_bands, price_lead_count, directional = self._build_price_bands(
            run_id, brief, phase_cards, recorder, diagnostic_phases=diagnostic_phases
        )
        competitors = self._build_competitor_map(
            run_id, brief, phase_cards, recorder, diagnostic_phases=diagnostic_phases
        )
        missing_mechanism = self._build_missing_mechanism(
            run_id, brief, phase_cards, recorder, diagnostic_phases=diagnostic_phases
        )

        # Gate/claim/score inputs: GATING evidence only. Diagnostic-only records
        # stay in the artifacts (marked) but never reach a gate this run.
        price_bands_gating = [r for r in price_bands if not r.get("diagnostic_only")]
        directional_gating = [r for r in directional if not r.get("diagnostic_only")]
        competitors_gating = [r for r in competitors if not r.get("diagnostic_only")]
        missing_mechanism_gating = (
            {} if 5 in diagnostic_phases else missing_mechanism
        )

        signals = compute_signals(phase_by_num, run_status)
        if diagnostic_continuation is not None:
            signals["diagnostic_trigger_phase"] = diagnostic_continuation["triggered_by_phase"]
            signals["diagnostic_phase_numbers"] = diagnostic_continuation["diagnostic_phases"]
        outcome = self.decision_engine.decide(hypothesis, gating_results, all_sources, signals)

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
            run_id, brief, graded_gating,
            min_count=self.decision_engine.min_sources_per_phase,
            price_artifacts=price_bands_gating,
            price_lead_count=price_lead_count,
            directional=directional_gating,
            diagnostic_phases=diagnostic_phases,
        )

        # E1 review: nine recording-readiness gates -> verdict + recording/B2/B3
        # status. Approval is additionally coupled to the conservative TEST bar
        # so it can never be easier than the existing engine's TEST.
        e1 = evaluate_e1_review(
            hypothesis=hypothesis, brief=brief, signals=signals, graded=graded_gating,
            price_bands=price_bands_gating, directional=directional_gating,
            competitors=competitors_gating,
            missing_mechanism=missing_mechanism_gating, target_member=self._target_member,
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
        if brief.phase_3_result is not None and len(price_bands_gating) == 0:
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
            diagnostic_phases=diagnostic_phases,
        )
        diagnostics = build_decision_diagnostics(
            brief=brief, e1_artifact=e1_artifact, signals=signals,
            phase_results=phase_results, rejected_summary=rejected_summary,
            run_status=run_status, extraction_error_count=extraction_errors,
            extraction_salvage_count=salvage_events,
            tool_failures=tool_failures, belief_state=belief_state,
            diagnostic_continuation=diagnostic_continuation,
        )
        next_plan = build_next_evidence_plan(
            hypothesis=hypothesis, brief=brief, e1_artifact=e1_artifact, signals=signals,
            tool_failures=tool_failures,
            phases_not_run=diagnostics.get("phases_not_run", []),
            diagnostic_phases=diagnostic_phases,
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
        diagnostic_note = None
        if diagnostic_continuation is not None:
            diagnostic_note = (
                f"Phases {diagnostic_continuation['diagnostic_phases']} ran in "
                "diagnostic-only continuation after Phase "
                f"{diagnostic_continuation['triggered_by_phase']} failed. Their "
                "evidence is excluded from this score and from every gate; it is "
                "recorded for future cycles only."
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
                "diagnostic_continuation_note": diagnostic_note,
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
            "diagnostic_continuation": diagnostic_continuation,
            "connector_registry": {
                "connectors": getattr(self, "_connector_audit", []),
                "summary": getattr(self, "_connector_summary", {}),
            },
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
        diagnostic_phases: Optional[set] = None,
    ) -> tuple[List[dict], dict]:
        """Grade every validated source, write the ledger + buyer-language
        artifacts (when recording), and return (graded_index, phase_cards).

        `phase_cards` maps phase number -> [(source_id, SourceCard)] so the
        structured phase artifacts can link by source_id. Phase 5 is synthesis
        and reuses Phase 4's sources, so it is not re-ledgered. Sources from
        `diagnostic_phases` are ledgered with diagnostic_only=True — preserved
        for future cycles, excluded from every gate/claim this run."""
        diagnostic_phases = diagnostic_phases or set()
        graded: List[dict] = []
        phase_cards: dict[int, list] = {}
        counter = 0
        for phase in phase_results:
            num = phase.phase_number
            if num == 5:
                continue
            etype = PHASE_EVIDENCE_TYPE.get(num, "other")
            is_diagnostic = num in diagnostic_phases
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
                        diagnostic_only=is_diagnostic,
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
                graded.append({
                    "source_id": sid, "phase": num, "grade": grade,
                    "evidence_type": etype, "diagnostic_only": is_diagnostic,
                })
        return graded, phase_cards

    # ------------------------------------------------------------------ #
    # Structured phase artifacts
    # ------------------------------------------------------------------ #
    def _build_price_bands(self, run_id, brief, phase_cards, recorder,
                           diagnostic_phases: Optional[set] = None):
        """Return (verified_price_records, lead_count, directional_records).

        Only verified competitor prices are written to price_band_artifacts.jsonl.
        Competitor leads (no observed price) and general market-pricing articles
        (directional) are NOT written there and do NOT count as price artifacts.
        Records from a diagnostic-only Phase 3 carry diagnostic_only=True.
        """
        diagnostic = 3 in (diagnostic_phases or set())
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
            if diagnostic:
                record["diagnostic_only"] = True
            if kind == "directional":
                record["directional"] = True
                directional.append(record)
                continue
            # Verified priced competitor — the only kind that is a price artifact.
            records.append(record)
            if recorder is not None:
                recorder.log_price_band(record)
        return records, lead_count, directional

    def _build_competitor_map(self, run_id, brief, phase_cards, recorder,
                              diagnostic_phases: Optional[set] = None) -> List[dict]:
        diagnostic = 4 in (diagnostic_phases or set())
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
            if diagnostic:
                record["diagnostic_only"] = True
            records.append(record)
            if recorder is not None:
                recorder.log_competitor(record)
        return records

    def _build_missing_mechanism(self, run_id, brief, phase_cards, recorder,
                                 diagnostic_phases: Optional[set] = None) -> dict:
        if brief.phase_5_result is None:
            return {}
        mech = dict(brief.phase_5_result.details or {})
        mech["run_id"] = run_id
        # Link to the competitor sources the gap was judged against.
        mech["supporting_source_ids"] = [sid for sid, _ in phase_cards.get(4, [])]
        mech.setdefault("status", "unsupported")
        mech.setdefault("gap_statement", brief.phase_5_result.findings)
        if 5 in (diagnostic_phases or set()):
            mech["diagnostic_only"] = True
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
