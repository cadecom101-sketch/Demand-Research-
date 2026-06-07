# Next Steps for Opus 4.8: Claude API Integration

## Status
✅ **Foundation: Complete and Audited**
- All core logic is working and tested
- All critical bugs fixed
- Ready for Claude API integration

⏳ **Remaining: Wire up Anthropic API calls (this is where you come in)**

---

## What Needs to Be Done

The system has 5 phase agents with TODO comments marking exactly where Claude API calls should go. Each one follows the same pattern:

### Pattern for Each Phase Agent

**File:** `src/demand_research/agents/phase_agents.py`

Each phase agent's `run()` method has a TODO block like this:
```python
# TODO: Integrate Anthropic API here
# from anthropic import Anthropic
# client = Anthropic()
# response = client.messages.create(
#     model="claude-opus-4-8",
#     max_tokens=4096,
#     messages=[{"role": "user", "content": ...}]
# )
```

**Your job:** Replace the TODO comments with actual Claude API calls.

---

## Specific Integration Tasks

### Phase 1: Signal Discovery (Line ~42-77 in phase_agents.py)
**Goal:** Find 3+ real market signals

**Input:** Product hypothesis
**Output:** SourceCard objects with marketplace listings

**How to integrate:**
```python
from anthropic import Anthropic

client = Anthropic()
prompt = self.collector.get_etsy_search_prompt(hypothesis.product_name)

response = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=4096,
    messages=[{"role": "user", "content": prompt}]
)

# Parse response → extract URLs, prices, platform info
# Create SourceCard for each signal
# Validate each with self.validator.validate_source()
sources = [source1, source2, source3...]
```

**Pass condition:** `len(sources) >= 3`

---

### Phase 2: Buyer Language Mining (Line ~90-117)
**Goal:** Find 3+ real buyer quotes

**Input:** Product hypothesis + Phase 1 results
**Output:** SourceCard objects with direct quotes

**How to integrate:**
```python
prompt = self.collector.get_buyer_language_prompt(hypothesis.product_name)

response = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=4096,
    messages=[{"role": "user", "content": prompt}]
)

# Parse response → extract quotes with attribution
# Mark each as direct_quote=True (if actual quote) or False (composite)
# Create SourceCard for each quote
sources = [source1_with_quote, source2_with_quote, ...]
```

**Pass condition:** `len(sources) >= 3`

---

### Phase 3: Price Band Mapping (Line ~128-156)
**Goal:** Find 3+ competitor prices

**Input:** Product hypothesis + Phase 2 results
**Output:** SourceCard objects with prices

**How to integrate:**
```python
prompt = self.collector.get_competitor_search_prompt(hypothesis.product_name)

response = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=4096,
    messages=[{"role": "user", "content": prompt}]
)

# Parse response → extract competitor URLs and prices
# Create SourceCard for each with price_observed field set
sources = [source1_with_price, source2_with_price, ...]
```

**Pass condition:** `len(sources) >= 3`

---

### Phase 4: Competitor Presence (Line ~166-194)
**Goal:** Analyze 3+ competitors structurally

**Input:** Product hypothesis + competitor URLs from Phase 3
**Output:** SourceCard objects with structural analysis

**How to integrate:**
```python
# Use competitors from Phase 3 (passed in as phase3_result)
sources = phase3_result.sources_collected

for competitor in sources:
    prompt = f"Analyze competitor at {competitor.url}..."
    
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    
    # Parse response → extract 6-dimension scoring:
    # 1. Demand validation
    # 2. Authorship evidence
    # 3. Build-readiness gate
    # 4. Listing-readiness gate
    # 5. Fee-stress logic
    # 6. Post-launch decision loop
    # Store in gap_note or competitor_features_observed

# Return same sources with enriched analysis
```

**Pass condition:** `len(sources) >= 3`

---

### Phase 5: Missing-Mechanism Gap (Line ~205-237)
**Goal:** Identify the structural gap

**Input:** Product hypothesis + competitor analysis from Phase 4
**Output:** Gap statement synthesized

**How to integrate:**
```python
prompt = f"""
Gap analysis: Compare your mechanism '{hypothesis.missing_mechanism_hypothesis}' 
against these competitors: {[c.source_name for c in phase4_result.sources_collected]}.

What structural gap exists? (Not features, but mechanisms.)
"""

response = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=4096,
    messages=[{"role": "user", "content": prompt}]
)

# Parse response → extract gap analysis
# Store in phase_5_result.findings
# Use competitor sources as reference (they're already in phase4_result)
```

**Pass condition:** Structural gap identified (not feature-based)

---

## Helper Methods Already Available

### In `SourceCollector` (research/source_collector.py):
```python
collector.detect_product_type(name, buyer, channel)
collector.get_etsy_search_prompt(query)
collector.get_competitor_search_prompt(query)
collector.get_buyer_language_prompt(category)
await collector.create_source_card(...)  # Creates validated SourceCard
```

### In `EvidenceValidator` (research/evidence_validator.py):
```python
validator.validate_source(source)  # Returns (is_valid, list[issues])
validator.validate_batch(sources)  # Returns (valid, invalid)
```

---

## Example Full Implementation (Phase 1)

Here's what the complete Phase 1 implementation should look like:

```python
async def run(self, hypothesis: ProductHypothesis) -> PhaseResult:
    """Phase 1: Signal Discovery"""
    logger.info(f"Phase 1: Searching for market signals - {hypothesis.product_name}")

    sources = []
    product_type = self.collector.detect_product_type(
        hypothesis.product_name,
        hypothesis.target_buyer,
        hypothesis.primary_channel,
    )

    # Call Claude to search for market signals
    from anthropic import Anthropic
    client = Anthropic()
    
    prompt = self.collector.get_etsy_search_prompt(hypothesis.product_name)
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=4096,
        system="You are a market research analyst. Find real product listings.",
        messages=[{"role": "user", "content": prompt}]
    )

    # Parse response text to extract structured data
    # Example: response.content[0].text contains JSON or structured text
    # Parse and create SourceCard objects
    
    for signal in parsed_signals:
        source = await self.collector.create_source_card(
            source_number=len(sources) + 1,
            source_name=signal["name"],
            url=signal["url"],
            platform="Etsy",
            price=signal.get("price"),
            what_it_proves="Real product listing in target category",
            what_it_does_not_prove="Whether this product is profitable or successful",
        )
        
        # Validate source
        is_valid, issues = self.validator.validate_source(source)
        if is_valid:
            sources.append(source)
        else:
            logger.warning(f"Source validation failed: {issues}")

    findings = f"Found {len(sources)} real market signals for {hypothesis.product_name}"
    status = PhaseStatus.PASS if len(sources) >= self.min_sources else PhaseStatus.FAIL

    return PhaseResult(
        phase_number=self.phase_number,
        phase_name=self.phase_name,
        status=status,
        sources_collected=sources,
        findings=findings,
        reason=f"Found {len(sources)} sources; need {self.min_sources}" if status == PhaseStatus.FAIL else "Sufficient signals found",
        pass_condition=f"{self.min_sources}+ real market signals with URLs and dates",
    )
```

---

## Testing Your Integration

After implementing each phase, test immediately:

```bash
# Install latest anthropic
pip install anthropic>=0.25.0

# Export your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Test the workflow
demand-research --from-file example_product.yaml

# Check output
ls -la briefs/
cat briefs/*-demand-brief.md
```

---

## Key Points for Integration

1. **Streaming not needed** - Use regular `client.messages.create()`, not streaming
2. **Token limits:** 4096 max_tokens per call is safe
3. **System prompt:** Optional but helpful for context
4. **Parsing:** Claude's response is in `response.content[0].text` - you'll need to parse it
5. **Validation:** Every source must pass `validator.validate_source()` before use
6. **Error handling:** Wrap API calls in try-except; log failures but don't crash

---

## Success Criteria

When you're done:
1. ✅ Run `demand-research --from-file example_product.yaml`
2. ✅ System completes all 5 phases
3. ✅ Returns BUILD/REVISE/PARK/KILL decision
4. ✅ Markdown brief generated in `briefs/`
5. ✅ All sources are real URLs with dates
6. ✅ No placeholder domains or AI-generated quotes

---

## File Locations to Edit

- `src/demand_research/agents/phase_agents.py` - All 5 phase agents (lines 42, 90, 128, 166, 205)

That's the only file you need to modify. Everything else is infrastructure.

---

Good luck! The foundation is solid. Just plug in the Claude API calls. 🚀
