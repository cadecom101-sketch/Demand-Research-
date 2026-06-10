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
from demand_research.research.claude_researcher import (
    ClaudeResearcher,
    chunk_text,
    dedupe_raw_sources,
)
from demand_research.research.evidence_validator import EvidenceValidator
from demand_research.research.query_planner import prompt_appendix
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
    "unknown, omit it or use null. Output a single JSON object and nothing else: "
    "no markdown, no code fences, no prose. Include at most 10 sources. Keep every "
    "text field under 200 characters and on a single line (no embedded newlines). "
    "If you are running low on room, return FEWER complete source objects rather "
    "than a truncated final object."
)

# Output-discipline rules appended to every extraction instruction. They reduce
# the chance of truncated/malformed JSON; they do not change any evidence bar.
_EXTRACTION_RULES = (
    "STRICT OUTPUT RULES (follow exactly):\n"
    "- Output JSON only. No markdown, no code fences, no explanation.\n"
    "- At most 10 sources in the \"sources\" array.\n"
    "- Every text field <= 200 characters.\n"
    "- No newline characters inside any string value.\n"
    "- Omit unknown fields or set them to null; never guess.\n"
    "- Always close every object and the array. If space runs short, emit fewer "
    "COMPLETE source objects rather than a cut-off final object."
)

# Research findings longer than this are extracted in smaller chunks so a single
# over-long response cannot truncate and lose everything.
_EXTRACTION_CHUNK_CHAR_LIMIT = 12000

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
      "currency": "USD",                  // or null
      "product_type": "Notion template | spreadsheet | ... | null",
      "what_it_promises": "the listing's main promise, or null",
      "features_included": ["feature", "..."],   // or null
      "buyer_language": "verbatim quote", // or null if this source is not a buyer quote
      "is_direct_quote": true,            // true=verbatim, false=paraphrase/composite, null=n/a
      "what_it_proves": "one sentence",
      "what_it_does_not_prove": "one sentence",
      "gap_note": "what this competitor does/does not govern, or null",
      "target_buyer": "who the competitor targets, or null",
      "main_promise": "competitor main promise, or null",
      "what_it_structurally_does": "tracker/planner/dashboard/gated workflow/..., or null",
      "what_it_does_not_govern": "the decision it does NOT force, or null",
      "teardown": {                        // Phase 4 only; use null elsewhere
        "demand_validation": "none|weak|present|strong|unknown",
        "authorship_evidence": "none|weak|present|strong|unknown",
        "build_readiness_gate": "none|weak|present|strong|unknown",
        "listing_readiness_gate": "none|weak|present|strong|unknown",
        "fee_stress_logic": "none|weak|present|strong|unknown",
        "post_launch_decision_loop": "none|weak|present|strong|unknown"
      }
    }
  ]
}
Only include sources whose URL actually appears in the findings."""

# Allowed 6-dimension teardown scores (Phase 4).
_TEARDOWN_DIMS = (
    "demand_validation", "authorship_evidence", "build_readiness_gate",
    "listing_readiness_gate", "fee_stress_logic", "post_launch_decision_loop",
)
_TEARDOWN_SCORES = {"none", "weak", "present", "strong", "unknown"}


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
        hypothesis: Optional[ProductHypothesis] = None,
    ) -> tuple[list[SourceCard], str]:
        """Run web research -> structured extraction -> validation.

        Every search attempt, the exact phase prompt, and every rejected source
        are streamed to the run recorder (when present) so the run is auditable.
        When a `hypothesis` is supplied, diversified query families (the query
        planner) are appended to the research prompt so the search casts a wider,
        smarter net — this changes no gate or threshold, only search quality.

        Returns (validated_sources, findings_summary).
        """
        phase_id = f"phase_{self.phase_number}"
        if hypothesis is not None:
            appendix = prompt_appendix(hypothesis, self.phase_number)
            if appendix:
                research_prompt = f"{research_prompt}\n{appendix}"
        if recorder is not None:
            recorder.log_phase_prompt(phase_id, self.phase_name, research_prompt)

        result = self.researcher.research(research_prompt, system=RESEARCH_SYSTEM)

        # Log searches even when the pass returns no usable text.
        if recorder is not None:
            recorder.log_searches(phase_id, self.phase_name, result.search_attempts)
            # Persist the EXACT research text (+ citation/search sidecar) BEFORE
            # extraction, so a downstream parse failure is still debuggable.
            recorder.write_raw_research(
                self.phase_number, result.text,
                citations=result.citations, search_attempts=result.search_attempts,
            )

        if not result.text:
            return [], "Web research returned no usable findings."

        # Give the extractor both the prose and the concrete source URLs the
        # web_search tool surfaced, so URLs aren't lost in summarisation.
        findings_for_extraction = result.text
        if result.citations:
            url_list = "\n".join(f"- {u}" for u in result.citations)
            findings_for_extraction = f"{result.text}\n\nSource URLs found:\n{url_list}"

        full_instruction = f"{extract_instruction}\n\n{_SOURCE_JSON_SHAPE}\n\n{_EXTRACTION_RULES}"
        raw_sources = self._run_extraction(findings_for_extraction, full_instruction, recorder)
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

    def _run_extraction(
        self,
        findings: str,
        instruction: str,
        recorder: Optional[Any],
    ) -> list:
        """Extract raw source dicts from findings — chunked + salvage-aware.

        Long research text is split into smaller extraction chunks so a single
        truncated response cannot lose everything. Each chunk is parsed and
        salvaged independently; complete source objects are merged and de-duped by
        (url, source_name). One bad chunk never erases good chunks. The exact
        per-phase and per-chunk raw responses are persisted before parsing is
        trusted. Nothing is fabricated: a chunk that yields no complete object
        contributes nothing.
        """
        chunks = chunk_text(findings, _EXTRACTION_CHUNK_CHAR_LIMIT)
        chunked = len(chunks) > 1
        raw_parts: list[str] = []
        merged: list = []

        for idx, chunk in enumerate(chunks, start=1):
            extraction = self.researcher.extract(chunk, instruction, system=EXTRACT_SYSTEM)
            raw_parts.append(extraction.raw_text)
            chunk_id = f"chunk_{idx}" if chunked else None
            if recorder is not None:
                if chunked:
                    recorder.write_raw_extraction_chunk(self.phase_number, idx, extraction.raw_text)
                if extraction.parse_error is not None:
                    pe = extraction.parse_error
                    recorder.log_extraction_error(
                        self.phase_number,
                        error_type=pe.get("error_type", "parse_error"),
                        error_message=pe.get("error_message", ""),
                        parser_step=pe.get("parser_step", ""),
                        raw_preview=extraction.raw_text,
                        line=pe.get("line"), column=pe.get("column"),
                        char_position=pe.get("char_position"), excerpt=pe.get("excerpt"),
                        chunk_id=chunk_id,
                    )
                elif extraction.salvage is not None:
                    recorder.log_extraction_salvage(
                        self.phase_number, extraction.salvage, chunk_id=chunk_id,
                    )
            data = extraction.data if isinstance(extraction.data, dict) else {}
            srcs = data.get("sources", []) if isinstance(data, dict) else []
            if isinstance(srcs, list):
                merged.extend(s for s in srcs if isinstance(s, dict))

        # Persist the combined per-phase raw response (compatibility filename),
        # whether or not the pass was chunked.
        if recorder is not None:
            combined = (
                raw_parts[0] if not chunked
                else "\n\n=== CHUNK SPLIT ===\n\n".join(raw_parts)
            )
            recorder.write_raw_extraction(self.phase_number, combined)

        return dedupe_raw_sources(merged)

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
                competitor_features_observed=_coerce_str_list(raw.get("features_included")),
                what_this_proves=str(raw.get("what_it_proves") or "Observed in market research."),
                what_this_does_not_prove=str(
                    raw.get("what_it_does_not_prove") or "Does not prove conversion demand."
                ),
                gap_note=_coerce_str(raw.get("gap_note")),
                is_direct_quote=_coerce_bool(raw.get("is_direct_quote")),
                details=_extract_details(raw),
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
            hypothesis=hypothesis,
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
            research_prompt, extract_instruction, default_platform="Reddit",
            recorder=recorder, hypothesis=hypothesis,
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
                # Stricter classification: a glowing/generic-satisfaction quote
                # proves the category sells, NOT the target buyer pain a Phase 2
                # artifact must demonstrate. Reject it durably rather than count
                # it as pain. (This can only reduce accepted buyer language.)
                if self.validator.is_generic_satisfaction_quote(card.buyer_language_captured):
                    if recorder is not None:
                        recorder.log_rejected(
                            f"phase_{self.phase_number}", self.phase_name,
                            {
                                "url": str(card.url),
                                "source_name": card.source_name,
                                "platform": card.platform,
                                "what_it_proves": card.what_this_proves,
                                "buyer_language": card.buyer_language_captured or "",
                            },
                            reason="generic_satisfaction_not_pain",
                            validator_rule="phase2_pain_requirement",
                        )
                    continue
                # Generic NEGATIVE reviews ("bad download", "seller was rude",
                # "too expensive") prove product dissatisfaction, not the
                # target buyer pain — rejected durably unless the exact quote
                # connects to the target job. Negative remarks that name the
                # target pain ("didn't help me know what product to make",
                # "still got no sales") pass through and count.
                if self.validator.is_generic_negative_review(card.buyer_language_captured):
                    if recorder is not None:
                        recorder.log_rejected(
                            f"phase_{self.phase_number}", self.phase_name,
                            {
                                "url": str(card.url),
                                "source_name": card.source_name,
                                "platform": card.platform,
                                "what_it_proves": card.what_this_proves,
                                "buyer_language": card.buyer_language_captured or "",
                            },
                            reason="generic_negative_not_target_pain",
                            validator_rule="phase2_target_pain_requirement",
                        )
                    continue
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
            "price in the price field and its listing URL. Also capture currency, "
            "product_type, what_it_promises, and features_included where visible."
        )
        sources, findings = self._collect_sources(
            research_prompt, extract_instruction,
            default_platform=hypothesis.primary_channel, recorder=recorder,
            hypothesis=hypothesis,
        )
        # Only verified competitor prices count. Competitor leads (no price /
        # "not captured") and general market-pricing articles do NOT satisfy
        # price-band mapping — but they stay in the ledger with honest wording.
        priced = [s for s in sources if classify_price_source(s) == "priced_competitor"]
        leads = 0
        for s in sources:
            kind = classify_price_source(s)
            if kind == "lead":
                leads += 1
                s.what_this_proves = "A relevant competitor/listing exists."
                s.what_this_does_not_prove = (
                    "Exact price was not captured, so this source does not satisfy "
                    "price-band mapping."
                )
            elif kind == "directional":
                s.what_this_proves = "Directional market pricing context exists."
                s.what_this_does_not_prove = (
                    "General market pricing only; does not establish a specific "
                    "competitor price band."
                )
        # Phase 3 PASSes only with 3+ verified prices AND a usable low/mid/premium map.
        bands = _price_bands(priced)
        has_map = sum(1 for tier in bands.values() if tier) >= 1
        passed = len(priced) >= self.min_sources and has_map
        band_summary = _summarise_price_bands(priced)
        result = self._result(
            PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            sources,
            f"{findings}\n\n{band_summary}" if band_summary else findings,
            (
                f"Mapped {len(priced)} verified competitor prices into low/mid/premium bands. "
                f"{band_summary}"
                if passed
                else (
                    f"Only {len(priced)} verified competitor prices found "
                    f"({leads} unpriced competitor leads excluded); "
                    f"need {self.min_sources} with a price map."
                )
            ),
            f"{self.min_sources}+ verified competitor prices with URLs and a low/mid/premium map",
        )
        result.details = {"price_bands": bands, "summary": band_summary, "lead_count": leads}
        return result


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
            "From the findings, list each competitor analysed. Fill the 10-field "
            "map (target_buyer, product_format/product_type, main_promise, "
            "features_included, what_it_structurally_does, what_it_does_not_govern) "
            "and the 6-dimension `teardown` (demand_validation, authorship_evidence, "
            "build_readiness_gate, listing_readiness_gate, fee_stress_logic, "
            "post_launch_decision_loop), each scored none/weak/present/strong/unknown. "
            "Put the structural read (what it does NOT govern) in gap_note. Use the "
            "real competitor URL in url. Distinguish products that STORE information "
            "from products that FORCE a decision."
        )
        sources, findings = self._collect_sources(
            research_prompt, extract_instruction,
            default_platform=hypothesis.primary_channel, recorder=recorder,
            hypothesis=hypothesis,
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
            "lower price, would this product still be structurally different? A gap "
            "that is only 'looks better / cleaner / cheaper / more pages / different "
            "buyer label / more features' is NOT structural.\n"
            'Return JSON: {"is_structural": true|false, '
            '"current_competitor_pattern": "...", "missing_mechanism": "...", '
            '"why_it_matters": "...", "proposed_mechanism": "...", '
            '"gap_statement": "...", "what_would_make_gap_weak": "...", '
            '"reason": "..."}'
        )
        if recorder is not None:
            recorder.log_phase_prompt(f"phase_{self.phase_number}", self.phase_name, instruction)
            # Phase 5 is synthesis (no web search): its "research" input is the
            # competitor structures carried in from Phase 4. Capture that input
            # as this phase's raw research record so the trail stays complete.
            recorder.write_raw_research(self.phase_number, competitor_summary)
        extraction = self.researcher.extract(
            findings=competitor_summary,
            instruction=instruction,
            system=EXTRACT_SYSTEM,
        )
        if recorder is not None:
            recorder.write_raw_extraction(self.phase_number, extraction.raw_text)
            if extraction.parse_error is not None:
                pe = extraction.parse_error
                recorder.log_extraction_error(
                    self.phase_number,
                    error_type=pe.get("error_type", "parse_error"),
                    error_message=pe.get("error_message", ""),
                    parser_step=pe.get("parser_step", ""),
                    raw_preview=extraction.raw_text,
                    line=pe.get("line"), column=pe.get("column"),
                    char_position=pe.get("char_position"), excerpt=pe.get("excerpt"),
                )
            elif extraction.salvage is not None:
                recorder.log_extraction_salvage(self.phase_number, extraction.salvage)
        data = extraction.data if isinstance(extraction.data, dict) else {}
        is_structural = _coerce_bool(data.get("is_structural"))
        gap_statement = _coerce_str(data.get("gap_statement")) or ""
        reason = _coerce_str(data.get("reason")) or ""

        # Competitor source IDs are linked later (orchestrator); here we record
        # how many competitor structures were available to compare against.
        competitor_count = len(phase4_result.sources_collected)
        aesthetic = _is_aesthetic_only(gap_statement, data)

        # A gap is supported only if it is structural, named, AND there are
        # competitor structures to compare against (never from absence alone).
        if aesthetic or not gap_statement:
            status = "unsupported"
        elif is_structural and competitor_count >= 1:
            status = "supported"
        elif gap_statement and competitor_count >= 1:
            status = "partially_supported"
        else:
            status = "unsupported"

        passed = status in ("supported", "partially_supported") and bool(is_structural) and not aesthetic
        findings = gap_statement or "Could not articulate a structural gap from the competitor analysis."

        mechanism_artifact = {
            "current_competitor_pattern": _coerce_str(data.get("current_competitor_pattern")) or "",
            "missing_mechanism": _coerce_str(data.get("missing_mechanism")) or "",
            "why_it_matters": _coerce_str(data.get("why_it_matters")) or "",
            "proposed_mechanism": (
                _coerce_str(data.get("proposed_mechanism"))
                or hypothesis.missing_mechanism_hypothesis
            ),
            "gap_statement": gap_statement,
            "supporting_source_ids": [],  # linked by the orchestrator
            "confidence": 0.7 if status == "supported" else (0.45 if status == "partially_supported" else 0.2),
            "what_would_make_gap_weak": (
                _coerce_str(data.get("what_would_make_gap_weak"))
                or "If a competitor could close it with better design, more pages, or lower price."
            ),
            "status": status,
            "is_structural": bool(is_structural) and not aesthetic,
        }

        result = self._result(
            PhaseStatus.PASS if passed else PhaseStatus.FAIL,
            phase4_result.sources_collected,
            findings,
            reason or (
                "Structural gap identified."
                if passed
                else ("Gap appears aesthetic/feature-only, not structural." if aesthetic
                      else "Gap appears non-structural or unsupported.")
            ),
            "Specific, structural missing mechanism identified (not feature/aesthetic-based)",
        )
        result.details = mechanism_artifact
        return result


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


def _coerce_str_list(value: Any) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        items = [v.strip() for v in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
    else:
        return None
    items = [v for v in items if v]
    return items or None


def _normalise_teardown(raw: Any) -> Optional[dict]:
    """Normalise a 6-dimension competitor teardown to allowed score values."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, str] = {}
    for dim in _TEARDOWN_DIMS:
        val = str(raw.get(dim, "unknown") or "unknown").strip().lower()
        out[dim] = val if val in _TEARDOWN_SCORES else "unknown"
    return out


def _extract_details(raw: dict) -> Optional[dict]:
    """Pull phase-specific structured extras off a raw extracted source."""
    keys = (
        "currency", "product_type", "what_it_promises", "target_buyer",
        "main_promise", "what_it_structurally_does", "what_it_does_not_govern",
    )
    details: dict[str, Any] = {}
    for k in keys:
        v = _coerce_str(raw.get(k))
        if v is not None:
            details[k] = v
    features = _coerce_str_list(raw.get("features_included"))
    if features:
        details["features_included"] = features
    teardown = _normalise_teardown(raw.get("teardown"))
    if teardown:
        details["teardown"] = teardown
    return details or None


def _summarise_price_bands(priced: list[SourceCard]) -> str:
    prices = sorted(p.price_observed for p in priced if p.price_observed is not None)
    if not prices:
        return ""
    low = ", ".join(f"${p:g}" for p in prices)
    return f"Observed prices: {low} (low ${prices[0]:g} / high ${prices[-1]:g})."


# Price tiers from the workflow doc: low $0–$12, mid $15–$24.99, premium $29+.
def _price_bands(priced: list[SourceCard]) -> dict:
    bands: dict[str, list[float]] = {"low": [], "mid": [], "premium": []}
    for s in priced:
        p = s.price_observed
        if p is None:
            continue
        if p <= 12:
            bands["low"].append(p)
        elif p < 29:
            bands["mid"].append(p)
        else:
            bands["premium"].append(p)
    return {tier: sorted(vals) for tier, vals in bands.items()}


# Markers that flag a "general market pricing" article (directional context only,
# never a verified competitor price). e.g. SendOwl-style pricing guides.
_DIRECTIONAL_PRICE_MARKERS = (
    "sendowl", "pricing guide", "price guide", "how much should", "how to price",
    "market range", "ultimate guide", "pricing strategy", "/blog", "blog post",
    "general pricing", "price your", "pricing tips", "guide to pricing",
)


def _price_not_captured(card: SourceCard) -> bool:
    """True if the source explicitly says the exact price was not captured."""
    text = (card.what_this_does_not_prove or "").lower()
    return "not captured" in text


def _is_directional_price(card: SourceCard) -> bool:
    """True if the source is a general market-pricing article, not a competitor."""
    hay = (
        f"{card.source_name} {card.url} {card.gap_note or ''} {card.what_this_proves or ''}"
    ).lower()
    return any(m in hay for m in _DIRECTIONAL_PRICE_MARKERS)


def classify_price_source(card: SourceCard) -> str:
    """Classify a Phase 3 source: priced_competitor | directional | lead.

    Only `priced_competitor` may satisfy price-band mapping or support the
    workable-price-band claim. A competitor lead (no observed price, or an
    explicit 'not captured') and a directional market article do not.
    """
    if card.price_observed is None or _price_not_captured(card):
        return "lead"
    if _is_directional_price(card):
        return "directional"
    return "priced_competitor"


_AESTHETIC_MARKERS = (
    "looks better", "look better", "cleaner", "nicer", "prettier", "cheaper",
    "more pages", "more templates", "more features", "different buyer", "better design",
    "better copy", "aesthetic", "easier on the eyes",
)


def _is_aesthetic_only(gap_statement: str, data: dict) -> bool:
    """True if the stated gap is only aesthetic/feature/label-based (not structural)."""
    text = " ".join(
        str(data.get(k, "")) for k in ("gap_statement", "missing_mechanism", "reason")
    ).lower()
    text = f"{text} {gap_statement.lower()}"
    if not text.strip():
        return False
    return any(marker in text for marker in _AESTHETIC_MARKERS)


def _merge_unique(primary: list[SourceCard], extra: list[SourceCard]) -> list[SourceCard]:
    seen = {str(s.url) for s in primary}
    merged = list(primary)
    for s in extra:
        if str(s.url) not in seen:
            seen.add(str(s.url))
            merged.append(s)
    return merged
