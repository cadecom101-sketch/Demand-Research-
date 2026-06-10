"""Free/public evidence-connector registry (capability + audit layer).

This module declares WHICH evidence surfaces a run can observe and HOW, and
records that honestly in the audit trail. It is deliberately NOT a scraping
layer: the Anthropic web-search research provider remains the only real
collection mechanism, and the public-platform connectors below describe
surfaces reachable *through* it. Nothing here:

  - fabricates evidence or invents availability;
  - prompts for credentials or bypasses access restrictions;
  - crashes the workflow when a connector is unavailable (unavailable means
    "skipped and recorded", never "error");
  - changes any gate, threshold, or verdict.

Statuses:
  - available:        directly usable this run;
  - via_web_search:   public surface reachable only through the web-search
                      research provider (no direct scraper/API is configured);
  - not_configured:   needs configuration (e.g. an API key) that is absent —
                      the run proceeds without it, no prompt is issued;
  - unavailable:      capability is missing at runtime (e.g. no browser);
  - blocked:          the surface refused access (recorded when observed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

CONNECTOR_STATUSES = (
    "available", "via_web_search", "not_configured", "unavailable", "blocked",
)


def _utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ConnectorDescriptor:
    """One evidence connector: what it reaches, what it needs, how it reports.

    `probe` returns (status, detail) and must never raise, prompt for
    credentials, or perform network access at registry-build time.
    """

    name: str
    kind: str  # research_provider | capability | public_source
    description: str
    phases: List[int] = field(default_factory=list)
    requires_credentials: List[str] = field(default_factory=list)
    probe: Optional[Callable[[], tuple]] = None

    def audit_record(self) -> dict:
        status, detail = "unavailable", "No probe defined."
        if self.probe is not None:
            try:
                status, detail = self.probe()
            except Exception as exc:  # noqa: BLE001 — a probe must never crash a run
                status, detail = "unavailable", f"Probe failed safely: {exc}"
        if status not in CONNECTOR_STATUSES:
            status, detail = "unavailable", f"Probe returned unknown status {status!r}."
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "phases": list(self.phases),
            "requires_credentials": list(self.requires_credentials),
            "status": status,
            "detail": detail,
            "checked_utc": _utc_now_iso(),
        }


class ConnectorRegistry:
    """Tracks evidence connectors and produces the per-run audit record."""

    def __init__(self):
        self._connectors: Dict[str, ConnectorDescriptor] = {}

    def register(self, descriptor: ConnectorDescriptor) -> None:
        self._connectors[descriptor.name] = descriptor

    def get(self, name: str) -> Optional[ConnectorDescriptor]:
        return self._connectors.get(name)

    def names(self) -> List[str]:
        return list(self._connectors)

    def audit(self) -> List[dict]:
        """One audit record per connector. Never raises; never prompts."""
        return [d.audit_record() for d in self._connectors.values()]

    def usable_for_phase(self, phase: int) -> List[dict]:
        """Connectors that can contribute to a phase this run (directly or
        through the web-search provider). Unusable connectors are simply not
        listed — the phase proceeds with whatever remains."""
        return [
            r for r in self.audit()
            if phase in r["phases"]
            and r["status"] in ("available", "via_web_search")
        ]

    def summary(self) -> dict:
        records = self.audit()
        by_status: Dict[str, int] = {}
        for r in records:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        return {
            "total": len(records),
            "by_status": by_status,
            "usable": sorted(
                r["name"] for r in records
                if r["status"] in ("available", "via_web_search")
            ),
            "unusable": sorted(
                r["name"] for r in records
                if r["status"] not in ("available", "via_web_search")
            ),
        }


# --------------------------------------------------------------------------- #
# Default registry for this repo
# --------------------------------------------------------------------------- #
# Public surfaces the methodology draws evidence from. They are observed
# THROUGH the web-search research provider; no direct scraper or platform API
# exists in this repo (and none is stubbed into pretending it does).
_PUBLIC_SOURCES = [
    ("etsy_public", "Etsy public listings, categories, and reviews", [1, 2, 3, 4]),
    ("gumroad_public", "Gumroad Discover listings and public product pages", [1, 3, 4]),
    ("notion_marketplace_public", "Notion Marketplace public template listings", [1, 3, 4]),
    ("reddit_public", "Public Reddit threads and comments", [1, 2]),
    ("youtube_public", "Public YouTube videos and comments", [2]),
    ("google_trends_public", "Google Trends public search-interest pages", [1]),
    ("facebook_groups_public", "Public Facebook group discussions", [2]),
]


def build_default_registry(
    *,
    anthropic_api_key_present: Optional[bool] = None,
    screenshot_capture: Optional[object] = None,
) -> ConnectorRegistry:
    """Build the default registry from runtime capability checks only.

    No network access, no credential prompts, no exceptions. When
    `anthropic_api_key_present` is None it is read from settings; pass an
    explicit bool in tests.
    """
    if anthropic_api_key_present is None:
        try:
            from demand_research.config import settings
            anthropic_api_key_present = bool(settings.anthropic_api_key)
        except Exception:  # noqa: BLE001 — settings must never break registry build
            anthropic_api_key_present = False

    registry = ConnectorRegistry()

    def _web_search_probe() -> tuple:
        if anthropic_api_key_present:
            return ("available",
                    "Anthropic web-search research provider is configured; all public "
                    "sources are observed through it.")
        return ("not_configured",
                "ANTHROPIC_API_KEY is not set. The run cannot perform live research; "
                "no credential prompt is issued.")

    registry.register(ConnectorDescriptor(
        name="anthropic_web_search",
        kind="research_provider",
        description=(
            "Primary research provider: Anthropic web-search tool. The only real "
            "evidence-collection mechanism in this repo."
        ),
        phases=[1, 2, 3, 4],
        requires_credentials=["ANTHROPIC_API_KEY"],
        probe=_web_search_probe,
    ))

    def _public_probe() -> tuple:
        if anthropic_api_key_present:
            return ("via_web_search",
                    "Public surface reachable through the web-search research provider; "
                    "no direct scraper/API is configured (none is needed, and none is "
                    "faked).")
        return ("unavailable",
                "Reachable only through the web-search research provider, which is "
                "not configured this run.")

    for name, description, phases in _PUBLIC_SOURCES:
        registry.register(ConnectorDescriptor(
            name=name,
            kind="public_source",
            description=description,
            phases=phases,
            requires_credentials=[],
            probe=_public_probe,
        ))

    def _screenshot_probe() -> tuple:
        if screenshot_capture is None:
            return ("not_configured",
                    "Screenshot capture is not configured for this run "
                    "(ENABLE_SCREENSHOTS is off).")
        try:
            if not getattr(screenshot_capture, "configured", False):
                return ("not_configured", screenshot_capture.availability_detail())
            if screenshot_capture.is_available():
                return ("available", screenshot_capture.availability_detail())
            return ("unavailable", screenshot_capture.availability_detail())
        except Exception as exc:  # noqa: BLE001
            return ("unavailable", f"Screenshot capability probe failed safely: {exc}")

    registry.register(ConnectorDescriptor(
        name="screenshot_capture",
        kind="capability",
        description=(
            "Passive screenshot capture of accepted public evidence pages for "
            "audit completeness. Never evidence by itself; never satisfies a gate."
        ),
        phases=[1, 2, 3, 4],
        requires_credentials=[],
        probe=_screenshot_probe,
    ))

    return registry
