# Autonomous Demand Research System - Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLI Interface                           │
│  (demand-research --from-file | --interactive | list | view)    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Orchestrator Agent                           │
│  (Manages 5-phase workflow, validates pass/fail, routes)        │
└────────────┬────────────────────────────┬──────────────────────┘
             │                            │
    ┌────────▼────────┐         ┌─────────▼─────────┐
    │ Phase 1-5 Agents│         │ Decision Engine   │
    │ (Specialized)   │         │ (BUILD/REVISE/... │
    └────┬────┬────┬──┘         └────────┬──────────┘
         │    │    │                     │
         ▼    ▼    ▼                     ▼
    ┌──────────────────────┐    ┌──────────────────┐
    │ Research Layer       │    │ Evidence Quality │
    │ • Source Collector   │    │ • Scoring        │
    │ • Evidence Validator │    │ • Pass/Fail      │
    │ • Claude API calls   │    │ • Quality (0-1)  │
    └──────────────────────┘    └────────┬─────────┘
             │                            │
             ▼                            ▼
    ┌─────────────────────────────────────────────┐
    │            DemandBrief Output               │
    │  • Phase results (1-5) with sources        │
    │  • Decision + reasoning                    │
    │  • Evidence quality score                  │
    └──┬────────────────────────────────────┬────┘
       │                                    │
       ▼                                    ▼
    ┌──────────────────┐          ┌──────────────────┐
    │ Markdown Output  │          │  Notion Output   │
    │ Git-tracked      │          │  MCP-integrated  │
    │ Version control  │          │  Live iteration  │
    └──────────────────┘          └──────────────────┘
```

## Component Details

### 1. CLI Layer (`cli.py`)

**Responsibility:** User interface and command routing

**Commands:**
- `demand-research` — Interactive research workflow
- `demand-research --from-file YAML` — File-based input
- `demand-research list-briefs` — List completed research
- `demand-research view PATH` — Display a brief

**Flow:**
1. Collects ProductHypothesis (from prompts or file)
2. Calls orchestrator async
3. Generates outputs (markdown + Notion)
4. Saves JSON export
5. Displays summary

### 2. Data Models (`models.py`)

**ProductHypothesis** (input)
```python
{
  product_id: UUID,
  product_name: str,
  target_buyer: str,
  buyer_job: str,
  product_format: str,
  primary_channel: str,
  missing_mechanism_hypothesis: str,
  created_at: datetime
}
```

**SourceCard** (evidence)
```python
{
  source_number: int,
  source_name: str,
  url: HttpUrl,
  date_observed: datetime,
  platform: str,  # Etsy, Gumroad, Reddit, etc.
  price_observed: float (optional),
  buyer_language_captured: str (optional),
  what_this_proves: str,
  what_this_does_not_prove: str,
  is_direct_quote: bool (optional),
  screenshot_filename: str (optional)
}
```

**PhaseResult** (phase output)
```python
{
  phase_number: int (1-5),
  phase_name: str,
  status: PASS | FAIL,
  sources_collected: List[SourceCard],
  findings: str,
  reason: str,
  pass_condition: str
}
```

**DemandBrief** (final output)
```python
{
  product_hypothesis: ProductHypothesis,
  phase_1_result: PhaseResult,
  phase_2_result: PhaseResult,
  phase_3_result: PhaseResult,
  phase_4_result: PhaseResult,
  phase_5_result: PhaseResult,
  decision: BUILD | REVISE | PARK | KILL,
  decision_reasoning: str,
  evidence_quality_score: float (0-1),
  created_at: datetime,
  updated_at: datetime
}
```

### 3. Orchestrator Agent (`agents/orchestrator.py`)

**Responsibility:** Sequential phase management and workflow control

**Flow:**
```python
async def run_workflow(hypothesis):
    brief = DemandBrief(hypothesis, decision=PARK, score=0)
    
    # Phase 1: Signals
    phase1 = await phase1_agent.run(hypothesis)
    brief.phase_1_result = phase1
    if phase1.status == FAIL:
        decision = engine.decide([phase1], [])
        return brief_with_decision
    
    # Phase 2: Buyer Language
    phase2 = await phase2_agent.run(hypothesis, phase1)
    brief.phase_2_result = phase2
    if phase2.status == FAIL:
        decision = engine.decide([phase1, phase2], sources)
        return brief_with_decision
    
    # ... repeat phases 3, 4, 5
    
    # All passed: Final decision
    all_phases = [phase1, phase2, phase3, phase4, phase5]
    all_sources = brief.all_sources()
    decision, reasoning, quality = engine.decide(
        hypothesis, all_phases, all_sources
    )
    return brief_with_decision
```

**Key:** Early termination on phase failure routes directly to decision engine.

### 4. Phase Agents (`agents/phase_agents.py`)

**Base Structure:**
```python
class BasePhaseAgent:
    phase_number: int
    phase_name: str
    min_sources: int
    validator: EvidenceValidator
    collector: SourceCollector
```

**Each Phase (1-5):**
```python
async def run(self, hypothesis, prev_result=None) -> PhaseResult:
    # 1. Use collector to generate search prompt
    # 2. Call Claude API with Anthropic SDK
    # 3. Parse response into SourceCard objects
    # 4. Validate with EvidenceValidator
    # 5. Return PhaseResult with status/sources/findings
```

**Phase Specifics:**

| Phase | Input | Search | Output | Pass |
|-------|-------|--------|--------|------|
| 1 | Hypothesis | Etsy, Gumroad, Google Trends | Market signals | 3+ signals |
| 2 | Phase 1 | Reviews, Reddit, YouTube | Buyer quotes | 3+ quotes |
| 3 | Phase 1 | Competitor listings | Prices | 3+ prices |
| 4 | Phase 3 | Competitor sites | Structural analysis | 3+ teardowns |
| 5 | Phase 4 | All above | Gap statement | Gap identified |

### 5. Research Layer (`research/`)

#### SourceCollector
**Responsibility:** Orchestrate source gathering, generate prompts

```python
def detect_product_type(name, buyer, channel) -> str
    # etsy_template | github_tool | gumroad | multi_platform

def get_etsy_search_prompt(query) -> str
    # Template for Claude: "Search Etsy for [query], extract..."

def get_competitor_search_prompt(query) -> str
def get_buyer_language_prompt(category) -> str

async def create_source_card(...) -> SourceCard
    # Pydantic validation of source
```

#### EvidenceValidator
**Responsibility:** Ensure sources are real, not fabricated

```python
def validate_source(source: SourceCard) -> (bool, List[str])
    checks:
    - URL is real (not placeholder domain)
    - Date is recent (< 365 days old)
    - Price is realistic (0-10000)
    - Quote has no AI-speak patterns
    - Attribution present if marked "direct"

def validate_batch(sources) -> (valid, invalid)
```

**Placeholder domains caught:**
- example.com, test.com, fake.com, mock.com, placeholder.com

**AI-speak patterns caught:**
- "As an AI", "I am an AI", "generated by", etc.

### 6. Decision Engine (`decision_engine.py`)

**Responsibility:** Convert phase results + sources → decision

**Decision Rules:**

```python
def decide(hypothesis, phase_results, all_sources):
    # Early exit rules
    if phase1_failed:
        return (KILL, "No market signals", 0.1)
    
    if phase2_failed:
        return (PARK, "No buyer language", 0.25)
    
    if phase3_failed:
        return (REVISE, "No pricing data", 0.3)
    
    # All phases passed: Score quality
    quality = calculate_quality_score(
        sources_count,    # 3-15 sources
        source_recency,   # <90 days better
        quote_authenticity # direct > composite
    )
    
    if quality >= 0.75:
        return (BUILD, "Strong evidence", quality)
    elif quality >= 0.50:
        return (REVISE, "Moderate evidence", quality)
    else:
        return (PARK, "Weak evidence", quality)
```

**Quality Scoring:**
- Base (40%): Phases passed (each = 8%)
- Sources (30%): Count 3-15 (more = better)
- Recency (20%): <90 days old = full points
- Quotes (10%): Direct = full, composite = partial

### 7. Output Generators (`outputs/`)

#### MarkdownGenerator
**Responsibility:** Version-controlled brief in git

**Output:** `briefs/[product-id]-demand-brief.md`

**Structure:**
```markdown
# Demand Brief: [Product Name]

## Executive Summary
- Decision: [BUILD|REVISE|PARK|KILL]
- Evidence Quality: [0-100%]
- Reasoning: [...]

## Product Hypothesis
- Product Name
- Target Buyer
- Buyer Job
- Format
- Channel
- Gap Hypothesis

## Phase 1: Signal Discovery
- Status: PASS/FAIL
- Sources: [3+ cards]

## Phase 2: Buyer Language
...

## Phase 3: Price Band Mapping
...

## Phase 4: Competitor Presence
...

## Phase 5: Missing-Mechanism Gap
...

## All Sources
[Detailed source cards]
```

#### NotionGenerator
**Responsibility:** Live Notion document via MCP tools

**Output:** Notion page structure (MCP integration pending)

**Uses:** `mcp__d9133ba1-d9e0-4b8e-9e5f-2bc1ceff098d__notion-*` tools

```python
def generate(brief) -> page_structure
    # Builds nested Notion blocks
    # Creates properties: Decision, Quality, Product, Buyer
    # Links to source URLs
    # Uses MCP tools for page creation
```

### 8. Configuration (`config.py`)

**Settings:**
```python
# API
anthropic_api_key: str
anthropic_model: str (default: claude-opus-4-8)

# Notion
notion_api_key: str
notion_database_id: str

# Paths (auto-created)
briefs_dir: Path
data_dir: Path
screenshots_dir: Path
cache_dir: Path

# Research
max_sources_per_phase: int (10)
min_sources_required: int (3)
research_timeout_seconds: int (300)
enable_screenshots: bool

# Logging
log_level: str (INFO)
```

## Data Flow Example

**Input:**
```yaml
product_name: "Notion Etsy Seller Dashboard"
target_buyer: "Etsy sellers"
buyer_job: "Track product performance"
product_format: "Notion template"
primary_channel: "Notion Marketplace"
missing_mechanism_hypothesis: "Forces profitability review before expansion"
```

**Phase 1 Flow:**
1. CLI → Orchestrator
2. Orchestrator → Phase1Agent.run()
3. Phase1Agent asks collector for prompt
4. Collector generates: "Search Etsy for 'Etsy seller dashboard'"
5. Claude API called with prompt + tools
6. Claude returns JSON: `[{name: "...", url: "...", price: ...}, ...]`
7. SourceCard objects created + validated
8. Phase1Result: PASS (3 sources) or FAIL (< 3)
9. If FAIL: Orchestrator routes to decision engine
10. If PASS: Orchestrator calls Phase2Agent

**Output (if all pass):**
```json
{
  "decision": "BUILD",
  "evidence_quality_score": 0.82,
  "phase_1": {
    "status": "PASS",
    "sources_collected": [
      {
        "source_number": 1,
        "source_name": "Etsy Shop Manager Pro",
        "url": "https://www.etsy.com/...",
        "price": 19.99,
        ...
      },
      ...
    ]
  },
  ...
}
```

## Extension Points

### Add a New Phase
1. Create `PhaseXAgent` class in `phase_agents.py`
2. Implement `run()` method
3. Update orchestrator to call new phase
4. Add to decision engine logic

### Customize Sources
1. Edit `source_collector.get_custom_prompt()`
2. Add new search platforms
3. Update evidence validator for new source types

### Change Decision Rules
1. Edit `decision_engine.decide()`
2. Adjust pass conditions in phase agents
3. Modify quality scoring weights

### Wire New MCP Tools
1. Define tool schema in phase agent
2. Pass to Claude API call
3. Parse tool responses
4. Convert to SourceCard objects

## Testing Strategy

**Unit Tests:**
- Evidence validator (catches fake sources)
- Decision engine (validates logic)
- Model validation (Pydantic schemas)

**Integration Tests:**
- Full workflow with mock sources
- Phase pass/fail conditions
- Markdown + JSON generation

**End-to-End Tests:**
```bash
demand-research --from-file example_product.yaml
# Verify briefs/[id]-*.md created
# Verify data/briefs/[id]-brief.json created
# Verify decision is BUILD/REVISE/PARK/KILL
```

## Security & Validation

**Source Validation:**
- No fabricated URLs (EvidenceValidator)
- No AI-generated quotes (AI-speak filter)
- No unsourced claims (attribution check)
- No old data (date check)

**Quality Assurance:**
- Pass conditions are strict (3+ real sources)
- Evidence quality scored honestly
- All decisions justified by sources
- Composite language marked (not hidden)

## Performance Considerations

**Current Bottleneck:** Claude API call latency

**Phases (sequential):**
1. Phase 1: ~10-30 sec (search)
2. Phase 2: ~20-40 sec (deep forum search)
3. Phase 3: ~10-20 sec (competitor pricing)
4. Phase 4: ~30-60 sec (detailed teardowns)
5. Phase 5: ~5-10 sec (synthesis)

**Total:** ~75-160 seconds per research cycle

**Optimization opportunities:**
- Parallel phase execution (if dependencies allow)
- Caching repeat searches
- Batch API calls
- Async web requests

## Future Roadmap

**Phase 2:** Real Claude integration
- Implement Phase 1-5 agent runs
- Test with sample products
- Iterate on prompting

**Phase 3:** Notion MCP wiring
- Connect to Notion API
- Create live feedback loop
- Link briefs to hypothesis database

**Phase 4:** Automation & Webhooks
- Git push trigger
- Scheduled research runs
- Slack/email notifications

**Phase 5:** Analytics & Trends
- Track decision accuracy
- Compare briefs across time
- Market trend analysis

---

**Document Version:** 1.0  
**Last Updated:** 2026-06-07  
**Status:** Foundation Complete, Claude Integration Pending
