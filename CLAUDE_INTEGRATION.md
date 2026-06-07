# Claude Agent SDK Integration Guide

This document explains how to integrate Claude API and MCP tools to make the research phases autonomous.

## Current State

The foundation is complete with:
- ✓ 5-phase structure (orchestrator + agents)
- ✓ Decision engine with pass/fail logic
- ✓ Evidence validation (ensures real sources)
- ✓ Output generation (markdown + Notion structure)
- ✓ CLI interface

**Missing:** Actual Claude API calls to research the web.

## What Needs to Happen

Each phase agent currently returns placeholder data. We need to fill in the `run()` methods with actual Claude API calls.

### Example: Phase 1 Agent Integration

**Current (stub):**
```python
# src/demand_research/agents/phase_agents.py: Phase1Agent.run()

async def run(self, hypothesis: ProductHypothesis) -> PhaseResult:
    """Phase 1 stub - returns placeholder."""
    sources = []
    findings = "Phase 1 structure ready; agent integration pending"
    return PhaseResult(...)
```

**After Integration:**
```python
from anthropic import Anthropic

async def run(self, hypothesis: ProductHypothesis) -> PhaseResult:
    """Phase 1: Search for real market signals."""
    
    client = Anthropic()
    sources = []
    
    # Ask Claude to search for market signals
    prompt = self.collector.get_etsy_search_prompt(
        hypothesis.product_name
    )
    
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=4096,
        system="""You are a demand research agent. Search for real market signals.
        
Requirements:
- Find real product listings on Etsy, Gumroad, Notion Marketplace
- Extract: product URL, price, shop name, review count, description
- Verify each URL is real and accessible
- Mark date as today
- Only report what you actually find, never fabricate
- If search finds nothing, say "No signals found" (valid research result)""",
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    
    # Parse response and create SourceCard objects
    for source_data in response.content[0].text.split("\n"):
        if source_data.strip():
            source = self.collector.create_source_card(
                source_number=len(sources) + 1,
                source_name=source_data["name"],
                url=source_data["url"],
                platform="Etsy",
                price=source_data.get("price"),
                what_it_proves="Real Etsy listing for similar product exists",
                what_it_does_not_prove="Whether this product is profitable or successful"
            )
            sources.append(source)
    
    # Validate sources
    validator = EvidenceValidator()
    valid, invalid = validator.validate_batch(sources)
    
    status = PhaseStatus.PASS if len(valid) >= self.min_sources else PhaseStatus.FAIL
    
    return PhaseResult(
        phase_number=1,
        phase_name="Signal Discovery",
        status=status,
        sources_collected=valid,
        findings=f"Found {len(valid)} valid market signals across Etsy/Gumroad/Notion",
        reason="Real product listings prove category exists" if status == PhaseStatus.PASS else "No market signals found",
        pass_condition="3+ real sources with URLs and dates"
    )
```

## Integration Steps

### 1. Install Anthropic SDK
```bash
pip install anthropic>=0.25.0
```

### 2. Update Phase 1 Agent
File: `src/demand_research/agents/phase_agents.py`

Replace `Phase1Agent.run()` stub with actual Claude call (see example above).

Key points:
- Import `Anthropic` client
- Use `get_etsy_search_prompt()` from source collector
- Parse Claude's response into SourceCard objects
- Validate with EvidenceValidator
- Return PhaseResult with real findings

### 3. Update Phase 2 Agent (Buyer Language Mining)

```python
prompt = self.collector.get_buyer_language_prompt(
    hypothesis.product_name
)

# Claude searches Reddit, Etsy reviews, YouTube comments
# Extracts exact quotes with attribution
# Marks direct vs. composite
```

### 4. Update Phase 3 Agent (Price Band Mapping)

```python
prompt = self.collector.get_competitor_search_prompt(
    hypothesis.product_name
)

# Claude searches Etsy, Gumroad, Notion Marketplace
# Extracts: name, URL, price, features, description
# Creates SourceCard with price_observed
```

### 5. Update Phase 4 Agent (Competitor Presence)

```python
# For each competitor from Phase 3:
# - Extract features and promises
# - Score on 6 dimensions:
#   1. Demand validation
#   2. Authorship evidence
#   3. Build-readiness gate
#   4. Listing-readiness gate
#   5. Fee-stress logic
#   6. Post-launch decision loop

# Create 10-field competitor map in SourceCard
```

### 6. Update Phase 5 Agent (Missing Mechanism Gap)

```python
# Synthesize Phase 4 findings
# Compare hypothesis mechanism vs. competitor mechanisms
# Identify structural difference (not feature-based)

# Use gap statement template:
# "Existing products help [buyer] [outcome],
#  but they mostly operate as [category].
#  They do not force [missing mechanism].
#  This creates [failure mode].
#  My product fills the gap by [specific governance],
#  so buyer can [specific decision outcome]."
```

### 7. Integrate with Notion MCP

File: `src/demand_research/outputs/notion_generator.py`

```python
from anthropic import Anthropic

async def generate(self, brief: DemandBrief):
    """Create Notion page using MCP tools."""
    
    client = Anthropic()
    
    # Use notion-create-pages tool
    response = client.messages.create(
        model="claude-opus-4-8",
        tools=[
            {
                "name": "notion_create_page",
                "description": "Create a Notion page",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "database_id": {"type": "string"},
                        "properties": {"type": "object"}
                    }
                }
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"Create Notion page for demand brief: {brief.product_hypothesis.product_name}"
            }
        ]
    )
    
    # Handle tool call response
    # Return page_id and URL
```

## Testing Your Integration

### Quick Test
```python
# test_phase1.py
from demand_research.models import ProductHypothesis
from demand_research.agents.phase_agents import Phase1Agent
import asyncio

async def test():
    hypothesis = ProductHypothesis(
        product_name="Notion Etsy Seller Dashboard",
        target_buyer="Etsy sellers",
        buyer_job="Track product performance",
        product_format="Notion template",
        primary_channel="Etsy",
        missing_mechanism_hypothesis="Forces demand check before launch"
    )
    
    agent = Phase1Agent()
    result = await agent.run(hypothesis)
    
    print(f"Status: {result.status}")
    print(f"Sources: {len(result.sources_collected)}")
    for source in result.sources_collected:
        print(f"  - {source.source_name}: {source.url}")

asyncio.run(test())
```

### Full Workflow Test
```bash
demand-research --from-file example_product.yaml
```

Check output:
```bash
ls briefs/
cat briefs/*-demand-brief.md
```

## Environment Setup

### For Anthropic API
```bash
export ANTHROPIC_API_KEY=sk-ant-...
export ANTHROPIC_MODEL=claude-opus-4-8
```

### For Notion (optional)
```bash
export NOTION_API_KEY=secret_...
export NOTION_DATABASE_ID=abc123...
```

## Key Design Principles

1. **Real sources only:** Claude must search real URLs, not fabricate
2. **Date enforcement:** Every source needs `date_observed`
3. **Direct quotes:** Mark as direct or composite (never lie about sourcing)
4. **Pass conditions are strict:** 3+ sources with validation
5. **Quality matters:** Evidence quality score reflects source recency and authenticity

## Claude Prompting Strategy

Each phase uses targeted prompts:

**Phase 1 (Signals):**
> "Search for real product listings similar to [name]. Find 3+ actual listings on Etsy, Gumroad, or Notion Marketplace. Report exact URLs, prices, and product descriptions."

**Phase 2 (Buyer Language):**
> "Find real buyer quotes about [pain point]. Search Etsy reviews, Reddit, YouTube comments. Extract exact quotes with source URLs and dates. Mark direct quotes vs. composite observations."

**Phase 3 (Pricing):**
> "Find 3+ competitor products and their prices. List: product name, URL, platform, price, features. Must have real URLs with screenshots/proof."

**Phase 4 (Competitor Analysis):**
> "Analyze each competitor product. For each, create 10-field map: name, URL, price, target buyer, format, promise, features, structure, gaps, decision logic. Score 0-3 on 6 dimensions."

**Phase 5 (Gap):**
> "Compare your mechanism vs. competitor mechanisms. What do competitors NOT force? Must be structural (behavioral), not aesthetic. Write gap statement."

## Debugging

If Claude returns no results:

1. **Check the prompt:** Is it clear? Does it ask for real sources?
2. **Check the URL:** Is the search term specific enough?
3. **Check date:** Are sources recent enough?
4. **Validate:** Does EvidenceValidator reject the source?

If EvidenceValidator flags a source:

1. Check URL validity
2. Check date is within 365 days
3. Check price is realistic (0-10000)
4. Check quote has attribution

## Next Actions

1. Start with Phase 1 integration (simplest, market signals)
2. Test with example product
3. Move to Phase 2 (buyer language)
4. Continue through Phase 5
5. Wire Notion integration last

Good luck! The foundation is solid — it just needs real research calls now.
