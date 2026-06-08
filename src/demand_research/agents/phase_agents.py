"""Phase-specific agents for each research phase.

Each agent drives real web research via ClaudeResearcher (the Anthropic web
search tool), extracts structured source cards, validates them, and reports a
PASS/FAIL against its minimum-sources bar. The shared research→extract→
validate pipeline lives in BasePhaseAgent.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from pydantic import HttpUrl, ValidationError

from demand_research.models import (
    ProductHypothesis,
    PhaseResult,
    PhaseStatus,
    SourceCard,
)
from demand_research.research.claude_researcher import ClaudeResearcher
from demand_research.research.evidence_validator import EvidenceValidator
from demand_research.research.source_collector import SourceCollector

logger = logging.getLogger(__name__)

# Strict system prompt shared by every research pass. The anti-fabrication
# rules here are the heart of the methodology: real sources only.
RESEARCH_SYSTEM = (
    "You are a demand-research analyst. You use the web_search tool to find "
    "REAL, verifiable evidence about a product market. Absolute rules:\n"
    "- Only report things you actually found via search, with real URLs.\n"
    "- Never invent listings, prices, reviews, quotes, or keyword volumes.\n"
    "- Quote buyer language verbatim; if you only have a paraphrase, say so.\n"
    "- If the search genuinely surfaces nothing relevant, say so plainly. "
    "A clean 'no evidence found' is a valid and valuable result.\n"
    "Search thoroughly, then summarise what you found with the source URLs."
)

EXTRACT_SYSTEM = (
    "You convert research notes into strict JSON. Only include items that have "
    "a real source URL present in the notes. Do not invent data. If a field is "
    "unknown, use null. Output a single JSON object and nothing else."
)

# The JSON shape every extraction must follow (described in-prompt since we
# parse defensively rather than relying on a specific structured-output API).
_SOURCE_JSON_SHAPE = """Return JSON of this exact shape:
{
  "sources": [
    {
      "source_name": "short human label",
      "url": "https://real-source-url",
      "platform": "Etsy | Gumroad | Notion Marketplace | Reddit | YouTube | forum | other",
      "price": 19.99,                     // number, or null if not a priced listing
      "buyer_language": "verbatim quote", // or null if this source is not a buyer quote
      "is_direct_quote": true,            // true=verbatim, false=paraphrase/composite, null=n/a
      "what_it_proves": "one sentence",
      "what_it_does_not_prove": "one sentence",
      "gap_note": "what this competitor does/does not govern, or null"
    }
  ]
}
Only include sources whose URL actually appears in the findings."""


class BasePhaseAgent:
    """Base class with the shared research pipeline."""

    def __init__(
        self,
        phase_number: int,
        phase_name: str,
        min_sources: int = 3,
        researcher: Optional[ClaudeResearcher] = None,
    ):
        self.phase_number = phase_number
        self.phase_name = phase_name
        self.min_sources = min_sources
        self.validator = EvidenceValidator()
        self.collector = SourceCollector()
        self.researcher = researcher or ClaudeResearcher()

    async def run(self, *args: Any, **kwargs: Any) -> PhaseResult:
        """Override in subclass."""
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Shared pipeline
    # ------------------------------------------------------------------ #
    def _collect_sources(
        self,
        research_prompt: str,
        extract_instruction: str,
        default_platform: str,
        search_phrase: Optional[str] = None,
        recorder: Optional[Any] = None,
    ) -> tuple[list[SourceCard], str]:
        """Run web research -> structured extraction -> validation.

        Every search attempt, the exact phase prompt, and every rejected source
        are streamed to the run recorder (when present) so the run is auditable.

        Returns (validated_sources, findings_summary).
        """
        phase_id = f"phase_{self.phase_number}"
        if recorder is not None:
            recorder.log_phase_prompt(phase_id, self.phase_name, research_prompt)

        result = self.researcher.research(research_prompt, system=RESEARCH_SYSTEM)

        # Log searches even when the pass returns no usable text.
        if recorder is not None:
            recorder.log_searches(phase_id, self.phase_name, result.search_attempts)

        if not result.text:
            return [], "Web research returned no usable findings."

        # Give the extractor both the prose and the concrete source URLs the
        # web_search tool surfaced, so URLs aren't lost in summarisation.
        findings_for_extraction = result.text
        if result.citations:
            url_list = "\n".join(f"- {u}" for u in result.citations)
            findings_for_extraction = f"{result.text}\n\nSource URLs found:\n{url_list}"

        data = self.researcher.extract(
            findings_for_extraction,
            f"{extract_instruction}\n\n{_SOURCE_JSON_SHAPE}",
            system=EXTRACT_SYSTEM,
        )
        raw_sources = data.get("sources", []) if isinstance(data, dict) else []
        if recorder is not None:
            recorder.bump_raw(len(raw_sources))

        cards: list[SourceCard] = []
        for raw in raw_sources:
            card = self._build_card(len(cards) + 1, raw, default_platform, search_phrase)
            if card is None:
                # Could not even construct a card — almost always a missing/bad URL.
                if recorder is not None:
                    recorder.log_rejected(
                        phase_id, self.phase_name, raw if isinstance(raw, dict) else {},
                        reason="missing_url", validator_rule="build_card",
                    )
                continue
            ok, issues = self.validator.validate_source(card)
            if ok:
                cards.append(card)
            else:
                reason, rule = _classify_rejection(issues)
                logger.info(
                    "Dropped source %r in %s: %s",
                    raw.get("source_name") if isinstance(raw, dict) else raw,
                    self.phase_name,
                    "; ".join(issues),
                )
                if recorder is not None:
                    recorder.log_rejected(
                        phase_id, self.phase_name,
                        raw if isinstance(raw, dict) else {},
                        reason=reason, validator_rule=rule,
                    )
        return cards, result.text.strip()

    def _build_card(
        self,
        number: int,
        raw: Any,
        default_platform: str,
        search_phrase: Optional[str],
    ) -> Optional[SourceCard]:
        """Coerce a raw extracted dict into a validated SourceCard, or None."""
        if not isinstance(raw, dict):
            return None
        url = raw.get("url")
        if not url or not isinstance(url, str):
            return None
        try:
            return SourceCard(
                source_number=number,
                source_name=str(raw.get("source_name") or "Unnamed source"),
                url=HttpUrl(url),
                date_observed=datetime.utcnow(),
                platform=str(raw.get("platform") or default_platform),
                search_phrase_used=search_phrase,
                price_observed=_coerce_float(raw.get("price")),
                buyer_language_captured=_coerce_str(raw.get("buyer_language")),
                what_this_proves=str(raw.get("what_it_proves") or "Observed in market research."),
                what_this_does_not_prove=str(
                    raw.get("what_it_does_not_prove") or "Does not prove conversion demand."
                ),
                gap_note=_coerce_str(raw.get("gap_note")),
                is_direct_quote=_coerce_bool(raw.get("is_direct_quote")),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            logger.info("Could not build SourceCard from %r: %s", url, exc)
            return None

    def _result(
        self,
        status: PhaseStatus,
        sources: list[SourceCard],
        findings: str,
        reason: str,
        pass_condition: str,
    ) -> PhaseResult:
        return PhaseResult(
            phase_number=self.phase_number,
            phase_name=self.phase_name,
            status=status,
            sources_collected=sources,
            findings=findings,
            reason=reason,
            pass_condition=pass_condition,
        )


class Phase1Agent(BasePhaseAgent):
    """Phase 1: Signal Discovery — does the market/category exist at all?"""

    def __init__(self, researcher: Optional[ClaudeResearcher] = None):
        super().__init__(1, "Signal Discovery", min_sources=3, researcher=researcher)

    async def run(
        self, hypothesis: ProductHypothesis, recorder: Optional[Any] = None
    ) -> PhaseResult:
        logger.info("Phase 1: signal discovery — %s", hypothesis.product_name)
        product_type = self.collector.detect_product_type(
            hypothesis.product_name, hypothesis.target_buyer, hypothesis.primary_channel
        )
        research_prompt = (
            f"Find real market signals for this product idea.\n"
            f"Product: {hypothesis.product_name}\n"
            f"Target buyer: {hypothesis.target_buyer}\n"
            f"Buyer job: {hypothesis.buyer_job}\n"
            f"Format: {hypothesis.product_format}\n"
            f"Primary channel: {hypothesis.primary_channel}\n\n"
            "Search marketplaces and search engines (Etsy, Gumroad, Notion "
            "Marketplace, Google) for existing listings, categories, and search "
            "interest that show this buyer job and product category really exist. "
            "Report each real listing or category page you find with its URL."
        )
        extract_instruction = (
            "From the findings, list each real market signal (a live listing, "
            "category page, or search-interest source) as a source."
        )
        sources, findings = self._collect_sources(
            research_prompt,
            extract_instruction,
            default_platform=hypothesis.primary_channel,
            search_phrase=hypothesis.product_name,
            recorder=recorder,
        )
        passed = len(sources) >= self.min_sources
        return self._result(
            PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            sources,
            findings or f"Product type detected: {product_type}.",
            (
                f"Found {len(sources)} real market signals."
                if passed
                else f"Only {len(sources)} signals found; need {self.min_sources}."
            ),
            f"{self.min_sources}+ real market signals with URLs and dates",
        )


class Phase2Agent(BasePhaseAgent):
    """Phase 2: Buyer Language Mining — real pain points in buyers' own words."""

    def __init__(self, researcher: Optional[ClaudeResearcher] = None):
        super().__init__(2, "Buyer Language Mining", min_sources=3, researcher=researcher)

    async def run(
        self,
        hypothesis: ProductHypothesis,
        phase1_result: PhaseResult,
        recorder: Optional[Any] = None,
    ) -> PhaseResult:
        logger.info("Phase 2: buyer language — %s", hypothesis.product_name)
        research_prompt = (
            f"Find REAL buyer/seller language about the pain behind this job:\n"
            f"Buyer: {hypothesis.target_buyer}\n"
            f"Job: {hypothesis.buyer_job}\n"
            f"Product context (for your understanding only — do NOT search for the "
            f"product name): {hypothesis.product_name} ({hypothesis.product_format})\n\n"
            "CRITICAL — how to search. Real people almost never describe their pain "
            "in product or category terms. They vent in their own emotional, "
            "colloquial, problem-first words. If you only search product-shaped "
            "phrases you will miss how humans actually articulate this frustration. "
            "So generate a WIDE set of search queries from several different angles:\n"
            "  1. Emotional / venting phrasings: 'so tired of ...', 'I hate when ...', "
            "'why is it so hard to ...', 'sick of wasting time on ...', "
            "'anyone else struggle with ...', 'is it just me or ...'.\n"
            "  2. Problem-first, not solution-first: describe the underlying job and "
            "obstacle in plain language, NOT the product you imagine fixing it.\n"
            "  3. Symptom / workaround phrasings: how people describe the messy way "
            "they cope today (spreadsheets, sticky notes, 'I just wing it', etc.).\n"
            "  4. Question phrasings people actually type: 'how do I keep track of ...', "
            "'what do you use to ...', 'best way to ... without ...'.\n"
            "  5. Synonyms and adjacent vocabulary for the buyer and the job — the "
            "words THEY use for themselves, which may differ from industry terms.\n\n"
            "Run several distinct searches across these angles. Cast a wide net over "
            "Reddit (relevant subreddits and general search), Etsy/Gumroad/Notion "
            "reviews, YouTube comments, Quora, X/Twitter, Facebook groups, and niche "
            "forums. Prioritise unprompted complaints in the buyer's own words over "
            "marketing copy or your own paraphrase.\n\n"
            "Capture exact quotes with the source URL. Never paraphrase a quote into "
            "something the person did not say; if you only have a paraphrase, mark it "
            "as such. A genuine 'no real pain found in these words' is a valid result."
        )
        extract_instruction = (
            "From the findings, list each real buyer-language artifact. Put the "
            "verbatim quote in buyer_language and set is_direct_quote=true only "
            "when it is verbatim; use false for paraphrase/composite."
        )
        sources, findings = self._collect_sources(
            research_prompt, extract_instruction, default_platform="Reddit", recorder=recorder
        )
        # Hard gate: Phase 2 ACCEPTS only genuine, verbatim buyer-language
        # artifacts (Grade B). Any other candidate that survived URL validation
        # but is a listing / blog / paraphrase is a *considered-but-rejected*
        # source and is written durably to rejected_sources.jsonl — never
        # silently dropped. See docs/EVIDENCE_RULES.md.
        artifacts: list[SourceCard] = []
        considered = len(sources)
        for card in sources:
            if card.is_direct_quote and card.buyer_language_captured:
                artifacts.append(card)
            elif recorder is not None:
                reason, rule = _phase2_rejection(card)
                recorder.log_rejected(
                    f"phase_{self.phase_number}", self.phase_name,
                    {
                        "url": str(card.url),
                        "source_name": card.source_name,
                        "platform": card.platform,
                        "what_it_proves": card.what_this_proves,
                        "buyer_language": card.buyer_language_captured or "",
                    },
                    reason=reason, validator_rule=rule,
                )
        # Only verbatim artifacts count as accepted Phase 2 sources.
        passed = len(artifacts) >= self.min_sources
        return self._result(
            PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            artifacts,
            findings,
            (
                f"Captured {len(artifacts)} verbatim buyer-language artifacts "
                f"({considered} candidates considered)."
                if passed
                else (
                    f"Only {len(artifacts)} verbatim buyer-language artifacts "
                    f"({considered} candidates considered, "
                    f"{considered - len(artifacts)} rejected); need {self.min_sources}."
                )
            ),
            f"{self.min_sources}+ verbatim buyer-language artifacts (direct quotes with attribution)",
        )


class Phase3Agent(BasePhaseAgent):
    """Phase 3: Price Band Mapping — what comparable products actually cost."""

    def __init__(self, researcher: Optional[ClaudeResearcher] = None):
        super().__init__(3, "Price Band Mapping", min_sources=3, researcher=researcher)

    async def run(
        self,
        hypothesis: ProductHypothesis,
        phase2_result: PhaseResult,
        recorder: Optional[Any] = None,
    ) -> PhaseResult:
        logger.info("Phase 3: price band mapping — %s", hypothesis.product_name)
        research_prompt = (
            f"Find real prices of products comparable to: {hypothesis.product_name} "
            f"({hypothesis.product_format}) for {hypothesis.target_buyer}.\n\n"
            "Search Etsy, Gumroad, and Notion Marketplace for at least 3 distinct "
            "competing products. For each, capture the product name, exact price, "
            "and listing URL so price bands (low/mid/premium) can be mapped."
        )
        extract_instruction = (
            "From the findings, list each competitor product with a real, visible "
            "price in the price field and its listing URL."
        )
        sources, findings = self._collect_sources(
            research_prompt, extract_instruction,
            default_platform=hypothesis.primary_channel, recorder=recorder,
        )
        priced = [s for s in sources if s.price_observed is not None]
        passed = len(priced) >= self.min_sources
        band = _summarise_price_bands(priced)
        return self._result(
            PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            sources,
            f"{findings}\n\n{band}" if band else findings,
            (
                f"Mapped {len(priced)} competitor prices. {band}"
                if passed
                else f"Only {len(priced)} priced competitors found; need {self.min_sources}."
            ),
            f"{self.min_sources}+ competitor prices with URLs",
        )


class Phase4Agent(BasePhaseAgent):
    """Phase 4: Competitor Presence — what competitors do structurally."""

    def __init__(self, researcher: Optional[ClaudeResearcher] = None):
        super().__init__(4, "Competitor Presence", min_sources=3, researcher=researcher)

    async def run(
        self,
        hypothesis: ProductHypothesis,
        phase3_result: PhaseResult,
        recorder: Optional[Any] = None,
    ) -> PhaseResult:
        logger.info("Phase 4: competitor presence — %s", hypothesis.product_name)
        known = "\n".join(
            f"- {s.source_name}: {s.url}" for s in phase3_result.sources_collected
        )
        research_prompt = (
            "Analyse what these competing products actually DO structurally — not "
            "their look, but the mechanisms they force the buyer through. For each, "
            "assess whether it includes: demand validation, authorship evidence, a "
            "build-readiness gate, a listing-readiness gate, fee-stress logic, and a "
            "post-launch decision loop.\n\n"
            f"Known competitors from pricing research:\n{known or '(none captured)'}\n\n"
            f"Also search for more competitors to: {hypothesis.product_name} for "
            f"{hypothesis.target_buyer}. Report each with its URL and what it does "
            "vs. does not appear to govern."
        )
        extract_instruction = (
            "From the findings, list each competitor analysed. Put the structural "
            "read (what it governs and, crucially, what it does NOT govern) in "
            "gap_note, with the competitor URL in url."
        )
        sources, findings = self._collect_sources(
            research_prompt, extract_instruction,
            default_platform=hypothesis.primary_channel, recorder=recorder,
        )
        # Fall back to Phase 3 competitors if fresh structural search was thin.
        if len(sources) < self.min_sources and phase3_result.sources_collected:
            sources = _merge_unique(sources, phase3_result.sources_collected)
        passed = len(sources) >= self.min_sources
        return self._result(
            PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            sources,
            findings,
            (
                f"Structurally analysed {len(sources)} competitors."
                if passed
                else f"Only {len(sources)} competitors analysed; need {self.min_sources}."
            ),
            f"{self.min_sources}+ competitors with structural teardown",
        )


class Phase5Agent(BasePhaseAgent):
    """Phase 5: Missing-Mechanism Gap — name the specific structural gap."""

    def __init__(self, researcher: Optional[ClaudeResearcher] = None):
        # Synthesis phase: it judges, it doesn't collect new sources.
        super().__init__(5, "Missing-Mechanism Gap", min_sources=0, researcher=researcher)

    async def run(
        self,
        hypothesis: ProductHypothesis,
        phase4_result: PhaseResult,
        recorder: Optional[Any] = None,
    ) -> PhaseResult:
        logger.info("Phase 5: missing-mechanism gap — %s", hypothesis.product_name)
        competitor_summary = "\n".join(
            f"- {s.source_name} ({s.url}): {s.gap_note or 'structure not captured'}"
            for s in phase4_result.sources_collected
        ) or "(no competitor structures captured)"

        instruction = (
            "Decide whether the proposed product fills a STRUCTURAL gap (a "
            "mechanism it forces the buyer through that competitors do not), as "
            "opposed to a merely aesthetic or feature-only difference.\n\n"
            f"Proposed missing mechanism (hypothesis): {hypothesis.missing_mechanism_hypothesis}\n\n"
            f"Competitor structures observed:\n{competitor_summary}\n\n"
            "Apply the test: if a competitor added better design, more pages, or "
            "lower price, would this product still be structurally different? "
            'Return JSON: {"is_structural": true|false, "gap_statement": "...", '
            '"reason": "..."}'
        )
        if recorder is not None:
            recorder.log_phase_prompt(f"phase_{self.phase_number}", self.phase_name, instruction)
        data = self.researcher.extract(
            findings=competitor_summary,
            instruction=instruction,
            system=EXTRACT_SYSTEM,
        )
        is_structural = _coerce_bool(data.get("is_structural")) if isinstance(data, dict) else None
        gap_statement = (data.get("gap_statement") if isinstance(data, dict) else None) or ""
        reason = (data.get("reason") if isinstance(data, dict) else None) or ""

        passed = bool(is_structural) and len(gap_statement.strip()) > 0
        findings = gap_statement.strip() or (
            "Could not articulate a structural gap from the competitor analysis."
        )
        # Phase 5 references the competitor sources rather than collecting new ones.
        return self._result(
            PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            phase4_result.sources_collected,
            findings,
            reason.strip() or ("Structural gap identified." if passed else "Gap appears non-structural."),
            "Specific, structural missing mechanism identified (not feature-based)",
        )


# ---------------------------------------------------------------------- #
# Rejection classification
# ---------------------------------------------------------------------- #
def _classify_rejection(issues: list[str]) -> tuple[str, str]:
    """Map validator issue strings to a durable rejection_reason + rule.

    Returns the first (most specific) matching reason so the rejected-source
    log is queryable by category, not just free text.
    """
    joined = " ; ".join(issues)
    low = joined.lower()
    # Order matters: most specific / most serious first.
    if "ai-generated" in low or "generic/templated" in low:
        return "ai_speak_detected", joined
    if "placeholder domain" in low:
        return "inaccessible", joined
    if "url is missing" in low or "does not use http" in low:
        return "missing_url", joined
    if "suspiciously short" in low:
        return "inaccessible", joined
    if "older than" in low or "future" in low:
        return "stale", joined
    if "quote is missing" in low:
        return "missing_required_quote", joined
    if "price" in low:
        return "other", joined
    return "other", joined


def _phase2_rejection(card: SourceCard) -> tuple[str, str]:
    """Classify why a Phase 2 candidate failed the buyer-language requirement.

    A candidate reaches here only if it survived URL validation but did not
    yield a verbatim Grade-B artifact.
    """
    rule = "phase2_buyer_language_requirement"
    if not card.buyer_language_captured:
        # No quote at all — a listing / blog / category page (Grade C content).
        return "no_direct_buyer_language", rule
    if not card.is_direct_quote:
        # A quote field exists but it is a paraphrase / composite, not verbatim.
        return "missing_required_quote", rule
    return "wrong_artifact_type", rule


# ---------------------------------------------------------------------- #
# Coercion + small helpers
# ---------------------------------------------------------------------- #
def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().lstrip("$").replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "yes", "y"}:
            return True
        if low in {"false", "no", "n"}:
            return False
    return None


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _summarise_price_bands(priced: list[SourceCard]) -> str:
    prices = sorted(p.price_observed for p in priced if p.price_observed is not None)
    if not prices:
        return ""
    low = ", ".join(f"${p:g}" for p in prices)
    return f"Observed prices: {low} (low ${prices[0]:g} / high ${prices[-1]:g})."


def _merge_unique(primary: list[SourceCard], extra: list[SourceCard]) -> list[SourceCard]:
    seen = {str(s.url) for s in primary}
    merged = list(primary)
    for s in extra:
        if str(s.url) not in seen:
            seen.add(str(s.url))
            merged.append(s)
    return merged
