"""Generate markdown demand brief for git version control."""

import logging
from pathlib import Path
from datetime import datetime
from demand_research.models import DemandBrief
from demand_research.config import settings

logger = logging.getLogger(__name__)


class MarkdownGenerator:
    """Generates markdown demand brief document."""

    def __init__(self):
        self.output_dir = settings.briefs_dir

    def generate(self, brief: DemandBrief) -> Path:
        """
        Generate markdown brief and save to file.
        Returns: path to generated markdown file
        """
        product_id = str(brief.product_hypothesis.product_id).split("-")[0]
        filename = f"{product_id}-{brief.product_hypothesis.product_name.lower().replace(' ', '-')}-demand-brief.md"
        output_path = self.output_dir / filename

        content = self._render_brief(brief)

        output_path.write_text(content, encoding="utf-8")
        logger.info(f"Markdown brief generated: {output_path}")

        return output_path

    def _render_brief(self, brief: DemandBrief) -> str:
        """Render complete brief as markdown."""
        lines = []

        # Header
        lines.append(f"# Demand Brief: {brief.product_hypothesis.product_name}")
        lines.append(f"Generated: {brief.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        lines.append("")

        # Executive Summary
        lines.append("## Executive Summary")
        lines.append(f"**Decision: {brief.decision.value}**")
        lines.append(f"**Evidence Quality: {brief.evidence_quality_score:.2%}**")
        lines.append("")
        lines.append(brief.decision_reasoning)
        lines.append("")

        # Product Hypothesis
        lines.append("## Product Hypothesis")
        hyp = brief.product_hypothesis
        lines.append(f"- **Product Name:** {hyp.product_name}")
        lines.append(f"- **Target Buyer:** {hyp.target_buyer}")
        lines.append(f"- **Buyer Job:** {hyp.buyer_job}")
        lines.append(f"- **Product Format:** {hyp.product_format}")
        lines.append(f"- **Primary Channel:** {hyp.primary_channel}")
        lines.append(f"- **Missing Mechanism Hypothesis:** {hyp.missing_mechanism_hypothesis}")
        lines.append("")

        # Phase Results
        if brief.phase_1_result:
            lines.extend(self._render_phase(brief.phase_1_result))
            lines.append("")

        if brief.phase_2_result:
            lines.extend(self._render_phase(brief.phase_2_result))
            lines.append("")

        if brief.phase_3_result:
            lines.extend(self._render_phase(brief.phase_3_result))
            lines.append("")

        if brief.phase_4_result:
            lines.extend(self._render_phase(brief.phase_4_result))
            lines.append("")

        if brief.phase_5_result:
            lines.extend(self._render_phase(brief.phase_5_result))
            lines.append("")

        # Sources
        lines.append("## All Sources")
        all_sources = brief.all_sources()
        if all_sources:
            for i, source in enumerate(all_sources, 1):
                lines.extend(self._render_source_card(i, source))
                lines.append("")
        else:
            lines.append("No sources collected.")
            lines.append("")

        # Metadata
        lines.append("---")
        lines.append(f"_Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}_")

        return "\n".join(lines)

    def _render_phase(self, phase_result) -> list[str]:
        """Render a single phase result."""
        lines = []

        status_marker = "✓" if phase_result.status.value == "PASS" else "✗"
        lines.append(f"## {status_marker} Phase {phase_result.phase_number}: {phase_result.phase_name}")
        lines.append(f"**Status:** {phase_result.status.value}")
        lines.append(f"**Pass Condition:** {phase_result.pass_condition}")
        lines.append("")

        lines.append("### Findings")
        lines.append(phase_result.findings)
        lines.append("")

        lines.append("### Reason")
        lines.append(phase_result.reason)
        lines.append("")

        lines.append(f"### Sources ({len(phase_result.sources_collected)} collected)")
        if phase_result.sources_collected:
            for source in phase_result.sources_collected:
                lines.append(
                    f"- [{source.source_name}]({source.url}) "
                    f"({source.date_observed.strftime('%Y-%m-%d')})"
                )
        else:
            lines.append("No sources collected in this phase.")
        lines.append("")

        return lines

    def _render_source_card(self, number: int, source) -> list[str]:
        """Render a source card as markdown."""
        lines = []

        lines.append(f"### Source {number}: {source.source_name}")
        lines.append(f"- **URL:** {source.url}")
        lines.append(f"- **Platform:** {source.platform}")
        lines.append(f"- **Date Observed:** {source.date_observed.strftime('%Y-%m-%d')}")

        if source.search_phrase_used:
            lines.append(f"- **Search Phrase:** {source.search_phrase_used}")

        if source.price_observed:
            lines.append(f"- **Price:** ${source.price_observed:.2f}")

        if source.buyer_language_captured:
            quote_type = "Direct Quote" if source.is_direct_quote else "Composite/Observed"
            lines.append(f"- **Buyer Language ({quote_type}):** \"{source.buyer_language_captured}\"")

        lines.append(f"- **What This Proves:** {source.what_this_proves}")
        lines.append(f"- **What This Does NOT Prove:** {source.what_this_does_not_prove}")

        if source.gap_note:
            lines.append(f"- **Gap Note:** {source.gap_note}")

        if source.screenshot_filename:
            lines.append(f"- **Screenshot:** ![{source.source_name}](../data/screenshots/{source.screenshot_filename})")

        return lines
