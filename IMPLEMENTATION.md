# Autonomous Demand Research Workflow - Implementation Status

## Overview
This project is an autonomous demand research system using Claude Agent SDK. It validates product ideas through 5 evidence-driven phases before build decisions.

## Architecture

### Core Components

1. **Models** (`src/demand_research/models.py`)
   - `ProductHypothesis` - Input product idea
   - `SourceCard` - Single research source with evidence
   - `PhaseResult` - Result from each phase
   - `DemandBrief` - Complete research output

2. **Decision Engine** (`src/demand_research/decision_engine.py`)
   - Applies pass/fail logic for each phase
   - Calculates evidence quality score
   - Determines BUILD/REVISE/PARK/KILL decision

3. **Research Layer** (`src/demand_research/research/`)
   - `source_collector.py` - Orchestrates source collection
   - `evidence_validator.py` - Validates sources are real (not fabricated)

4. **Agents** (`src/demand_research/agents/`)
   - `orchestrator.py` - Manages workflow phases
   - `phase_agents.py` - Individual phase implementations
     - Phase 1: Signal Discovery
     - Phase 2: Buyer Language Mining
     - Phase 3: Price Band Mapping
     - Phase 4: Competitor Presence
     - Phase 5: Missing-Mechanism Gap

5. **Output Generators** (`src/demand_research/outputs/`)
   - `markdown_generator.py` - Git-tracked markdown briefs
   - `notion_generator.py` - Notion document structure (MCP ready)

6. **CLI** (`src/demand_research/cli.py`)
   - `demand-research` command to start research
   - `--from-file` to load hypothesis from YAML/JSON
   - `list-briefs` to view completed research
   - `view` to read a brief

## Current Status: Foundation Ready

### ✓ Implemented
- Project structure (pyproject.toml, package layout)
- Data models with Pydantic validation
- Configuration system (environment-based)
- Decision engine with evidence quality scoring
- Evidence validator (ensures sources are real)
- Source collector framework (ready for web integration)
- Agent base classes with phase structure
- Orchestrator workflow engine
- Markdown output generator
- Notion output generator (structure, MCP integration pending)
- CLI with commands
- Example product hypothesis file

### ⏳ Next Phase: Claude Agent SDK Integration

The foundation is complete. The next phase integrates actual research:

1. **Wire Claude Agent SDK Tool Calling**
   - Implement tool definitions for research execution
   - Hook up WebFetch and WebSearch MCP tools
   - Call Claude API from phase agents with web research instructions

2. **Implement Phase Agents with Real Research**
   - Phase 1: Search for market signals (Etsy, Gumroad, Google Trends)
   - Phase 2: Mine buyer language (Reddit, reviews, forums)
   - Phase 3: Map competitor pricing
   - Phase 4: Analyze competitor structures
   - Phase 5: Identify missing mechanisms

3. **Complete Notion Integration**
   - Use `mcp__d9133ba1-d9e0-4b8e-9e5f-2bc1ceff098d__notion-*` tools
   - Create Notion pages with brief structure
   - Link from Notion to markdown source

4. **Add Screenshot Capture** (Optional)
   - Evidence proof for sources
   - Screenshot URLs or saved images

## Quick Start

### Installation
```bash
pip install -r requirements.txt
# or
pip install -e .
```

### Run Research
```bash
# Interactive prompts
demand-research

# From YAML file
demand-research --from-file example_product.yaml

# List completed briefs
demand-research list-briefs

# View a brief
demand-research view briefs/abcd-governed-solo-operator-launch-os-demand-brief.md
```

### Generated Outputs
- **Markdown:** `briefs/[product-id]-demand-brief.md` (version-controlled)
- **JSON:** `data/briefs/[product-id]-brief.json` (for processing)
- **Notion:** Document created via MCP tools (when implemented)

## Configuration

Set environment variables:
```bash
ANTHROPIC_API_KEY=sk-...
ANTHROPIC_MODEL=claude-opus-4-8
NOTION_API_KEY=secret_...
NOTION_DATABASE_ID=...
LOG_LEVEL=INFO
ENABLE_SCREENSHOTS=false
```

Or create `.env` file:
```
ANTHROPIC_API_KEY=sk-...
```

## Research Methodology

Each phase validates one aspect:

1. **Phase 1 (Signal Discovery):** Does the product category exist in real markets?
   - Pass: 3+ real sources with URLs, dates, observable signals
   - Searches: Etsy, Gumroad, Notion Marketplace, Google Trends

2. **Phase 2 (Buyer Language):** What do real buyers say they struggle with?
   - Pass: 3+ real buyer quotes from reviews, forums, Reddit
   - Mark as direct or composite (never fabricate)

3. **Phase 3 (Price Band):** What prices do similar products command?
   - Pass: 3+ competitor prices with URLs and screenshots
   - Maps: Low/Mid/Premium bands

4. **Phase 4 (Competitor Analysis):** What do existing products structurally do?
   - Pass: 3+ competitors with 10-field maps + 6-dimension teardowns
   - Scores: demand validation, authorship gates, build gates, listing gates, fee stress, post-launch loops

5. **Phase 5 (Missing Mechanism):** What structural thing do competitors NOT force?
   - Pass: Specific structural gap named (not feature-based)
   - Validates: Gap is behavioral/mechanical, not aesthetic

## Decision Logic

**BUILD:** All 5 phases pass + strong evidence (quality ≥ 0.75)
- Real market signals, buyer pain visible, viable pricing, competitors analyzed, gap is structural

**REVISE:** All phases pass but evidence is moderate (0.50-0.75)
- Market exists but buyer too broad, gap weak, price unclear, or competitors mostly track/organize

**PARK:** Weak evidence in all phases (< 0.50) OR Phase 2-5 fails
- Evidence exists but insufficient; revisit later

**KILL:** Phase 1 fails + all others fail
- No market signal, no buyer pain, just a re-skin

## File Structure
```
demand-research/
├── src/demand_research/
│   ├── __init__.py
│   ├── cli.py (entry point)
│   ├── models.py (Pydantic schemas)
│   ├── config.py (settings)
│   ├── decision_engine.py (BUILD/REVISE/PARK/KILL)
│   ├── agents/
│   │   ├── orchestrator.py (workflow engine)
│   │   └── phase_agents.py (Phase 1-5 agents)
│   ├── research/
│   │   ├── source_collector.py (web scraping orchestration)
│   │   └── evidence_validator.py (real-source enforcement)
│   └── outputs/
│       ├── markdown_generator.py (git-tracked briefs)
│       └── notion_generator.py (MCP-integrated Notion docs)
├── briefs/ (generated markdown demand briefs)
├── data/
│   ├── screenshots/ (evidence images)
│   ├── cache/ (temp research data)
│   └── briefs/ (JSON export)
├── pyproject.toml
├── requirements.txt
├── example_product.yaml
├── IMPLEMENTATION.md (this file)
└── README.md (user guide)
```

## Next: Claude Integration

Once the foundation is solid, integrate Claude Agent SDK:

```python
# In phase_agents.py: Phase1Agent.run()
client = Anthropic()

response = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=4096,
    tools=[
        {
            "name": "web_search",
            "description": "Search the web for market signals",
            "input_schema": {...}
        }
    ],
    messages=[
        {
            "role": "user",
            "content": collector.get_etsy_search_prompt(hypothesis.product_name)
        }
    ]
)

# Parse response, extract sources, validate
```

## Testing

Run a basic workflow test:
```bash
demand-research --from-file example_product.yaml
```

Check outputs:
```bash
ls briefs/
cat briefs/*-demand-brief.md
```

## Notes

- All sources **must be real URLs** with dates
- **No fabricated quotes** - only direct sourcing or marked composite
- **No AI-generated demand signals** - only real market data
- Evidence validator catches placeholder domains, AI-speak, missing attribution
- Quality score factors in source count, recency, and quote authenticity
