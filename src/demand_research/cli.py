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
    default=None,
    help="Name of the product to research",
)
@click.option(
    "--buyer",
    default=None,
    help="Who is the buyer/user?",
)
@click.option(
    "--job",
    default=None,
    help="What are they trying to do?",
)
@click.option(
    "--format",
    default=None,
    help="Notion template / spreadsheet / printable / etc.",
)
@click.option(
    "--channel",
    default=None,
    help="Etsy / Gumroad / Notion Marketplace / etc.",
)
@click.option(
    "--gap",
    default=None,
    help="What structural mechanism is missing?",
)
@click.option(
    "--from-file",
    type=click.Path(exists=True),
    help="Load product hypothesis from YAML/JSON file instead of prompts",
)
@click.option(
    "--workflow-mode",
    default="e1-demand-brief",
    show_default=True,
    help="Workflow mode. Only 'e1-demand-brief' is supported (BUILD disabled).",
)
@click.option(
    "--target-member",
    default="Base",
    show_default=True,
    help="Member under the Governed Solo-Operator Launch OS primitive to validate. "
         "Base only; Member A/B are deferred and fail scope lock.",
)
def research(
    name: Optional[str],
    buyer: Optional[str],
    job: Optional[str],
    format: Optional[str],
    channel: Optional[str],
    gap: Optional[str],
    from_file: Optional[str],
    workflow_mode: str,
    target_member: str,
):
    """Run the E1 demand-brief review workflow for the Base member."""

    from demand_research.e1_review import normalize_target_member, is_excluded_member
    from demand_research.models import PRIMITIVE_NAME, BASE_MEMBER_NAME

    member_name, member_key = normalize_target_member(target_member)
    if is_excluded_member(member_key):
        raise click.ClickException(
            f"Scope lock: target member '{target_member}' ({member_name}) is deferred at E0 and "
            "is NOT validated by this repo. This run validates the Base member only "
            f"({BASE_MEMBER_NAME}) under {PRIMITIVE_NAME}. Re-run with --target-member Base."
        )

    if from_file:
        hypothesis = load_hypothesis_from_file(from_file)
    else:
        # Prompt for missing values
        if not name:
            name = click.prompt("Product name")
        if not buyer:
            buyer = click.prompt("Target buyer")
        if not job:
            job = click.prompt("Buyer job (what are they trying to do?)")
        if not format:
            format = click.prompt("Product format (Notion template / spreadsheet / etc.)")
        if not channel:
            channel = click.prompt("Primary channel (Etsy / Gumroad / etc.)")
        if not gap:
            gap = click.prompt("Missing mechanism hypothesis")

        hypothesis = ProductHypothesis(
            product_name=name,
            target_buyer=buyer,
            buyer_job=job,
            product_format=format,
            primary_channel=channel,
            missing_mechanism_hypothesis=gap,
        )

    click.echo("\n" + "=" * 60)
    click.echo("Starting E1 Demand-Brief Review Workflow")
    click.echo(f"Primitive: {PRIMITIVE_NAME}")
    click.echo(f"Target Member: {member_name}")
    click.echo(f"Product: {hypothesis.product_name}")
    click.echo(f"Buyer: {hypothesis.target_buyer}")
    click.echo("=" * 60 + "\n")

    # Every run gets a durable truth-layer folder under runs/{run_id}/.
    from demand_research.audit.recorder import RunRecorder
    from demand_research.research.claude_researcher import ResearchUnavailableError

    recorder = RunRecorder(hypothesis, model_name=settings.anthropic_model)
    click.echo(f"Audit run folder: {recorder.run_dir}\n")

    # Run workflow. If research cannot run (e.g. no API credentials), fail
    # honestly rather than emitting a fabricated BUILD/REVISE/PARK/KILL verdict.
    try:
        brief = asyncio.run(
            run_research_async(hypothesis, recorder,
                               target_member=target_member, workflow_mode=workflow_mode)
        )
    except ResearchUnavailableError as exc:
        raise click.ClickException(
            f"Research could not run: {exc}\n"
            "Set ANTHROPIC_API_KEY in your environment and try again. "
            "No demand brief was written (refusing to fake a verdict). "
            f"A failed-run manifest was recorded at: {recorder.run_dir}"
        )

    # Generate outputs
    click.echo("\nGenerating outputs...")

    markdown_gen = MarkdownGenerator()
    markdown_path = markdown_gen.generate(brief)
    click.echo(f"✓ Markdown brief: {markdown_path}")
    click.echo(f"✓ Audit trail: {recorder.run_dir}")

    notion_gen = NotionGenerator()
    notion_result = notion_gen.generate(brief)
    if notion_result["status"] == "created":
        click.echo(f"✓ Notion page created: {notion_result.get('url', '')}")
    elif notion_result["status"] == "skipped":
        click.echo("• Notion skipped (set NOTION_API_KEY and NOTION_DATABASE_ID to enable)")
    else:
        click.echo(f"• Notion not created: {notion_result.get('reason', 'unknown error')}")

    # Summary — recording-readiness review (never "BUILD recommended" / "POST_E1").
    e1 = brief.e1_review or {}
    click.echo("\n" + "=" * 60)
    click.echo("E1 Demand-Brief Review Complete")
    click.echo("=" * 60)
    click.echo(f"Primitive: {e1.get('primitive_name', PRIMITIVE_NAME)}")
    click.echo(f"Target Member: {e1.get('target_member', member_name)}")
    click.echo(f"Current State: {e1.get('current_state', 'E0_AUTHORED_CAPTURED')}")
    click.echo(f"Candidate State: {e1.get('candidate_state', 'E1_CANDIDATE')}")
    click.echo(f"Review Verdict: {e1.get('review_verdict', brief.review_verdict or 'n/a')}")
    click.echo(f"Recording Status: {e1.get('recording_status', 'NOT_RECORDED')}")
    click.echo(f"B2 Acceptance: {e1.get('b2_acceptance_status', 'NOT_MET')}")
    click.echo(f"B3 Status: {e1.get('b3_status', 'LOCKED')}")
    click.echo(f"Public Execution Status: {e1.get('public_execution_status', 'NONE')}")
    click.echo(f"Evidence Quality: {brief.evidence_quality_score:.0%}")
    click.echo(f"Sources Collected: {len(brief.all_sources())}")
    click.echo(f"Run artifacts: {recorder.run_dir}")
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


async def run_research_async(
    hypothesis: ProductHypothesis,
    recorder=None,
    *,
    target_member: str = "Base",
    workflow_mode: str = "e1-demand-brief",
):
    """Run research workflow asynchronously."""
    orchestrator = ResearchOrchestrator()
    brief = await orchestrator.run_workflow(
        hypothesis, recorder=recorder,
        target_member=target_member, workflow_mode=workflow_mode,
    )
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
