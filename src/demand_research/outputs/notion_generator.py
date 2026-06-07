"""Generate Notion document for demand brief (structure for MCP integration)."""

import logging
from demand_research.models import DemandBrief
from demand_research.config import settings

logger = logging.getLogger(__name__)


class NotionGenerator:
    """Generates Notion document for demand brief."""

    def __init__(self):
        self.notion_api_key = settings.notion_api_key
        self.database_id = settings.notion_database_id

    def generate(self, brief: DemandBrief) -> dict:
        """
        Generate Notion document structure.
        In production, this will use mcp__d9133ba1-d9e0-4b8e-9e5f-2bc1ceff098d__notion-* tools
        to create pages and databases.

        Returns: dict with page_id and URL
        """
        # This is a placeholder that returns the structure
        # Actual Notion API calls will be implemented when MCP tools are available
        page_structure = {
            "title": f"📊 Demand Brief: {brief.product_hypothesis.product_name}",
            "emoji": "📊",
            "properties": {
                "Decision": {"type": "select", "value": brief.decision.value},
                "Evidence Quality": {
                    "type": "number",
                    "value": round(brief.evidence_quality_score * 100),
                },
                "Product Name": {"type": "text", "value": brief.product_hypothesis.product_name},
                "Target Buyer": {"type": "text", "value": brief.product_hypothesis.target_buyer},
                "Status": {"type": "select", "value": "In Research"},
            },
            "children": self._build_page_structure(brief),
        }

        logger.info(
            f"Notion page structure prepared for: {brief.product_hypothesis.product_name}"
        )
        # Note: Actual page creation happens via MCP tools
        # return {"page_id": "placeholder", "url": "will-be-set-by-mcp-tools"}

        return page_structure

    def _build_page_structure(self, brief: DemandBrief) -> list[dict]:
        """Build nested page structure for Notion."""
        blocks = []

        # Executive Summary Section
        blocks.append({
            "type": "heading_1",
            "text": "Executive Summary",
        })
        blocks.append({
            "type": "paragraph",
            "text": f"Decision: {brief.decision.value} | "
            f"Quality: {brief.evidence_quality_score:.0%}",
        })
        blocks.append({
            "type": "paragraph",
            "text": brief.decision_reasoning,
        })

        # Product Hypothesis Section
        blocks.append({"type": "heading_1", "text": "Product Hypothesis"})
        hyp = brief.product_hypothesis
        blocks.append(
            {
                "type": "bulleted_list",
                "items": [
                    f"Product Name: {hyp.product_name}",
                    f"Target Buyer: {hyp.target_buyer}",
                    f"Buyer Job: {hyp.buyer_job}",
                    f"Format: {hyp.product_format}",
                    f"Channel: {hyp.primary_channel}",
                    f"Gap Hypothesis: {hyp.missing_mechanism_hypothesis}",
                ],
            }
        )

        # Phase Results
        for phase in [
            brief.phase_1_result,
            brief.phase_2_result,
            brief.phase_3_result,
            brief.phase_4_result,
            brief.phase_5_result,
        ]:
            if phase:
                blocks.extend(self._build_phase_blocks(phase))

        # Sources
        blocks.append({"type": "heading_1", "text": "All Sources"})
        all_sources = brief.all_sources()
        if all_sources:
            for source in all_sources:
                blocks.append({
                    "type": "paragraph",
                    "text": f"[{source.source_name}]({source.url}) - "
                    f"{source.platform} ({source.date_observed.strftime('%Y-%m-%d')})",
                })
        else:
            blocks.append({"type": "paragraph", "text": "No sources collected."})

        return blocks

    def _build_phase_blocks(self, phase_result) -> list[dict]:
        """Build blocks for a phase result."""
        blocks = []

        status = "✓ PASS" if phase_result.status.value == "PASS" else "✗ FAIL"
        blocks.append({
            "type": "heading_2",
            "text": f"{status} Phase {phase_result.phase_number}: {phase_result.phase_name}",
        })

        blocks.append({
            "type": "paragraph",
            "text": f"**Pass Condition:** {phase_result.pass_condition}",
        })

        blocks.append({
            "type": "paragraph",
            "text": f"**Findings:** {phase_result.findings}",
        })

        if phase_result.sources_collected:
            blocks.append({"type": "heading_3", "text": "Sources"})
            for source in phase_result.sources_collected:
                blocks.append({
                    "type": "paragraph",
                    "text": f"- [{source.source_name}]({source.url})",
                })

        return blocks
