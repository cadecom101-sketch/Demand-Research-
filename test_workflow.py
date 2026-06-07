#!/usr/bin/env python
"""Quick test of workflow structure."""

import asyncio
from demand_research.models import ProductHypothesis, PhaseStatus
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.decision_engine import DecisionEngine
from demand_research.outputs.markdown_generator import MarkdownGenerator


async def test_workflow():
    """Test basic workflow structure."""
    print("Testing Demand Research Workflow Structure...\n")

    # Create test hypothesis
    hypothesis = ProductHypothesis(
        product_name="Test Notion Template",
        target_buyer="Digital product sellers",
        buyer_job="Validate product demand before building",
        product_format="Notion template",
        primary_channel="Etsy",
        missing_mechanism_hypothesis="Forces demand validation before product creation",
    )

    print(f"✓ ProductHypothesis created: {hypothesis.product_name}")

    # Run orchestrator
    orchestrator = ResearchOrchestrator()
    brief = await orchestrator.run_workflow(hypothesis)

    print(f"✓ Orchestrator completed")
    print(f"  - Decision: {brief.decision.value}")
    print(f"  - Quality: {brief.evidence_quality_score:.0%}")
    print(f"  - Phases passed: {sum(1 for p in [brief.phase_1_result, brief.phase_2_result, brief.phase_3_result, brief.phase_4_result, brief.phase_5_result] if p and p.status == PhaseStatus.PASS)}/5")

    # Generate markdown
    markdown_gen = MarkdownGenerator()
    markdown_path = markdown_gen.generate(brief)
    print(f"✓ Markdown brief generated: {markdown_path}")

    # Read and display
    content = markdown_path.read_text()
    print(f"\n--- Generated Brief Preview ---\n")
    print(content[:500] + "...\n")

    print("✓ All tests passed!")
    return brief


if __name__ == "__main__":
    brief = asyncio.run(test_workflow())
