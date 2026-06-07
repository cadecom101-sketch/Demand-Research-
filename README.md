# Autonomous Demand Research Workflow

An autonomous system that validates product ideas through structured evidence-driven research before you invest build time. Uses Claude Agent SDK to autonomously execute a proven 5-phase demand validation methodology.

## What It Does

Instead of building from excitement, you get **real buyer signals first**:

- **Phase 1: Signal Discovery** - Are people actually searching for this? Are products already being sold?
- **Phase 2: Buyer Language** - What do real buyers complain about? What words do they use?
- **Phase 3: Price Band Mapping** - What price range do similar products command?
- **Phase 4: Competitor Presence** - What do existing products structurally do? What's missing?
- **Phase 5: Missing-Mechanism Gap** - What does your product force buyers to do that competitors don't?

At the end: **BUILD** (strong evidence), **REVISE** (fix scope/gap), **PARK** (not enough evidence yet), or **KILL** (no real demand).

## Quick Start

### Installation

```bash
pip install -e .
# or
pip install -r requirements.txt
```

### Run a Research Cycle

**Interactive mode (prompts you for details):**
```bash
demand-research
```

**From a YAML file:**
```bash
demand-research --from-file example_product.yaml
```

**View completed briefs:**
```bash
demand-research list-briefs
demand-research view briefs/your-brief.md
```

### Example Product Hypothesis

Create `my_product.yaml`:
```yaml
product_name: "Notion Etsy Seller Dashboard"
target_buyer: "Solo digital product sellers on Etsy"
buyer_job: "Decide which products to expand, revise, or park based on profitability"
product_format: "Notion template"
primary_channel: "Notion Marketplace"
missing_mechanism_hypothesis: "Most seller tools track after launch; this forces profitability review before expansion"
```

Then run:
```bash
demand-research --from-file my_product.yaml
```

## How It Works

### The Research Phases

#### Phase 1: Signal Discovery (Pass: 3+ real sources)
Agent searches for real market signals:
- Product category exists on Etsy, Gumroad, Notion Marketplace
- Real people are selling similar products
- Google Trends shows search activity
- All sources must have real URLs and dates

#### Phase 2: Buyer Language Mining (Pass: 3+ buyer quotes)
Agent mines real buyer pain points:
- Etsy review comments
- Reddit threads in r/entrepreneur, r/smallbusiness, etc.
- Notion Marketplace reviews
- YouTube comments
- Forum discussions
- **Must be direct quotes with attribution** (or marked "composite" if synthesized)

#### Phase 3: Price Band Mapping (Pass: 3+ competitor prices)
Agent finds what similar products cost:
- Identifies 3+ real competitors
- Records their prices: $6, $15, $24.99, $49
- Maps into low/mid/premium bands
- All prices must have screenshots/URLs

#### Phase 4: Competitor Presence (Pass: 3+ analyzed competitors)
Agent analyzes what competitors **structurally do**:
- Creates 10-field competitor map (name, URL, price, promise, features, structure, gaps)
- Scores on 6 dimensions:
  1. Demand validation (do they require proof of buyer need?)
  2. Authorship evidence (do they prove the seller created it?)
  3. Build-readiness gate (do they gate before building?)
  4. Listing-readiness gate (do they gate before listing?)
  5. Fee-stress logic (do they force fee math?)
  6. Post-launch decision loop (do they force review after launch?)

#### Phase 5: Missing-Mechanism Gap (Pass: structural gap identified)
Agent identifies the specific structural opportunity:
- Compares your hypothesis mechanism vs. competitor mechanisms
- Validates the gap is **structural**, not aesthetic
- Example gap: "Competitors track after decision; we force decision **before** motion"

### Evidence Quality Scoring

Score (0-100%) factors in:
- **Source count** (more sources = higher quality)
- **Source recency** (sources <90 days old count more)
- **Quote authenticity** (direct > composite)
- **Pass condition met** (phase status)

**BUILD** requires quality ≥75% + all phases pass.

### Decision Rules

| Decision | Meaning | Next Step |
|----------|---------|-----------|
| **BUILD** | Strong evidence across all 5 phases. Market is real, buyers hurt, you have a gap. | Start building. |
| **REVISE** | Evidence exists but moderate quality. Narrow the buyer, clarify the gap, or validate pricing more. | Adjust hypothesis and rerun research. |
| **PARK** | Market may exist but insufficient evidence now. Revisit in 3-6 months. | Keep the brief. Run again later. |
| **KILL** | No market signals, no buyer pain, just a re-skin. Don't build. | Archive the idea. |

## Output Files

### Generated Briefs

**Markdown (version-controlled):**
```
briefs/
└── abc123-governed-solo-operator-launch-os-demand-brief.md
```
Contains all phases, sources, and decision reasoning. Commit to git for version history.

**JSON (for processing):**
```
data/briefs/
└── abc123-brief.json
```
Machine-readable format for automation or importing elsewhere.

**Notion (live iteration):**
📊 Page created in your Notion workspace with:
- Product hypothesis
- All phase findings
- Source cards with links
- Decision and reasoning
- Evidence quality score

### Evidence Storage

```
data/
├── screenshots/      (source proof images)
├── cache/           (temp research data)
└── briefs/          (JSON exports)
```

## Configuration

Create `.env` file in project root:

```bash
# Claude API
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-4-8

# Notion (optional, for MCP integration)
NOTION_API_KEY=secret_...
NOTION_DATABASE_ID=abc123...

# Behavior
LOG_LEVEL=INFO
ENABLE_SCREENSHOTS=true
```

## Architecture

### Key Components

```
demand_research/
├── models.py          # Pydantic schemas
├── config.py          # Settings + environment
├── decision_engine.py # BUILD/REVISE/PARK/KILL logic
├── cli.py             # CLI entry point
├── agents/
│   ├── orchestrator.py    # Workflow management
│   └── phase_agents.py    # Phase 1-5 agents
├── research/
│   ├── source_collector.py      # Source gathering
│   └── evidence_validator.py    # Real-source enforcement
└── outputs/
    ├── markdown_generator.py   # Git-tracked briefs
    └── notion_generator.py     # Notion integration
```

### Agent SDK Integration

Each phase agent uses Claude with tool calling:

```python
client = Anthropic()

response = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=4096,
    tools=[
        # Tool definitions for web research
    ],
    messages=[
        {
            "role": "user",
            "content": "Find market signals for [product]..."
        }
    ]
)
```

The orchestrator chains phases, validates pass/fail, and routes to decision engine.

## Methodology Notes

### What Counts as Evidence

**Strong evidence:**
- Real product listing on Etsy with price and reviews
- Direct quote from a review: "I wish this had [feature]"
- Google Trends chart showing search volume
- Dated screenshot of competitor marketplace page

**Weak evidence:**
- "I think people want this"
- "AI says this niche is trending"
- "One competitor exists" (without details)
- Keyword volume with no source/date

### What Counts as Buyer Language

**Direct quote:**
```
"I need a way to decide which products deserve build time."
— Sarah M., Etsy seller review, June 2024
[URL to review]
```

**Composite observation (must be labeled):**
```
COMPOSITE BUYER LANGUAGE — NOT A DIRECT QUOTE
Sellers repeatedly mention struggling to track profitability
across multiple product listings and revisions.
[Sources: 3 reviews + 2 Reddit threads]
```

### The Missing-Mechanism Test

**Ask: If a competitor added better design, more pages, or cheaper pricing, would your system still be structurally different?**

- **Yes** → Real gap (behavioral/mechanical difference)
- **No** → Weak gap (just aesthetic/feature difference)

Example real gaps:
- "Ours forces demand proof **before** you design; theirs only tracks after."
- "Ours gates each product through a profitability review; theirs is just a dashboard."
- "Ours makes you choose EXPAND/REVISE/PARK for each listing; theirs just shows data."

## Extending This Workflow

### Add New Research Sources

Edit `src/demand_research/research/source_collector.py`:

```python
def get_custom_source_prompt(self, query: str) -> str:
    """Custom search for your data source."""
    return f"""Search [custom platform] for: {query}
    
    Collect:
    - Product title
    - Price
    - Description
    - Review/feedback
    - URL
    """
```

### Customize Pass Conditions

Edit `src/demand_research/agents/phase_agents.py`:

```python
class Phase1Agent(BasePhaseAgent):
    def __init__(self):
        super().__init__(
            phase_number=1,
            min_sources=5  # Require 5 instead of 3
        )
```

### Adapt for Different Product Types

The system auto-detects product type:
- **etsy_template** → Etsy/Gumroad/Notion focus
- **github_tool** → GitHub/Stack Overflow/Reddit focus
- **multi_platform** → All sources

Edit detection in `source_collector.detect_product_type()`.

### Add Custom Notion Structure

Edit `src/demand_research/outputs/notion_generator.py`:

```python
def generate(self, brief: DemandBrief) -> dict:
    # Build custom page structure
    # Use mcp__d9133ba1-d9e0-4b8e-9e5f-2bc1ceff098d__notion-* tools
```

## Troubleshooting

### "No sources found" in Phase X

The agent couldn't locate evidence. This is **valid research feedback** — it means the market signal isn't visible online, not that your idea is bad.

Options:
1. **Revise** your product definition (too broad? too niche?)
2. **Park** and try again in 6 months
3. **Kill** if you believe this should have obvious signals

### "COMPOSITE BUYER LANGUAGE" warnings

The agent couldn't find exact buyer quotes. This is honest — it means real feedback isn't visible in reviews, forums, or comments.

Options:
1. Search harder (different Reddit communities, Slack groups, Discord)
2. Mark as honest composite observation (not a real quote)
3. Proceed with weaker evidence

### Notion integration not creating pages

Notion MCP tools need to be wired. The structure is ready; await Notion API key + database ID in `.env`.

## Research Methodology Source

This workflow is based on E0→E1 validation methodology:

- **E0** = Idea (vibe-based)
- **E1** = Validated demand (evidence-based)

The goal: **Collect enough real-world evidence to answer:**
- Is there a real buyer job?
- Are people already looking for solutions?
- Are comparable products present?
- What do buyers complain about?
- What price range exists?
- What mechanism is missing?

## Tips for Success

1. **Be honest about failures.** If Phase 1 returns no market signals, that's real data. Don't ignore it.

2. **Use real sources only.** Never fabricate quotes or demand signals. The evidence validator catches this.

3. **Narrow your buyer.** "Everyone" doesn't work. "Solo Etsy sellers launching digital products" works.

4. **Name the mechanism, not just features.** "Gates launch readiness" is structural. "Has more pages" is not.

5. **Revisit every 3-6 months.** Markets change. A PARK idea might become viable later.

6. **Let the data speak.** If evidence points to PARK or REVISE, trust it. Your time is better spent elsewhere.

## Contributing

To improve the research methodology or add features:

1. Fork the repo
2. Create a branch: `git checkout -b feature/your-feature`
3. Edit and test (see `test_workflow.py` for examples)
4. Commit: `git commit -am "Add feature"`
5. Push and create PR

## License

MIT

## Questions?

See `IMPLEMENTATION.md` for technical details or run:
```bash
demand-research --help
```
