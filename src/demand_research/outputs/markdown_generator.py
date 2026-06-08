"""Generate the human-readable markdown demand brief.

The brief is the *summary* of a run; the `runs/{run_id}/` folder is the audit
trail. This renderer surfaces the audit layer (score breakdown, hard gates,
claim ledger, search + rejected summaries, artifact paths) so a reader can see
exactly how the verdict was reached and where to verify it.
"""

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
    def render(self) -> str:
        b = self.brief
        L: list[str] = []
        L.append(f"# Demand Brief: {b.product_hypothesis.product_name}")
        L.append(f"Generated: {b.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        L.append("")
        L += self._executive_summary()
        L += self._hypothesis()
        L += self._evidence_stage()
        L += self._phase_results()
        L += self._score_breakdown()
        L += self._hard_gates()
        L += self._buyer_language()
        L += self._price_band()
        L += self._competitor_map()
        L += self._missing_mechanism()
        L += self._claim_ledger()
        L += self._search_summary()
        L += self._rejected_summary()
        L += self._section("What This Proves", self.audit.get("what_proves", "Not assessed."))
        L += self._section("What This Does NOT Prove", self.audit.get("what_not_proves", "Not assessed."))
        L += self._section("What Would Change This Decision", self.audit.get("what_would_change", "Not assessed."))
        L += self._section("Next Recommended Experiment", self.audit.get("next_experiment", "Not assessed."))
        L += self._all_sources()
        L += self._audit_artifacts()
        L.append("---")
        L.append(f"_Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}_")
        return "\n".join(L)

    # ------------------------------------------------------------------ #
    def _executive_summary(self) -> list[str]:
        b = self.brief
        fatal = b.fatal_gaps or self.audit.get("fatal_gaps", [])
        fatal_str = "; ".join(fatal) if fatal else "None"
        return [
            "## Executive Summary",
            f"- **Decision:** {b.decision.value}",
            f"- **Evidence Stage:** {b.evidence_stage.value}",
            f"- **Evidence Quality:** {b.evidence_quality_score:.2%}",
            f"- **Run ID:** {b.run_id or 'n/a'}",
            f"- **Run Status:** {b.run_status}",
            f"- **Fatal Gaps:** {fatal_str}",
            f"- **Main Reason:** {b.decision_reasoning}",
            "",
        ]

    def _evidence_stage(self) -> list[str]:
        stage = self.brief.evidence_stage.value
        meaning = {
            "E0": "Unproven. Stays E0 — does not yet earn build time (KILL / PARK / REVISE).",
            "E1_CANDIDATE": "Earns the next cheapest external TEST (fake-door / pre-order).",
            "POST_E1": "Past validation; build-justified by stronger evidence.",
        }.get(stage, "")
        return [
            "## Evidence Stage",
            "",
            f"Evidence Stage: {stage}",
            "",
            meaning,
            "",
        ]

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
        bands = (self.brief.phase_3_result.details or {}).get("price_bands", {})
        records = self.audit.get("price_bands", [])
        if not records and not any(bands.values()):
            return L + ["No competitor prices were captured.", ""]
        for tier in ("low", "mid", "premium"):
            vals = bands.get(tier, [])
            pretty = ", ".join(f"${v:g}" for v in vals) if vals else "—"
            L.append(f"- **{tier.capitalize()} tier:** {pretty}")
        if records:
            L.append("")
            L.append("| Competitor | Price | Tier | Currency | URL |")
            L.append("| ---------- | ----- | ---- | -------- | --- |")
            for r in records:
                price = r.get("price_observed")
                price_s = f"${price:g}" if isinstance(price, (int, float)) else "—"
                L.append(f"| {r.get('competitor_name','')} | {price_s} | {r.get('price_tier','')} | "
                         f"{r.get('currency','')} | {r.get('url','')} |")
        supported = (self.brief.phase_3_result.details or {}).get("summary", "")
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
            mark = "✓" if phase.status == PhaseStatus.PASS else "✗"
            L.append(f"### {mark} Phase {phase.phase_number}: {phase.phase_name}")
            L.append(f"**Status:** {phase.status.value}")
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
