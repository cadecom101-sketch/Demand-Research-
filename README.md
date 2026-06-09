# Autonomous Demand Brief Research Workflow

**Demand Research produces a reviewable `E1_CANDIDATE` demand brief for
`Base — Retail Instant-Download OS`, the first member under the
`Governed Solo-Operator Launch OS` primitive.** It collects and audits
documented desk evidence (niche, observed sources, buyer/search/listing
language, observed price band, competitor presence, missing-mechanism gap), runs
nine E1 review gates, and returns an **E1 review verdict** that a human can
approve for recording into Revenue OS.

```text
Governed Solo-Operator Launch OS          (primitive — Andrew-authored)
└── Base — Retail Instant-Download OS      (first member — validated here)
    └── first E1 demand brief being created now
```

It is **not** a BUILD recommender. It is **not** a public test runner. It is
**not** a POST_E1 validator. It does **not** record into Revenue OS, does
**not** unlock B3, and does **not** validate Member A or Member B. Its job is to
answer one question:

> Does Base have enough documented desk evidence to become `E1_CANDIDATE` and
> pass E1 review for recording into Revenue OS?

The verdict is one of **`E1_APPROVED_TO_RECORD` / `E1_REVISE_BEFORE_RECORDING` /
`E1_PARK` / `E1_KILL`**. The repo can reach at most **`E1_APPROVED_TO_RECORD`**;
recording itself (`E1_RECORDED`) and B2 acceptance happen **outside this repo**
in Revenue OS. BUILD is disabled — a five-phase desk-research run can never
justify BUILD.

The "eyes and hands" are the **Anthropic API's native web search tool** — Claude
searches the real web (Etsy, Gumroad, Notion Marketplace, Reddit, forums),
extracts structured source cards, and every card is validated before it counts.
No fabricated demand, no invented quotes, no unsourced keyword volumes.

> **The run folder is the truth layer.** The markdown brief is only the
> human-readable summary.
>
> Find the signal.
> Capture the buyer's words.
> Map the price.
> Tear down competitors.
> Name the missing mechanism.
> Then let the gates decide.

The original methodology lives in [`docs/E0_E1_WORKFLOW.md`](docs/E0_E1_WORKFLOW.md)
and [`docs/PROJECT_SOURCE.md`](docs/PROJECT_SOURCE.md).

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
DecisionEngine (internal score tier; BUILD disabled, capped to TEST)
        │
        ▼
E1 Review (9 gates) → E1_APPROVED_TO_RECORD / E1_REVISE_BEFORE_RECORDING /
                      E1_PARK / E1_KILL  + recording / B2 / B3 status
        │
        ├─ Markdown brief  → briefs/<id>-demand-brief.md   (git-tracked)
        ├─ JSON export     → data/briefs/<id>-brief.json
        ├─ Run folder      → runs/<run_id>/ (truth layer, incl. e1_review_gates.json)
        └─ Notion page     → created when NOTION_API_KEY is set
```

The nine E1 review gates are: `scope_lock`, `minimum_real_observed_evidence`,
`demand_signal_exists`, `buyer_language_captured`, `observed_price_band`,
`competitor_presence`, `specific_missing_mechanism_gap`,
`fit_to_andrew_authored_primitive`, and `no_fabrication`. The price-band gate
counts **verified observed prices only** — competitor leads without prices and
directional pricing articles never satisfy it.

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

# From a YAML hypothesis file (Base member, e1-demand-brief mode by default):
demand-research research --from-file example_product.yaml

# Or interactively (prompts for any field you don't pass as a flag):
demand-research research

# The workflow validates the Base member only. Member A/B fail scope lock:
demand-research research --target-member Base   # default
```

`--workflow-mode` defaults to `e1-demand-brief` and `--target-member` defaults
to `Base`. Passing `--target-member A` or `B` fails fast with a scope-lock
error: those members are deferred at E0 and are not validated by this repo.

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

## E1 review verdicts

The headline output is a recording-readiness verdict from the nine E1 gates:

| Verdict                        | When                                                                            |
| ------------------------------ | ------------------------------------------------------------------------------- |
| **E1_APPROVED_TO_RECORD**      | All nine gates pass (and the conservative engine cleared its TEST bar)           |
| **E1_REVISE_BEFORE_RECORDING** | Scope/fit/buyer-language/price/competitor/mechanism gate fails                   |
| **E1_PARK**                    | Too few observed sources, or no external demand signal                          |
| **E1_KILL**                    | Fabrication / evidence-integrity failure                                        |

When the verdict is `E1_APPROVED_TO_RECORD`: `recording_status = READY_TO_RECORD`,
`b2_acceptance_status = READY_FOR_ACCEPTANCE`, `b3_status = LOCKED`,
`public_execution_status = NONE`. The repo never sets `E1_RECORDED`, never marks
B2 accepted, and never unlocks B3 — those are external Revenue OS acts.

Internally the conservative `DecisionEngine` still scores the run
(KILL/PARK/REVISE/TEST), and **BUILD is disabled** (capped to TEST). The E1
gates can only make approval *harder* than the engine's TEST bar, never easier.

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

## Trust Standard

This repository is designed to prevent premature product building.

A demand brief is not trusted unless the run preserves:

1. the exact searches attempted,
2. the sources found,
3. the sources rejected,
4. the reasons sources were rejected,
5. the direct buyer-language artifacts used,
6. the claims made,
7. the source IDs supporting each claim,
8. the evidence score breakdown,
9. the hard gates that passed or failed,
10. the run conditions needed for rerun comparison.

The final markdown brief is only the human-readable summary. **The run folder is
the audit trail.** Every run writes `runs/{run_id}/`:

```
runs/{run_id}/
  run_manifest.json               # model, commit, prompts, counts, E1 review state
  search_log.jsonl                # every search attempt (incl. zero-result/errors)
  rejected_sources.jsonl          # every dropped source + reason + rule
  source_ledger.jsonl             # every validated source, graded A/B/C/D
  buyer_language_artifacts.jsonl  # verbatim quotes linked to source IDs
  price_band_artifacts.jsonl      # VERIFIED observed competitor prices only
  competitor_map.jsonl            # 10-field map + 6-dimension teardown
  missing_mechanism_gap.json      # structural gap analysis
  claim_ledger.jsonl              # each claim + supporting source IDs + status
  evidence_scorecard.json         # the visible scoring formula + components
  e1_review_gates.json            # nine E1 gates + verdict + recording/B2/B3 status
  demand_brief.md                 # human-readable summary
  demand_brief.json               # machine-readable brief
```

The grading rules, phase requirements, hard gates, and scoring formula are
specified in [`docs/EVIDENCE_RULES.md`](docs/EVIDENCE_RULES.md).

## Scope and boundaries

Demand Research currently produces an `E1_CANDIDATE` demand brief for
`Base — Retail Instant-Download OS`, the first member under the
`Governed Solo-Operator Launch OS` primitive. It collects and audits documented
demand signal: niche/sub-niche, observed sources, buyer/search/listing language,
observed price band, competitor presence, missing-mechanism gap, and a demand
brief with cited observations.

It is **not** a BUILD recommender, **not** a public test runner, and **not** a
POST_E1 validator. **Revenue OS recording is the separate, external act that
turns `E1_CANDIDATE / E1_APPROVED_TO_RECORD` into `E1_RECORDED` and satisfies B2
acceptance.** This repo never performs it.

Conservative behavior is preserved end-to-end:

- No fabricated evidence, no fake buyer quotes, no guessed prices.
- No generic scraping, no public execution (publishing/listing/ads/contact).
- No B3 unlock; no Member A / Member B review.
- The price-band integrity rule holds: competitor leads without prices and
  directional pricing articles never satisfy the price band; only verified
  observed prices land in `price_band_artifacts.jsonl`.
- The run folder remains the truth layer; the markdown brief is only the
  human-readable summary.

## Methodology

This implements an E0 → E1 demand-brief workflow. The discipline is the point:
every claim traces to a real, dated URL; buyer quotes are verbatim or explicitly
marked composite; "no evidence found" is an honest, valuable result rather than
something to paper over. Run each product idea through its own brief.
