# Autonomous Demand Brief Research Workflow

Validate a product idea with **real web evidence** before you spend build time.
You provide a one-line product hypothesis; the workflow autonomously runs five
evidence-gated research phases and returns a **BUILD / REVISE / PARK / KILL**
decision backed by dated, sourced citations.

The "eyes and hands" are the **Anthropic API's native web search tool** — Claude
searches the real web (Etsy, Gumroad, Notion Marketplace, Reddit, forums),
extracts structured source cards, and every card is validated before it counts.
No fabricated demand, no invented quotes, no unsourced keyword volumes.

## How it works

```
Product hypothesis (CLI)
        │
        ▼
ResearchOrchestrator ── runs phases in sequence, stops at first FAIL
        │
        ├─ Phase 1  Signal Discovery      → is there a real market/category?
        ├─ Phase 2  Buyer Language        → real pain in buyers' own words
        ├─ Phase 3  Price Band Mapping     → what comparables actually cost
        ├─ Phase 4  Competitor Presence    → what competitors do structurally
        └─ Phase 5  Missing-Mechanism Gap  → is the gap structural, not cosmetic?
        │
        ▼
DecisionEngine → BUILD / REVISE / PARK / KILL  + evidence-quality score
        │
        ├─ Markdown brief  → briefs/<id>-demand-brief.md   (git-tracked)
        ├─ JSON export     → data/briefs/<id>-brief.json
        └─ Notion page     → created when NOTION_API_KEY is set
```

Each phase does two real model calls:

1. **Research** — Claude (`claude-opus-4-8`) with the `web_search` server tool
   searches the live web and reports what it actually found, with URLs.
2. **Extract** — a tool-free structured pass turns those findings into typed
   `SourceCard`s, which are then validated (real URL, recent date, realistic
   price, no AI-speak in quotes). Anything that fails validation is dropped.

A phase PASSes only with **3+ validated sources** (Phase 5 instead requires a
named *structural* gap). The decision and evidence-quality score follow from
how much real evidence survived.

## Quick start

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...      # required to run live research

# From a YAML hypothesis file:
demand-research research --from-file example_product.yaml

# Or interactively (prompts for any field you don't pass as a flag):
demand-research research
```

Outputs land in `briefs/` (markdown, committed alongside your repo) and
`data/briefs/` (JSON). If `NOTION_API_KEY` and `NOTION_DATABASE_ID` are set, a
Notion page is also created.

### Other commands

```bash
demand-research list-briefs          # list completed briefs
demand-research view briefs/<file>   # print a brief
```

## Configuration

Set via environment or a `.env` file in the project root:

| Variable              | Default            | Purpose                                  |
| --------------------- | ------------------ | ---------------------------------------- |
| `ANTHROPIC_API_KEY`   | —                  | **Required** to run live research        |
| `ANTHROPIC_MODEL`     | `claude-opus-4-8`  | Research/extraction model                |
| `ANTHROPIC_EFFORT`    | `high`             | Effort level (`low`/`medium`/`high`/`xhigh`/`max`) |
| `NOTION_API_KEY`      | —                  | Enables Notion page creation             |
| `NOTION_DATABASE_ID`  | —                  | Target Notion database for new pages     |
| `LOG_LEVEL`           | `INFO`             | Logging verbosity                        |

If credentials are missing, the CLI **refuses to emit a verdict** and tells you
to set `ANTHROPIC_API_KEY` — it never turns an infrastructure failure into a
fake KILL.

## Decision rules

| Decision   | When                                                                 |
| ---------- | -------------------------------------------------------------------- |
| **BUILD**  | All 5 phases pass with strong evidence (quality ≥ 0.75)              |
| **REVISE** | All phases pass but evidence is moderate (0.50–0.75)                 |
| **PARK**   | Weak evidence overall, or Phase 2 fails (market unclear, revisit)   |
| **KILL**   | Phase 1 fails — no real market signal at all                        |

Evidence quality blends source count, recency, and quote authenticity
(see `decision_engine.py`).

## Project layout

```
src/demand_research/
├── cli.py                       # entry point (`demand-research`)
├── models.py                    # Pydantic schemas (hypothesis, source card, brief)
├── config.py                    # env-driven settings
├── decision_engine.py           # BUILD/REVISE/PARK/KILL + quality score
├── agents/
│   ├── orchestrator.py          # runs the 5 phases, routes to the engine
│   └── phase_agents.py          # Phase 1–5 + shared research pipeline
├── research/
│   ├── claude_researcher.py     # Anthropic web-search + structured extraction
│   ├── evidence_validator.py    # rejects placeholder/AI-speak/stale sources
│   └── source_collector.py      # product-type heuristic
└── outputs/
    ├── markdown_generator.py    # git-tracked brief
    └── notion_generator.py      # Notion REST page (when configured)

tests/test_workflow.py           # end-to-end tests with a fake Anthropic client
```

## Testing

The full pipeline is covered with a dependency-injected fake client, so tests
run offline:

```bash
pip install pytest
pytest -q
```

Tests assert: all-pass → BUILD/REVISE, Phase 1 empty → KILL, placeholder URLs
are dropped, markdown renders, missing credentials raise (never fake a verdict),
and JSON parsing is defensive.

## Methodology

This implements an E0 → E1 demand-brief workflow. The discipline is the point:
every claim traces to a real, dated URL; buyer quotes are verbatim or explicitly
marked composite; "no evidence found" is an honest, valuable result rather than
something to paper over. Run each product idea through its own brief.
