"""CLI entry point for demand research workflow."""

import asyncio
import logging
from pathlib import Path
from typing import Optional
import click
from datetime import datetime
import json

from demand_research.models import ProductHypothesis
from demand_research.agents.orchestrator import ResearchOrchestrator
from demand_research.outputs.markdown_generator import MarkdownGenerator
from demand_research.outputs.notion_generator import NotionGenerator
from demand_research.config import settings

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@click.group()
def cli():
    """Demand Research Workflow - Autonomous product validation."""
    pass


@cli.command()
@click.option(
    "--name",
    prompt="Product name",
    help="Name of the product to research",
)
@click.option(
    "--buyer",
    prompt="Target buyer",
    help="Who is the buyer/user?",
)
@click.option(
    "--job",
    prompt="Buyer job",
    help="What are they trying to do?",
)
@click.option(
    "--format",
    prompt="Product format",
    help="Notion template / spreadsheet / printable / etc.",
)
@click.option(
    "--channel",
    prompt="Primary channel",
    help="Etsy / Gumroad / Notion Marketplace / etc.",
)
@click.option(
    "--gap",
    prompt="Missing mechanism hypothesis",
    help="What structural mechanism is missing?",
)
@click.option(
    "--from-file",
    type=click.Path(exists=True),
    help="Load product hypothesis from YAML/JSON file instead of prompts",
)
def research(
    name: str,
    buyer: str,
    job: str,
    format: str,
    channel: str,
    gap: str,
    from_file: Optional[str],
):
    """Run demand research workflow for a product idea."""

    if from_file:
        hypothesis = load_hypothesis_from_file(from_file)
    else:
        hypothesis = ProductHypothesis(
            product_name=name,
            target_buyer=buyer,
            buyer_job=job,
            product_format=format,
            primary_channel=channel,
            missing_mechanism_hypothesis=gap,
        )

    click.echo("\n" + "=" * 60)
    click.echo(f"Starting Demand Research Workflow")
    click.echo(f"Product: {hypothesis.product_name}")
    click.echo(f"Buyer: {hypothesis.target_buyer}")
    click.echo("=" * 60 + "\n")

    # Run workflow
    brief = asyncio.run(run_research_async(hypothesis))

    # Generate outputs
    click.echo("\nGenerating outputs...")

    markdown_gen = MarkdownGenerator()
    markdown_path = markdown_gen.generate(brief)
    click.echo(f"✓ Markdown brief: {markdown_path}")

    notion_gen = NotionGenerator()
    notion_structure = notion_gen.generate(brief)
    click.echo(f"✓ Notion structure prepared (MCP integration pending)")

    # Summary
    click.echo("\n" + "=" * 60)
    click.echo("Research Complete")
    click.echo("=" * 60)
    click.echo(f"Decision: {brief.decision.value}")
    click.echo(f"Evidence Quality: {brief.evidence_quality_score:.0%}")
    click.echo(f"Sources Collected: {len(brief.all_sources())}")
    click.echo(f"Reasoning: {brief.decision_reasoning[:100]}...")
    click.echo("=" * 60 + "\n")

    # Save brief as JSON for further processing
    save_brief_json(brief)


@cli.command()
def list_briefs():
    """List all completed demand briefs."""
    briefs_dir = settings.briefs_dir
    briefs = list(briefs_dir.glob("*-demand-brief.md"))

    if not briefs:
        click.echo("No demand briefs found.")
        return

    click.echo(f"\nFound {len(briefs)} demand briefs:\n")
    for brief_file in sorted(briefs, reverse=True):
        click.echo(f"  {brief_file.name}")
    click.echo()


@cli.command()
@click.argument("brief_path", type=click.Path(exists=True))
def view(brief_path: str):
    """View a completed demand brief."""
    path = Path(brief_path)
    content = path.read_text()
    click.echo(content)


@cli.command()
@click.option(
    "--product",
    help="Product name to watch",
)
def watch(product: Optional[str]):
    """Watch for research workflow progress (placeholder for async monitoring)."""
    click.echo("Research monitoring not yet implemented.")
    click.echo("Check the briefs/ directory for generated markdown files.")


def load_hypothesis_from_file(file_path: str) -> ProductHypothesis:
    """Load product hypothesis from YAML or JSON file."""
    path = Path(file_path)

    if path.suffix in [".yaml", ".yml"]:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
    elif path.suffix == ".json":
        with open(path) as f:
            data = json.load(f)
    else:
        raise ValueError(f"Unsupported file format: {path.suffix}")

    return ProductHypothesis(**data)


async def run_research_async(hypothesis: ProductHypothesis):
    """Run research workflow asynchronously."""
    orchestrator = ResearchOrchestrator()
    brief = await orchestrator.run_workflow(hypothesis)
    return brief


def save_brief_json(brief):
    """Save brief as JSON for processing."""
    json_dir = settings.data_dir / "briefs"
    json_dir.mkdir(parents=True, exist_ok=True)

    product_id = str(brief.product_hypothesis.product_id).split("-")[0]
    json_path = json_dir / f"{product_id}-brief.json"

    # Convert brief to JSON-serializable dict
    brief_dict = {
        "product_hypothesis": {
            "product_name": brief.product_hypothesis.product_name,
            "target_buyer": brief.product_hypothesis.target_buyer,
            "buyer_job": brief.product_hypothesis.buyer_job,
            "product_format": brief.product_hypothesis.product_format,
            "primary_channel": brief.product_hypothesis.primary_channel,
            "missing_mechanism_hypothesis": brief.product_hypothesis.missing_mechanism_hypothesis,
        },
        "decision": brief.decision.value,
        "evidence_quality_score": brief.evidence_quality_score,
        "decision_reasoning": brief.decision_reasoning,
        "created_at": brief.created_at.isoformat(),
    }

    with open(json_path, "w") as f:
        json.dump(brief_dict, f, indent=2)


def main():
    """Main entry point."""
    try:
        cli()
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise click.ClickException(str(e))


if __name__ == "__main__":
    main()
