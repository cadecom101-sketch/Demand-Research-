"""Generate a Notion page for a demand brief.

When NOTION_API_KEY (and NOTION_DATABASE_ID) are configured, this pushes a real
page to Notion via the REST API. Without credentials it returns a 'skipped'
status and the block structure, so the workflow stays fully autonomous where
configured and degrades cleanly where not.
"""

import logging
from typing import Any

import httpx

from demand_research.models import DemandBrief, PhaseResult, PhaseStatus
from demand_research.config import settings

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1/pages"
NOTION_VERSION = "2022-06-28"
MAX_BLOCKS = 90  # Notion caps children at 100 per create; keep headroom.


def _text(content: str) -> dict[str, Any]:
    # Notion rich_text caps at 2000 chars per text object.
    return {"type": "text", "text": {"content": content[:1900]}}


def _para(content: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [_text(content)]}}


def _heading(content: str, level: int = 2) -> dict[str, Any]:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": [_text(content)]}}


def _bullet(content: str) -> dict[str, Any]:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [_text(content)]}}


class NotionGenerator:
    """Builds Notion blocks for a brief and optionally pushes them to Notion."""

    def __init__(self) -> None:
        self.api_key = settings.notion_api_key
        self.database_id = settings.notion_database_id

    def generate(self, brief: DemandBrief) -> dict[str, Any]:
        """Build blocks and, if credentials exist, create the Notion page.

        Returns a status dict: {status, url|reason, blocks}.
        """
        blocks = self._build_blocks(brief)

        if not (self.api_key and self.database_id):
            logger.info("Notion credentials not set; skipping live page creation.")
            return {"status": "skipped",
                    "reason": "NOTION_API_KEY / NOTION_DATABASE_ID not configured",
                    "blocks": blocks}

        try:
            return self._create_page(brief, blocks)
        except Exception as exc:  # noqa: BLE001 - never let Notion failure abort the run
            logger.warning("Notion page creation failed: %s", exc)
            return {"status": "error", "reason": str(exc), "blocks": blocks}

    # ------------------------------------------------------------------ #
    def _create_page(self, brief: DemandBrief, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        payload = {
            "parent": {"database_id": self.database_id},
            "properties": {
                "Name": {
                    "title": [_text(f"Demand Brief: {brief.product_hypothesis.product_name}")]
                }
            },
            "children": blocks[:MAX_BLOCKS],
        }
        resp = httpx.post(NOTION_API, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        url = data.get("url", "")
        logger.info("Notion page created: %s", url)
        return {"status": "created", "url": url, "page_id": data.get("id", "")}

    # ------------------------------------------------------------------ #
    def _build_blocks(self, brief: DemandBrief) -> list[dict[str, Any]]:
        hyp = brief.product_hypothesis
        blocks: list[dict[str, Any]] = [
            _heading("Executive Summary", 1),
            _para(f"Decision: {brief.decision.value}  |  "
                  f"Evidence quality: {brief.evidence_quality_score:.0%}"),
            _para(brief.decision_reasoning),
            _heading("Product Hypothesis", 1),
            _bullet(f"Target buyer: {hyp.target_buyer}"),
            _bullet(f"Buyer job: {hyp.buyer_job}"),
            _bullet(f"Format: {hyp.product_format}"),
            _bullet(f"Channel: {hyp.primary_channel}"),
            _bullet(f"Gap hypothesis: {hyp.missing_mechanism_hypothesis}"),
        ]

        for phase in (
            brief.phase_1_result, brief.phase_2_result, brief.phase_3_result,
            brief.phase_4_result, brief.phase_5_result,
        ):
            if phase is None:
                continue
            mark = "PASS" if phase.status == PhaseStatus.PASS else "FAIL"
            blocks.append(_heading(f"[{mark}] Phase {phase.phase_number}: {phase.phase_name}", 2))
            blocks.append(_para(phase.findings))
            for s in phase.sources_collected[:5]:
                blocks.append(_bullet(f"{s.source_name} — {s.url}"))

        return blocks
