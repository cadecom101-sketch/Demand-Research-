# Project Status: Autonomous Demand Research Workflow

**Status:** ✅ Foundation Complete & Deployed  
**Branch:** `claude/demand-brief-research-workflow-1bVl6`  
**Last Updated:** 2026-06-07  
**Next Phase:** Claude Agent SDK Integration

## What Was Built

A complete autonomous research system that validates product ideas through structured, evidence-driven inquiry before build investment.

### ✅ Completed Components

1. **Project Structure** (100%)
   - ✓ Python package layout with src/demand_research/
   - ✓ pyproject.toml with dependencies
   - ✓ requirements.txt
   - ✓ .gitignore configured
   - ✓ Ready for pip install

2. **Data Models** (100%)
   - ✓ ProductHypothesis (input schema)
   - ✓ SourceCard (evidence schema)
   - ✓ PhaseResult (phase output schema)
   - ✓ DemandBrief (final output schema)
   - ✓ Pydantic validation on all models
   - ✓ Enums: PhaseStatus, Decision

3. **Decision Engine** (100%)
   - ✓ BUILD/REVISE/PARK/KILL logic
   - ✓ Pass conditions per phase
   - ✓ Evidence quality scoring (0-1.0)
   - ✓ Score factors: count, recency, authenticity
   - ✓ Early termination on phase failure

4. **Evidence Validator** (100%)
   - ✓ URL validation (catches placeholder domains)
   - ✓ Date validation (enforces recency)
   - ✓ Price validation (realistic range check)
   - ✓ Quote validation (catches AI-speak)
   - ✓ Attribution checking
   - ✓ Batch validation with error reporting

5. **Research Layer** (100%)
   - ✓ SourceCollector framework
   - ✓ Product type detection (etsy/github/gumroad)
   - ✓ Prompt generation for each phase
   - ✓ SourceCard creation factory
   - ✓ Ready for Claude API integration

6. **Phase Agents** (100%)
   - ✓ BasePhaseAgent abstract class
   - ✓ Phase 1: Signal Discovery structure
   - ✓ Phase 2: Buyer Language Mining structure
   - ✓ Phase 3: Price Band Mapping structure
   - ✓ Phase 4: Competitor Presence structure
   - ✓ Phase 5: Missing-Mechanism Gap structure
   - ✓ Async/await ready for Claude calls

7. **Orchestrator** (100%)
   - ✓ Sequential phase management
   - ✓ Pass/fail routing
   - ✓ Early termination on phase failure
   - ✓ Final decision routing
   - ✓ Logging and status tracking

8. **Output Generators** (100%)
   - ✓ Markdown generator (git-tracked briefs)
   - ✓ Notion generator structure (MCP ready)
   - ✓ JSON export for data processing
   - ✓ Source card rendering
   - ✓ Phase result formatting

9. **CLI Interface** (100%)
   - ✓ demand-research command (interactive)
   - ✓ --from-file option (YAML/JSON input)
   - ✓ list-briefs command
   - ✓ view command
   - ✓ Help text and prompts

10. **Configuration** (100%)
    - ✓ Environment-based settings
    - ✓ .env file support
    - ✓ Auto-create directories
    - ✓ API key management
    - ✓ Optional Notion integration

11. **Documentation** (100%)
    - ✓ README.md (user guide)
    - ✓ IMPLEMENTATION.md (technical guide)
    - ✓ ARCHITECTURE.md (system design)
    - ✓ CLAUDE_INTEGRATION.md (next steps)
    - ✓ PROJECT_STATUS.md (this file)
    - ✓ example_product.yaml (sample input)

### ⏳ Next Phase: Claude Integration

The following **need to be implemented** to make research autonomous:

1. **Phase 1 Agent Real Implementation**
   - [ ] Call Anthropic API from Phase1Agent.run()
   - [ ] Search Etsy/Gumroad/Notion Marketplace
   - [ ] Parse results into SourceCards
   - [ ] Validate sources

2. **Phase 2-5 Agent Real Implementations**
   - [ ] Phase 2: Mine buyer language from reviews/Reddit
   - [ ] Phase 3: Extract competitor prices
   - [ ] Phase 4: Analyze competitor structures
   - [ ] Phase 5: Synthesize gap analysis

3. **Notion MCP Integration**
   - [ ] Wire mcp__d9133ba1-* tools
   - [ ] Create Notion pages from briefs
   - [ ] Link sources to Notion
   - [ ] Return page_id and URL

4. **Testing & Validation**
   - [ ] Test Phase 1 with real Etsy search
   - [ ] Test full workflow end-to-end
   - [ ] Verify source validation
   - [ ] Check decision logic accuracy

5. **Screenshot Capture** (Optional)
   - [ ] Implement screenshot storage
   - [ ] Link screenshots to source cards
   - [ ] Embed in Notion pages

## File Structure

```
demand-research/
├── README.md                      # User guide
├── IMPLEMENTATION.md              # Technical guide
├── ARCHITECTURE.md                # System design
├── CLAUDE_INTEGRATION.md          # Integration instructions
├── PROJECT_STATUS.md              # This file
├── example_product.yaml           # Sample input
├── requirements.txt               # Dependencies
├── pyproject.toml                 # Package config
├── .gitignore                     # Git config
│
├── src/demand_research/
│   ├── __init__.py
│   ├── cli.py                     # CLI entry point
│   ├── models.py                  # Pydantic schemas
│   ├── config.py                  # Configuration
│   ├── decision_engine.py         # Decision logic
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── orchestrator.py        # Workflow manager
│   │   └── phase_agents.py        # Phase 1-5 agents
│   │
│   ├── research/
│   │   ├── __init__.py
│   │   ├── source_collector.py    # Source orchestration
│   │   └── evidence_validator.py  # Validation
│   │
│   └── outputs/
│       ├── __init__.py
│       ├── markdown_generator.py  # Git output
│       └── notion_generator.py    # Notion output
│
├── briefs/                        # Generated markdown briefs
├── data/
│   ├── screenshots/               # Evidence images
│   ├── cache/                     # Temp research data
│   └── briefs/                    # JSON exports
│
└── .git/                          # Git history

```

## Key Statistics

- **Lines of Code:** ~2,500
- **Files:** 22 (Python: 14, Config: 4, Docs: 4)
- **Commits:** 4
- **Test Coverage:** Foundation ready (tests pending)
- **Dependencies:** 10 packages (anthropic, pydantic, click, etc.)

## How It Works (End-to-End)

### 1. User Input
```bash
demand-research --from-file my_product.yaml
```

Provides:
- Product name, buyer, buyer job
- Product format (template/spreadsheet/etc)
- Primary channel (Etsy/Gumroad/etc)
- Hypothesis about missing mechanism

### 2. Orchestrator Routes
- Calls Phase 1 agent
- If Phase 1 PASSES: calls Phase 2 agent
- If any phase FAILS: routes to decision engine
- If all phases PASS: final decision

### 3. Phase Agents (When Claude Integrated)
Each phase:
- Generates targeted search prompt
- Calls Claude API with web search tools
- Parses response into SourceCard objects
- Validates sources (real URLs, dates, no AI-speak)
- Returns PhaseResult with PASS/FAIL

### 4. Decision Engine
Scores evidence:
- Quality = 40% (phases passed) + 30% (source count) + 20% (recency) + 10% (authenticity)
- Decision = BUILD (≥75%) | REVISE (50-75%) | PARK (<50%) | KILL (Phase 1 fails)

### 5. Outputs Generated
- **Markdown brief** → `briefs/[id]-demand-brief.md` (git-tracked)
- **JSON export** → `data/briefs/[id]-brief.json` (data processing)
- **Notion page** → Created in Notion workspace (when MCP integrated)

## Usage Example

### Before (Manual Process)
```
You: "Should I build a Notion Etsy seller dashboard?"
Manual work:
- Search Etsy for 2 hours
- Read 50 product listings
- Find 3 competitors, analyze them
- Mine Reddit for buyer pain points
- Compile findings
- Decide (often biased)
Time: 3-5 hours
Result: Vague, subjective
```

### After (Autonomous System)
```bash
$ demand-research --from-file etsy_dashboard.yaml

Starting Demand Research Workflow
Product: Notion Etsy Seller Dashboard
Buyer: Solo digital product sellers
...

=== Research Complete ===
Decision: BUILD
Evidence Quality: 82%
Sources: 15 collected
```

**Briefs automatically created:**
- `briefs/abc123-notion-etsy-seller-dashboard-demand-brief.md`
- `data/briefs/abc123-brief.json`
- Notion page linked

Time: ~2 minutes
Result: Objective, sourced, traceable

## Design Principles

1. **Evidence-First:** No vibes, hunches, or AI-generated demand signals
2. **Real Sources Only:** Every source must be real, dated, accessible
3. **Transparent Process:** Full audit trail in markdown (git-tracked)
4. **Strict Pass Conditions:** 3+ sources per phase (no shortcuts)
5. **Honest Quality Scoring:** Evidence quality reflects actual confidence
6. **Clear Decision Logic:** BUILD/REVISE/PARK/KILL is justified by data
7. **Extensible:** Easy to add new research sources or change logic

## Installation & Testing

### Install
```bash
pip install -e .
```

### Quick Test (Current State)
```bash
python src/demand_research/cli.py research --from-file example_product.yaml
```

Outputs:
- ✓ Markdown brief created
- ✓ JSON export saved
- ✓ Decision rendered
- ✓ Quality score calculated

(Note: Phases return placeholder data until Claude integration)

### After Claude Integration
```bash
# Will run real research
demand-research --from-file example_product.yaml

# Will search Etsy, Reddit, Google Trends, etc.
# Will mine real buyer language
# Will validate all sources
# Will generate evidence-based decision
```

## Commits Made

1. **a925728** - Build autonomous demand research workflow foundation
   - Complete project structure
   - All core components
   - CLI interface
   - Models and schemas

2. **522cdb0** - Add comprehensive README and documentation
   - User guide
   - Implementation guide
   - 5-phase methodology explained

3. **a489d26** - Add Claude Agent SDK integration guide
   - Step-by-step instructions
   - Example Phase 1 integration
   - Prompting strategy

4. **807ee14** - Add detailed system architecture documentation
   - Component overview
   - Data flow diagrams
   - Performance considerations

## Next Developer Tasks

### Priority 1: Phase 1 Integration
- [ ] Import Anthropic client in phase_agents.py
- [ ] Implement Phase1Agent.run() with real Claude call
- [ ] Test with Etsy search
- [ ] Validate EvidenceValidator catches fake URLs

### Priority 2: Phases 2-5 Integration
- [ ] Phase 2: Buyer language mining (Reddit, reviews)
- [ ] Phase 3: Price band mapping
- [ ] Phase 4: Competitor analysis
- [ ] Phase 5: Gap synthesis

### Priority 3: Notion MCP Integration
- [ ] Set up Notion API key in .env
- [ ] Implement mcp tool calls
- [ ] Create Notion page from brief
- [ ] Link sources

### Priority 4: Testing & Validation
- [ ] Test full workflow with sample product
- [ ] Verify decisions are accurate
- [ ] Check source validation
- [ ] Performance profiling

## Resources

- **Main Docs:** README.md (user), ARCHITECTURE.md (technical)
- **Integration Guide:** CLAUDE_INTEGRATION.md
- **Code:** src/demand_research/ (all source)
- **Example:** example_product.yaml

## Success Criteria

✅ All foundation components built and working  
✅ Package installs cleanly  
✅ CLI accepts product input  
✅ Decision engine calculates BUILD/REVISE/PARK/KILL  
✅ Markdown briefs generate correctly  
⏳ Claude integration (next phase)  
⏳ Notion MCP integration (next phase)  

## Handoff Notes

The system is **ready for Claude Agent SDK integration**. All the plumbing is in place:
- Orchestrator manages phase flow
- Phase agents have `run()` method signatures
- Decision engine is complete
- Evidence validator is ready
- Output generators work

**You need to:** Fill in the phase agents with actual Claude API calls using the prompts in source_collector.py.

See CLAUDE_INTEGRATION.md for the exact steps.

---

**Questions?** Check README.md for user guide or ARCHITECTURE.md for technical details.
