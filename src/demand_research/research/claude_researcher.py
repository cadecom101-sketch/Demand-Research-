"""Claude-powered web research — the 'eyes and hands' of the workflow.

Uses the Anthropic API's native server-side web search tool to perform real
web research, then a structured-extraction pass to turn findings into typed
data. The Anthropic client is dependency-injected so the logic can be tested
with a fake client and run live wherever ANTHROPIC_API_KEY is configured.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)

# Latest web search tool version (server-side, with dynamic filtering) — see
# the Anthropic tool-use docs. Works on Opus 4.6+ / Sonnet 4.6.
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}

# Bound the server-side pause_turn continuation loop so a runaway search can't
# spin forever.
MAX_PAUSE_CONTINUATIONS = 5


class ResearchUnavailableError(RuntimeError):
    """Raised when research cannot run (e.g. missing API credentials).

    This is deliberately distinct from "research ran and found nothing" — the
    workflow must never turn an infrastructure failure into a fake KILL verdict.
    """


class _AnthropicLike(Protocol):
    """Minimal protocol of the Anthropic client surface we use (for testing)."""

    @property
    def messages(self) -> Any: ...


@dataclass
class ResearchResult:
    """Output of a web-research pass."""

    text: str
    citations: list[str] = field(default_factory=list)
    # Provider-level search attempts captured from server_tool_use /
    # web_search_tool_result blocks: each is a dict with query, status,
    # result_count, result_urls, error_message, search_provider.
    search_attempts: list[dict] = field(default_factory=list)


def _default_client() -> _AnthropicLike:
    """Construct a real Anthropic client, translating missing-credential
    failures into a clear ResearchUnavailableError."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency guaranteed by pyproject
        raise ResearchUnavailableError(
            "The 'anthropic' package is not installed. Run: pip install anthropic"
        ) from exc

    try:
        return anthropic.Anthropic()
    except Exception as exc:  # noqa: BLE001 - SDK raises a bare error on missing key
        raise ResearchUnavailableError(
            "Could not initialise the Anthropic client. Set ANTHROPIC_API_KEY "
            "(or ANTHROPIC_AUTH_TOKEN) in the environment to run autonomous research."
        ) from exc


class ClaudeResearcher:
    """Runs real web research and structured extraction via the Anthropic API."""

    def __init__(
        self,
        client: Optional[_AnthropicLike] = None,
        model: str = "claude-opus-4-8",
        effort: str = "high",
    ):
        self._client = client
        self.model = model
        self.effort = effort

    @property
    def client(self) -> _AnthropicLike:
        if self._client is None:
            self._client = _default_client()
        return self._client

    # ------------------------------------------------------------------ #
    # Stage 1: real web research (server-side web_search tool)
    # ------------------------------------------------------------------ #
    def research(self, prompt: str, system: str) -> ResearchResult:
        """Run an agentic web-search loop and return collected text + URLs.

        Handles the server-side `pause_turn` stop reason by re-sending the
        conversation so Claude can resume its search.
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                system=system,
                tools=[WEB_SEARCH_TOOL],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                messages=messages,
            )
        except ResearchUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ResearchUnavailableError(f"Web research request failed: {exc}") from exc

        # Accumulate search attempts across the whole pause_turn loop so the
        # audit trail captures every query, not just the final turn's.
        attempts: list[dict] = self._collect_search_attempts(response)

        continuations = 0
        while getattr(response, "stop_reason", None) == "pause_turn" and continuations < MAX_PAUSE_CONTINUATIONS:
            messages.append({"role": "assistant", "content": response.content})
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                system=system,
                tools=[WEB_SEARCH_TOOL],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                messages=messages,
            )
            attempts.extend(self._collect_search_attempts(response))
            continuations += 1

        text = self._collect_text(response)
        citations = self._collect_citations(response)
        logger.info(
            "Web research returned %d chars, %d citations, %d search attempts",
            len(text), len(citations), len(attempts),
        )
        return ResearchResult(text=text, citations=citations, search_attempts=attempts)

    # ------------------------------------------------------------------ #
    # Stage 2: structured extraction (no tools -> no citation conflict)
    # ------------------------------------------------------------------ #
    def extract(self, findings: str, instruction: str, system: str) -> dict[str, Any]:
        """Turn free-form research findings into a structured dict.

        Uses a plain (tool-free) call and defensively parses JSON from the
        response, so it does not depend on a specific structured-output API
        version. Every extracted field is re-validated downstream by Pydantic
        and the EvidenceValidator, so malformed model output fails closed.
        """
        prompt = (
            f"{instruction}\n\n"
            "Return ONLY a single valid JSON object, no prose, no markdown fences.\n\n"
            "=== RESEARCH FINDINGS ===\n"
            f"{findings}"
        )
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                system=system,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                messages=[{"role": "user", "content": prompt}],
            )
        except ResearchUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ResearchUnavailableError(f"Extraction request failed: {exc}") from exc

        text = self._collect_text(response)
        return self._parse_json(text)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _collect_text(response: Any) -> str:
        parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "\n".join(parts).strip()

    @staticmethod
    def _collect_citations(response: Any) -> list[str]:
        """Pull real source URLs out of web_search result blocks and text
        citations, de-duplicated and order-preserving."""
        urls: list[str] = []
        seen: set[str] = set()

        def _add(url: Optional[str]) -> None:
            if url and url not in seen:
                seen.add(url)
                urls.append(url)

        for block in getattr(response, "content", []) or []:
            btype = getattr(block, "type", None)
            # web_search_tool_result blocks carry a list of results with urls
            if btype == "web_search_tool_result":
                content = getattr(block, "content", None) or []
                for item in content:
                    _add(getattr(item, "url", None))
            # text blocks may carry citations referencing source urls
            for citation in getattr(block, "citations", None) or []:
                _add(getattr(citation, "url", None))
        return urls

    @staticmethod
    def _collect_search_attempts(response: Any) -> list[dict]:
        """Pair each server_tool_use web_search query with its result block.

        This captures the *actual* queries the server-side tool ran, plus the
        result count and a results_found / zero_results / tool_error /
        rate_limited status — logged whether or not anything was found.
        """
        attempts: list[dict] = []
        pending_query: Optional[str] = None

        def _get(obj: Any, key: str) -> Any:
            if isinstance(obj, dict):
                return obj.get(key)
            return getattr(obj, key, None)

        for block in getattr(response, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "server_tool_use" and getattr(block, "name", None) == "web_search":
                inp = getattr(block, "input", None)
                pending_query = _get(inp, "query") if inp is not None else None
            elif btype == "web_search_tool_result":
                content_val = getattr(block, "content", None)
                query = pending_query or ""
                if isinstance(content_val, list):
                    urls = [u for u in (_get(i, "url") for i in content_val) if u]
                    attempts.append({
                        "query": query,
                        "result_count": len(content_val),
                        "result_urls": urls,
                        "status": "results_found" if content_val else "zero_results",
                        "error_message": None,
                        "search_provider": "anthropic_web_search",
                    })
                else:
                    err = _get(content_val, "error_code") or _get(content_val, "error")
                    rate_codes = {"max_uses_exceeded", "too_many_requests", "rate_limited"}
                    status = "rate_limited" if str(err) in rate_codes else "tool_error"
                    attempts.append({
                        "query": query,
                        "result_count": 0,
                        "result_urls": [],
                        "status": status,
                        "error_message": str(err) if err else "search tool error",
                        "search_provider": "anthropic_web_search",
                    })
                pending_query = None
        return attempts

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Defensively extract a JSON object from model output."""
        if not text:
            return {}
        # Strip code fences if present.
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        candidate = fenced.group(1) if fenced else text
        # Fall back to the first balanced-looking object.
        if not fenced:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = candidate[start : end + 1]
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("Could not parse JSON from extraction output")
            return {}
