"""Generate the human-readable markdown demand brief.

The brief is the *summary* of a run; the `runs/{run_id}/` folder is the audit
trail. This renderer surfaces the audit layer (score breakdown, hard gates,
claim ledger, search + rejected summaries, artifact paths) so a reader can see
exactly how the verdict was reached and where to verify it.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Optional

from demand_research.models import DemandBrief, PhaseStatus
from demand_research.config import settings

logger = logging.getLogger(__name__)


def render_markdown(brief: DemandBrief) -> str:
    """Render a complete demand brief (including audit sections) to markdown."""
    return _Renderer(brief).render()


class MarkdownGenerator:
    """Writes the rendered brief into the git-tracked `briefs/` directory."""

    def __init__(self):
        self.output_dir = settings.briefs_dir

    def generate(self, brief: DemandBrief) -> Path:
        product_id = str(brief.product_hypothesis.product_id).split("-")[0]
        slug = brief.product_hypothesis.product_name.lower().replace(" ", "-")
        output_path = self.output_dir / f"{product_id}-{slug}-demand-brief.md"
        output_path.write_text(render_markdown(brief), encoding="utf-8")
        logger.info("Markdown brief generated: %s", output_path)
        return output_path


class _Renderer:
    def __init__(self, brief: DemandBrief):
        self.brief = brief
        self.audit: dict = brief.audit or {}

    # ------------------------------------------------------------------ #
    def _e1(self) -> dict:
        return self.audit.get("e1_review", {}) or (self.brief.e1_review or {})

    def render(self) -> str:
        b = self.brief
        L: list[str] = []
        L.append(f"# Demand Brief (E1 Review): {b.target_member}")
        L.append(f"Generated: {b.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        L.append("")
        L += self._status_block()
        L += self._executive_summary()
        L += self._hypothesis()
        L += self._primitive_hierarchy()
        L += self._scope_lock()
        L += self._states()
        L += self._review_verdict()
        L += self._recording_readiness()
        L += self._b2_b3_boundary()
        L += self._phase_results()
        L += self._score_breakdown()
        L += self._hard_gates()
        L += self._e1_gate_results()
        L += self._buyer_language()
        L += self._price_band()
        L += self._competitor_map()
        L += self._missing_mechanism()
        L += self._fit_to_primitive()
        L += self._claim_ledger()
        L += self._search_summary()
        L += self._rejected_summary()
        L += self._section("What This Proves", self.audit.get("what_proves", "Not assessed."))
        L += self._section("What This Does NOT Prove", self.audit.get("what_not_proves", "Not assessed."))
        L += self._section("What Would Change This Verdict", self.audit.get("what_would_change", "Not assessed."))
        L += self._decision_diagnostics()
        L += self._next_evidence_plan()
        L += self._revenue_payload()
        L += self._section("Next Recommended Step", self.audit.get("next_experiment", "Not assessed."))
        L += self._all_sources()
        L += self._audit_artifacts()
        L.append("---")
        L.append(f"_Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}_")
        return "\n".join(L)

    # ------------------------------------------------------------------ #
    def _status_block(self) -> list[str]:
        """The required, scannable E1 review status block."""
        e1 = self._e1()
        excluded = ", ".join(e1.get("excluded_members", [])) or "Member A, Member B"
        rows = [
            ("Primitive", e1.get("primitive_name", "")),
            ("Target Member", e1.get("target_member", self.brief.target_member)),
            ("Excluded Members", excluded),
            ("Current State", e1.get("current_state", "E0_AUTHORED_CAPTURED")),
            ("Candidate State", e1.get("candidate_state", "E1_CANDIDATE")),
            ("Review Verdict", e1.get("review_verdict", self.brief.review_verdict or "")),
            ("Recording Status", e1.get("recording_status", "NOT_RECORDED")),
            ("B2 Acceptance", e1.get("b2_acceptance_status", "NOT_MET")),
            ("B3 Status", e1.get("b3_status", "LOCKED")),
            ("Public Execution Status", e1.get("public_execution_status", "NONE")),
        ]
        L = ["```text"]
        for label, value in rows:
            if label == "B2 Acceptance" and value == "NOT_MET":
                value = "NOT_MET until Revenue OS rows are created"
            L.append(f"{label}: {value}")
        L.append("```")
        L.append("")
        return L

    # ------------------------------------------------------------------ #
    def _executive_summary(self) -> list[str]:
        b = self.brief
        e1 = self._e1()
        fatal = b.fatal_gaps or self.audit.get("fatal_gaps", [])
        fatal_str = "; ".join(fatal) if fatal else "None"
        return [
            "## Executive Summary",
            f"- **Review Verdict:** {e1.get('review_verdict', b.review_verdict or 'n/a')}",
            f"- **Evidence Stage:** {b.evidence_stage.value}",
            f"- **Recording Status:** {e1.get('recording_status', 'NOT_RECORDED')}",
            f"- **Decision (internal score tier; BUILD disabled):** {b.decision.value}",
            f"- **Evidence Quality:** {b.evidence_quality_score:.2%}",
            f"- **Run ID:** {b.run_id or 'n/a'}",
            f"- **Run Status:** {b.run_status}",
            f"- **Fatal Gaps:** {fatal_str}",
            f"- **Main Reason:** {b.decision_reasoning}",
            "",
        ]

    def _primitive_hierarchy(self) -> list[str]:
        e1 = self._e1()
        excluded = e1.get("excluded_members", []) or []
        L = [
            "## Primitive / Member Hierarchy",
            "",
            f"**Primitive (parent):** {e1.get('primitive_name', '')}",
            f"**First member (validated here):** {e1.get('target_member', self.brief.target_member)}",
            "",
            "```text",
            e1.get("primitive_name", "Governed Solo-Operator Launch OS"),
            f"└── {e1.get('target_member', self.brief.target_member)}",
            "    └── first E1 demand brief being created now",
            "```",
            "",
            "**Deferred members (NOT validated by this repo, parked at E0):**",
        ]
        for m in excluded:
            L.append(f"- {m}")
        L.append("")
        L.append("_This repo validates the FIRST member only. It does not validate the parent "
                 "primitive universe, and it does not review Member A or Member B._")
        L.append("")
        return L

    def _scope_lock(self) -> list[str]:
        e1 = self._e1()
        gate = next((g for g in e1.get("gates", []) if g.get("gate_id") == "scope_lock"), {})
        L = [
            "## Target Member and Scope Lock",
            "",
            f"**Target Member:** {e1.get('target_member', self.brief.target_member)}",
            f"**Scope Lock:** {gate.get('status', 'n/a')} — {gate.get('reason', '')}",
            "",
            "_E1_REVIEW is for the Base demand brief under the Governed Solo-Operator Launch OS "
            "primitive. It does not validate the whole primitive._",
            "",
        ]
        return L

    def _states(self) -> list[str]:
        e1 = self._e1()
        return [
            "## Current State / Candidate State",
            "",
            f"- **Current State:** {e1.get('current_state', 'E0_AUTHORED_CAPTURED')} "
            "(Andrew-authored draft; not recorded in Revenue OS; B3 not unlocked).",
            f"- **Candidate State:** {e1.get('candidate_state', 'E1_CANDIDATE')} "
            "(the brief under review).",
            "",
            "Target progression: `E0_AUTHORED_CAPTURED → E1_CANDIDATE → E1_APPROVED_TO_RECORD → "
            "E1_RECORDED`. This repo can reach at most **E1_APPROVED_TO_RECORD**; "
            "**E1_RECORDED** happens outside this repo in Revenue OS.",
            "",
        ]

    def _review_verdict(self) -> list[str]:
        e1 = self._e1()
        verdict = e1.get("review_verdict", self.brief.review_verdict or "n/a")
        meaning = {
            "E1_APPROVED_TO_RECORD": "Passed E1 review; ready for human approval and external "
                                     "recording into Revenue OS.",
            "E1_REVISE_BEFORE_RECORDING": "Close, but one or more gates must be addressed before "
                                          "recording.",
            "E1_PARK": "Insufficient documented desk evidence to become an E1 candidate yet.",
            "E1_KILL": "Do not record — evidence integrity failed.",
        }.get(verdict, "")
        return [
            "## E1 Review Verdict",
            "",
            f"**{verdict}** — {meaning}",
            "",
            "_This is a recording-readiness verdict, not a BUILD/TEST recommendation. BUILD is "
            "disabled in this workflow and the repo never performs public execution._",
            "",
        ]

    def _recording_readiness(self) -> list[str]:
        e1 = self._e1()
        return [
            "## Recording Readiness",
            "",
            f"- **Recording Status:** {e1.get('recording_status', 'NOT_RECORDED')}",
            "- Recording into Revenue OS is an **external** act (outside this repo) and is what "
            "turns `E1_CANDIDATE / E1_APPROVED_TO_RECORD` into `E1_RECORDED`.",
            "- This repo drafts the recording payload (below) but never writes it.",
            "",
        ]

    def _b2_b3_boundary(self) -> list[str]:
        e1 = self._e1()
        return [
            "## B2 / B3 Boundary",
            "",
            f"- **B2 Acceptance:** {e1.get('b2_acceptance_status', 'NOT_MET')} — "
            "B2 is satisfied only when Revenue OS rows are created (outside this repo).",
            f"- **B3 Status:** {e1.get('b3_status', 'LOCKED')} — this repo never unlocks B3, even "
            "when the brief is approved.",
            f"- **Public Execution Status:** {e1.get('public_execution_status', 'NONE')} — no "
            "publishing, listing, ads, scraping, or seller/customer contact.",
            "",
        ]

    def _e1_gate_results(self) -> list[str]:
        e1 = self._e1()
        gates = e1.get("gates", [])
        L = ["## E1 Review Gate Results", ""]
        if not gates:
            return L + ["No E1 review gates were evaluated for this run.", ""]
        L.append("| Gate | Status | Reason | Supporting Sources |")
        L.append("| ---- | ------ | ------ | ------------------ |")
        for g in gates:
            supp = ", ".join(g.get("supporting_source_ids", [])) or "—"
            reason = (g.get("reason", "") or "").replace("|", "\\|")
            L.append(f"| {g.get('gate_id','')} | {g.get('status','')} | {reason} | {supp} |")
        L.append("")
        return L

    def _fit_to_primitive(self) -> list[str]:
        e1 = self._e1()
        gate = next((g for g in e1.get("gates", [])
                     if g.get("gate_id") == "fit_to_andrew_authored_primitive"), {})
        return [
            "## Fit to Andrew's Authored Primitive",
            "",
            f"**Fit gate:** {gate.get('status', 'n/a')} — {gate.get('reason', '')}",
            "",
            "_The authored primitive mechanism: evidence before motion; separate the object from "
            "its evidence; gate every state transition; preserve authorship rationale; force a "
            "human decision when risk is detected._",
            "",
        ]

    def _revenue_payload(self) -> list[str]:
        e1 = self._e1()
        payload = self.audit.get("revenue_os_payload_draft") or e1.get("revenue_os_payload_draft") \
            or (self.brief.e1_review or {}).get("revenue_os_payload_draft", {})
        L = [
            "## Revenue OS Recording Payload Draft",
            "",
            "> **DRAFT ONLY — NOT RECORDED — DO NOT WRITE TO REVENUE OS FROM THIS REPO — "
            "HUMAN REVIEW REQUIRED**",
            "",
        ]
        if not payload:
            return L + ["No payload draft was generated.", ""]
        L.append("```json")
        L.append(json.dumps(payload, ensure_ascii=False, indent=2))
        L.append("```")
        L.append("")
        L.append("_Recording these rows into Revenue OS is the separate, external act that "
                 "satisfies B2. This repo does not perform it._")
        L.append("")
        return L

    def _decision_diagnostics(self) -> list[str]:
        diag = self.audit.get("decision_diagnostics") or {}
        if not diag:
            return []
        L = ["## Decision Diagnostics", ""]
        L.append(f"- **Failure mode:** `{diag.get('failure_mode', 'n/a')}` — "
                 f"{diag.get('failure_mode_note', '')}")
        if diag.get("run_partial"):
            L.append("- **Run status:** PARTIAL — observation of the market was "
                     "incomplete; this run cannot be certified as a clean observation.")
        if diag.get("not_a_market_conclusion"):
            L.append("- **NOT a market conclusion:** evidence shortfalls in the affected "
                     "phase(s) reflect tooling/observation failure, not researched "
                     "absence of demand.")
        for tf in diag.get("tool_failures", []) or []:
            L.append(
                f"- **Tool/search failure:** Phase {tf.get('phase')} "
                f"({tf.get('phase_name', '')}) — "
                f"{'/'.join(tf.get('failure_types', []))}; severity "
                f"{tf.get('failure_severity', 'unknown')}; detected via "
                f"{', '.join(tf.get('detection_sources', []))}."
            )
        cont = diag.get("diagnostic_continuation") or {}
        if cont:
            L.append(
                f"- **Diagnostic continuation:** Phase {cont.get('triggered_by_phase')} "
                f"({cont.get('triggered_by_phase_name', '')}) failed first; phases "
                f"{cont.get('diagnostic_phases', [])} still ran in DIAGNOSTIC-ONLY mode. "
                "Their evidence is recorded in the artifacts for future cycles but is "
                "excluded from every gate, claim, score, and the E1 review of this run "
                "— it can never override the failed hard gate or make this run "
                "approval-eligible."
            )
        if diag.get("uncertainty_types"):
            L.append("- **Uncertainty types:** "
                     + ", ".join(f"`{u}`" for u in diag["uncertainty_types"]))
        failing = diag.get("failing_e1_gates", [])
        if failing:
            L.append(f"- **Failing E1 gates (priority order):** "
                     + ", ".join(f"`{g}`" for g in failing))
        tooling_gates = diag.get("gates_not_fully_evaluable_due_to_tooling", [])
        if tooling_gates:
            L.append("- **Gates not fully evaluable due to tooling:** "
                     + ", ".join(f"`{g}`" for g in tooling_gates))
        clean_gates = diag.get("gates_unsupported_after_clean_search", [])
        if clean_gates:
            L.append("- **Gates unsupported after clean search:** "
                     + ", ".join(f"`{g}`" for g in clean_gates))
        diag_gates = diag.get("gates_evaluated_diagnostically", [])
        if diag_gates:
            L.append("- **Gates evaluated diagnostically only (evidence preserved, "
                     "cannot pass this run):** "
                     + ", ".join(f"`{g}`" for g in diag_gates))
        if diag.get("why_generic_not_enough"):
            L.append(f"- **Why generic evidence is not enough:** {diag['why_generic_not_enough']}")
        rej = diag.get("rejected_evidence", {}) or {}
        if rej.get("total"):
            by_reason = ", ".join(f"{k}={v}" for k, v in (rej.get("by_reason") or {}).items())
            L.append(f"- **Rejected evidence:** {rej.get('total')} ({by_reason})")
        L.append("")
        L.append("| Phase | Status | Observation state | Accepted | Supports E1 gate(s) |")
        L.append("| ----- | ------ | ----------------- | -------- | ------------------- |")
        for p in diag.get("phases", []):
            L.append(
                f"| {p.get('phase')} {p.get('name','')} | {p.get('status','')} | "
                f"{p.get('observation_state', '—')} | "
                f"{p.get('accepted_source_count', 0)} | "
                f"{', '.join(p.get('supports_e1_gates', [])) or '—'} |"
            )
        L.append("")
        belief = diag.get("belief_state") or {}
        if belief:
            L.append("### Belief State (per-gate observation status)")
            L.append("")
            L.append("| Belief | Status | Confidence | Evidence | Reason |")
            L.append("| ------ | ------ | ---------- | -------- | ------ |")
            for key, entry in belief.items():
                conf = entry.get("confidence")
                L.append(
                    f"| `{key}` | {entry.get('status', '')} | "
                    f"{conf if conf is not None else '—'} | "
                    f"{entry.get('evidence_count', 0)} | {entry.get('reason', '')} |"
                )
            L.append("")
        return L

    def _next_evidence_plan(self) -> list[str]:
        plan = self.audit.get("next_evidence_plan") or {}
        if not plan.get("applies"):
            return []
        L = ["## Next Evidence Plan", ""]
        if plan.get("primary_blocker"):
            L.append(f"**Primary blocker (fix first):** `{plan['primary_blocker']}`")
        failed = plan.get("failed_gates_in_priority_order", [])
        if failed:
            L.append("**Failed gates (priority order):** " + ", ".join(f"`{g}`" for g in failed))
        L.append("")
        for block in plan.get("targeted_search_plan", []):
            L.append(f"### Gate `{block['for_failed_gate']}` → {block['phase_name']}")
            sts = block.get("would_satisfy_source_types", [])
            if sts:
                L.append(f"_Would satisfy:_ {', '.join(sts)}")
            if block.get("would_not_count"):
                L.append(f"_Would NOT count:_ {block['would_not_count']}")
            for fam in block.get("search_families", []):
                L.append(f"- **{fam['name']}** — {fam['intent']}")
                for q in fam.get("queries", [])[:5]:
                    L.append(f"    - `{q}`")
            L.append("")
        return L

    def _incomplete(self, phase_present: bool) -> Optional[str]:
        if phase_present:
            return None
        return ("This section is incomplete because an earlier hard gate capped the "
                "decision and the phase did not run.")

    def _price_band(self) -> list[str]:
        L = ["## Price Band Mapping", ""]
        msg = self._incomplete(self.brief.phase_3_result is not None)
        if msg:
            return L + [msg, ""]
        details = self.brief.phase_3_result.details or {}
        bands = details.get("price_bands", {})
        records = self.audit.get("price_bands", [])          # verified prices only
        lead_count = self.audit.get("price_leads", details.get("lead_count", 0))
        directional = self.audit.get("directional", [])

        L.append(f"**Verified competitor prices:** {len(records)}  |  "
                 f"**Competitor leads (no price captured):** {lead_count}  |  "
                 f"**Directional pricing articles:** {len(directional)}")
        L.append("")

        if not records:
            L.append("No *verified* competitor prices were captured.")
            if lead_count:
                L.append(f"{lead_count} competitor lead(s) were found but lacked a specific "
                         "observed price, so they do not establish a price band.")
            if directional:
                L.append(f"{len(directional)} general market-pricing article(s) provide "
                         "directional context only.")
            L.append("")
            L.append("**What price evidence does NOT prove:** exact competitor price bands were "
                     "not established for this product.")
            L.append("")
            return L

        for tier in ("low", "mid", "premium"):
            vals = bands.get(tier, [])
            pretty = ", ".join(f"${v:g}" for v in vals) if vals else "—"
            L.append(f"- **{tier.capitalize()} tier:** {pretty}")
        L.append("")
        L.append("**Verified competitor prices:**")
        L.append("")
        L.append("| Competitor | Price | Tier | Currency | URL |")
        L.append("| ---------- | ----- | ---- | -------- | --- |")
        for r in records:
            price = r.get("price_observed")
            price_s = f"${price:g}" if isinstance(price, (int, float)) else "—"
            L.append(f"| {r.get('competitor_name','')} | {price_s} | {r.get('price_tier','')} | "
                     f"{r.get('currency','')} | {r.get('url','')} |")
        if directional:
            L.append("")
            L.append("**Directional pricing context (not competitor prices):**")
            for r in directional:
                L.append(f"- {r.get('competitor_name','')} — {r.get('url','')}")
        supported = details.get("summary", "")
        L.append("")
        L.append(f"**Supported price range:** {supported or 'see tiers above.'}")
        L.append("**What price evidence does NOT prove:** that buyers will pay this price for "
                 "*this* product — only that comparable products are listed at these prices.")
        L.append("")
        return L

    def _competitor_map(self) -> list[str]:
        L = ["## Competitor Presence Map", ""]
        msg = self._incomplete(self.brief.phase_4_result is not None)
        if msg:
            return L + [msg, ""]
        records = self.audit.get("competitors", [])
        if not records:
            return L + ["No competitors were structurally mapped.", ""]
        L.append("**10-field competitor map:**")
        L.append("")
        L.append("| Competitor | Price | Target buyer | Format | Main promise | Does NOT govern |")
        L.append("| ---------- | ----- | ------------ | ------ | ------------ | --------------- |")
        for r in records:
            price = r.get("price")
            price_s = f"${price:g}" if isinstance(price, (int, float)) else "—"
            L.append(f"| {r.get('competitor_name','')} | {price_s} | {r.get('target_buyer','') or '—'} | "
                     f"{r.get('product_format','') or '—'} | {(r.get('main_promise','') or '—')} | "
                     f"{(r.get('what_it_does_not_appear_to_govern','') or '—')} |")
        L.append("")
        L.append("**6-dimension teardown** (none / weak / present / strong / unknown):")
        L.append("")
        L.append("| Competitor | Demand val. | Authorship | Build gate | Listing gate | Fee stress | Post-launch loop |")
        L.append("| ---------- | ----------- | ---------- | ---------- | ------------ | ---------- | ---------------- |")
        for r in records:
            L.append(f"| {r.get('competitor_name','')} | {r.get('demand_validation_score','unknown')} | "
                     f"{r.get('authorship_evidence_score','unknown')} | {r.get('build_readiness_gate_score','unknown')} | "
                     f"{r.get('listing_readiness_gate_score','unknown')} | {r.get('fee_stress_logic_score','unknown')} | "
                     f"{r.get('post_launch_decision_loop_score','unknown')} |")
        L.append("")
        L.append("_Distinction: a product that **stores** information is not the same as a product "
                 "that **forces** a decision. The teardown scores capture which competitors actually "
                 "gate decisions vs. merely track._")
        L.append("")
        return L

    def _missing_mechanism(self) -> list[str]:
        L = ["## Missing-Mechanism Gap", ""]
        msg = self._incomplete(self.brief.phase_5_result is not None)
        if msg:
            return L + [msg, ""]
        m = self.audit.get("missing_mechanism", {}) or (self.brief.phase_5_result.details or {})
        if not m:
            return L + ["No missing-mechanism analysis was produced.", ""]
        L.append(f"- **Status:** {m.get('status', 'unsupported')}")
        L.append(f"- **Structural:** {m.get('is_structural', False)}")
        L.append(f"- **Current competitor pattern:** {m.get('current_competitor_pattern','') or '—'}")
        L.append(f"- **Missing mechanism:** {m.get('missing_mechanism','') or '—'}")
        L.append(f"- **Why it matters:** {m.get('why_it_matters','') or '—'}")
        L.append(f"- **Proposed mechanism:** {m.get('proposed_mechanism','') or '—'}")
        L.append(f"- **Gap statement:** {m.get('gap_statement','') or '—'}")
        L.append(f"- **What would make the gap weak:** {m.get('what_would_make_gap_weak','') or '—'}")
        supp = ", ".join(m.get("supporting_source_ids", [])) or "—"
        L.append(f"- **Supporting sources:** {supp}")
        L.append("")
        L.append("_A gap that is only 'looks better / cleaner / cheaper / more pages / different "
                 "buyer label' is aesthetic, not structural, and is marked unsupported._")
        L.append("")
        return L

    def _hypothesis(self) -> list[str]:
        h = self.brief.product_hypothesis
        return [
            "## Product Hypothesis",
            f"- **Product Name:** {h.product_name}",
            f"- **Target Buyer:** {h.target_buyer}",
            f"- **Buyer Job:** {h.buyer_job}",
            f"- **Product Format:** {h.product_format}",
            f"- **Primary Channel:** {h.primary_channel}",
            f"- **Missing Mechanism Hypothesis:** {h.missing_mechanism_hypothesis}",
            "",
        ]

    def _phase_results(self) -> list[str]:
        L = ["## Phase Results", ""]
        for phase in (self.brief.phase_1_result, self.brief.phase_2_result,
                      self.brief.phase_3_result, self.brief.phase_4_result,
                      self.brief.phase_5_result):
            if phase is None:
                continue
            if phase.status == PhaseStatus.PASS:
                mark = "✓"
            elif phase.status == PhaseStatus.DIAGNOSTIC_ONLY:
                mark = "◇"
            else:
                mark = "✗"
            L.append(f"### {mark} Phase {phase.phase_number}: {phase.phase_name}")
            L.append(f"**Status:** {phase.status.value}")
            if phase.diagnostic_reason:
                L.append(f"**Diagnostic-only:** {phase.diagnostic_reason}")
            L.append(f"**Pass Condition:** {phase.pass_condition}")
            L.append("")
            L.append(f"**Findings:** {phase.findings}")
            L.append(f"**Reason:** {phase.reason}")
            if phase.sources_collected:
                L.append(f"**Sources ({len(phase.sources_collected)}):**")
                for s in phase.sources_collected:
                    L.append(f"- [{s.source_name}]({s.url}) ({s.date_observed.strftime('%Y-%m-%d')})")
            else:
                L.append("**Sources:** none collected in this phase.")
            L.append("")
        return L

    def _score_breakdown(self) -> list[str]:
        sc = self.audit.get("scorecard", {})
        comps = sc.get("components", [])
        L = ["## Evidence Quality Score Breakdown", ""]
        if not comps:
            L.append("Score breakdown not available for this run.")
            L.append("")
            return L
        L.append(f"_Formula {sc.get('formula_version', 'v1')} — total "
                 f"{sc.get('total_score', self.brief.evidence_quality_score):.2f}_")
        L.append("")
        L.append("| Component | Weight | Raw Score | Weighted Score | Rationale |")
        L.append("| --------- | ------ | --------- | -------------- | --------- |")
        for c in comps:
            L.append(f"| {c.get('name','')} | {c.get('weight',0):.2f} | "
                     f"{c.get('raw_score',0):.2f} | {c.get('weighted_score',0):.2f} | "
                     f"{c.get('rationale','')} |")
        overrides = sc.get("hard_gate_overrides", [])
        if overrides:
            L.append("")
            L.append(f"**Hard-gate overrides applied:** {'; '.join(overrides)}")
        L.append("")
        return L

    def _hard_gates(self) -> list[str]:
        gates = self.audit.get("gates", [])
        L = ["## Hard Gate Results", ""]
        if not gates:
            L.append("No hard gates evaluated for this run.")
            L.append("")
            return L
        L.append("| Gate | Status | Reason |")
        L.append("| ---- | ------ | ------ |")
        for g in gates:
            L.append(f"| {g.get('gate','')} | {g.get('status','').upper()} | {g.get('reason','')} |")
        L.append("")
        return L

    def _buyer_language(self) -> list[str]:
        arts = self.audit.get("buyer_artifacts", [])
        L = ["## Buyer-Language Artifacts", ""]
        if not arts:
            L.append("No valid buyer-language artifacts were collected.")
            L.append("")
            return L
        for a in arts:
            L.append(f"- \"{a.get('quote','')}\" — [{a.get('platform','source')}]({a.get('url','')}) "
                     f"(pain: {a.get('pain_type','other')}, strength: {a.get('strength','')}, "
                     f"source {a.get('source_id','')})")
        L.append("")
        return L

    def _claim_ledger(self) -> list[str]:
        claims = self.audit.get("claims", [])
        L = ["## Claim Ledger Summary", ""]
        if not claims:
            L.append("No claims recorded for this run.")
            L.append("")
            return L
        L.append("| Claim | Status | Evidence Grade Required | Supporting Sources | Reason |")
        L.append("| ----- | ------ | ----------------------- | ------------------ | ------ |")
        for c in claims:
            supp = ", ".join(c.get("supporting_source_ids", [])) or "—"
            claim_text = (c.get("claim", "") or "").replace("|", "\\|")
            reason_text = (c.get("reason", "") or "").replace("|", "\\|")
            L.append(f"| {claim_text} | {c.get('status','')} | {c.get('required_evidence_grade','')} | "
                     f"{supp} | {reason_text} |")
        L.append("")
        return L

    def _search_summary(self) -> list[str]:
        s = self.audit.get("search_summary", {})
        return [
            "## Search Log Summary",
            "",
            f"- Total searches attempted: {s.get('total', 0)}",
            f"- Searches with results: {s.get('results_found', 0)}",
            f"- Zero-result searches: {s.get('zero_results', 0)}",
            f"- Tool-error searches: {s.get('tool_error', 0)}",
            f"- Rate-limited searches: {s.get('rate_limited', 0)}",
            "",
        ]

    def _rejected_summary(self) -> list[str]:
        r = self.audit.get("rejected_summary", {})
        L = ["## Rejected Sources Summary", "", f"- Total rejected: {r.get('total', 0)}"]
        by_reason = r.get("by_reason", {})
        if by_reason:
            L.append("- By reason:")
            for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
                L.append(f"  - {reason}: {count}")
        examples = r.get("examples", [])
        if examples:
            L.append("- Examples:")
            for ex in examples[:5]:
                L.append(f"  - [{ex.get('rejection_reason','')}] {ex.get('url','') or ex.get('title','')}")
        L.append("")
        return L

    def _all_sources(self) -> list[str]:
        L = ["## All Sources", ""]
        sources = self.brief.all_sources()
        if not sources:
            L.append("No sources collected.")
            L.append("")
            return L
        for i, s in enumerate(sources, 1):
            L.append(f"### Source {i}: {s.source_name}")
            L.append(f"- **URL:** {s.url}")
            L.append(f"- **Platform:** {s.platform}")
            L.append(f"- **Date Observed:** {s.date_observed.strftime('%Y-%m-%d')}")
            if s.price_observed is not None:
                L.append(f"- **Price:** ${s.price_observed:.2f}")
            if s.buyer_language_captured:
                qt = "Direct Quote" if s.is_direct_quote else "Composite/Observed"
                L.append(f"- **Buyer Language ({qt}):** \"{s.buyer_language_captured}\"")
            L.append(f"- **What This Proves:** {s.what_this_proves}")
            L.append(f"- **What This Does NOT Prove:** {s.what_this_does_not_prove}")
            if s.gap_note:
                L.append(f"- **Gap Note:** {s.gap_note}")
            L.append("")
        return L

    def _audit_artifacts(self) -> list[str]:
        paths = self.audit.get("artifact_paths", {})
        L = ["## Audit Artifacts", ""]
        if self.brief.run_id:
            L.append(f"Run folder: `runs/{self.brief.run_id}/`")
            L.append("")
        names = list(paths.keys()) or [
            "run_manifest.json", "search_log.jsonl", "rejected_sources.jsonl",
            "source_ledger.jsonl", "buyer_language_artifacts.jsonl", "claim_ledger.jsonl",
            "evidence_scorecard.json",
        ]
        for name in names:
            L.append(f"- `{name}`")
        L.append("")
        return L

    @staticmethod
    def _section(title: str, body: Any) -> list[str]:
        return [f"## {title}", "", str(body), ""]
