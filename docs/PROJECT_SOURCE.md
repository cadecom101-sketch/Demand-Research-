# Demand Research — Project Source of Truth

This file is the durable project context for the Demand Research repository. It
combines (1) the original E0 → E1 demand-brief workflow, (2) the current
audit/provenance architecture, (3) the conservative decision rules, and (4) the
operating standard for future autonomous, reusable product research.

Companion docs: `docs/E0_E1_WORKFLOW.md` (the workflow) and
`docs/EVIDENCE_RULES.md` (grades, gates, scoring).

---

## 1. Project purpose

Demand Research is an autonomous, reusable **E0 → E1-candidate evidence gate**
for product ideas. It helps decide whether an idea earns the next cheapest test.
It exists to stop building from excitement alone. It is **not** optimized to
produce BUILD decisions — it is optimized to avoid *false* BUILD decisions.

## 2. Core trust principle

A demand brief is not trusted because it sounds rigorous. It is trusted only
when its claims trace back to durable search, source, rejection, buyer-language,
claim, scoring, and run records.

```
No artifact, no trust.
No buyer language, no BUILD.
Grade C proves category only.
If Phase 2 fails, PARK.
```

**The markdown brief is not the truth layer. The run folder is the truth layer.**

## 3. E0 → E1 concept

- **E0** — an unproven idea; interesting, but has not earned build time.
- **E1-candidate** — enough evidence to justify the next cheapest external test.
- **POST-E1** — past validation; build-justified.

Decision → stage mapping:

```
KILL   -> E0           (abandon unless hypothesis changes)
PARK   -> E0           (revisit later with better evidence)
REVISE -> E0           (modify buyer/channel/mechanism/positioning)
TEST   -> E1_CANDIDATE (run cheap external validation)
BUILD  -> POST_E1      (only after strong/behavioral validation)
```

The brief should usually decide whether the idea earns TEST, not BUILD.

## 4. Product hypothesis input

Required fields: product idea, target buyer, buyer job, product format, primary
channel, missing-mechanism hypothesis. Every idea runs through the workflow
separately; do not reuse one brief across products.

## 5. Five-phase research workflow

1. **Signal Discovery** — does the category/buyer job exist? PASS: 1–3 real
   market signals.
2. **Buyer Language Mining** — real pain in buyers' words. PASS: ≥3 verbatim
   buyer-language artifacts. Composite language must be labeled and never faked.
3. **Price Band Mapping** — what comparables cost. PASS: ≥3 real prices +
   low/mid/premium map. (Fixed name — not "Price and Mapping".)
4. **Competitor Presence** — what competitors structurally do. PASS: ≥3
   competitors with a 10-field map + 6-dimension teardown.
5. **Missing-Mechanism Gap** — the structural mechanism missing from the market.
   PASS: a specific, supported mechanism (not aesthetic/feature-only).

## 6. Evidence grades

- **A** behavioral / purchase-intent · **B** direct buyer-language ·
  **C** category/competitor/content (category only) · **D** weak/inferred.
- Grade C cannot satisfy a Phase 2 buyer-language requirement.

## 7. Source card template

```
Source number / Source name / URL / Date observed / Platform /
Search phrase used / Price observed / Buyer language captured /
Competitor features observed / What this source proves /
What this source does NOT prove / Screenshot filename / Gap note /
Evidence type / Evidence grade
```

## 8. Canonical run folder (truth layer)

```
runs/{timestamp}Z_{product-slug}/
  run_manifest.json
  search_log.jsonl
  rejected_sources.jsonl
  source_ledger.jsonl
  buyer_language_artifacts.jsonl
  price_band_artifacts.jsonl
  competitor_map.jsonl
  missing_mechanism_gap.json
  claim_ledger.jsonl
  evidence_scorecard.json
  demand_brief.md
  demand_brief.json
```

## 9. Required demand brief sections

```
1. Executive Summary           11. Claim Ledger Summary
2. Product Hypothesis          12. Search Log Summary
3. Evidence Stage              13. Rejected Sources Summary
4. Phase Results               14. What This Proves
5. Evidence Quality Score      15. What This Does NOT Prove
6. Hard Gate Results           16. What Would Change This Decision
7. Buyer-Language Artifacts    17. Next Recommended Experiment
8. Price Band Mapping          18. All Sources
9. Competitor Presence Map     19. Audit Artifacts
10. Missing-Mechanism Gap
```

If no buyer-language artifacts were collected, the brief says so explicitly. If
a later phase did not run because an earlier hard gate capped the decision, the
section says: "This section is incomplete because …".

## 10. Decision rules

```
KILL   — no market signal, no buyer pain, re-skin, or no nameable mechanism.
PARK   — may be useful later; evidence missing now (default when Phase 2 fails).
REVISE — evidence exists but buyer/channel/mechanism/price needs work.
TEST   — enough evidence for cheap external validation; E1-candidate.
BUILD  — only after strong/behavioral validation; post-E1.
```

## 11. Phase pass requirements

```
Phase 1: 1–3 real market/category/search signals.
Phase 2: 3 verbatim buyer-language artifacts (Grade B).
Phase 3: 3 real competitor prices + low/mid/premium map.
Phase 4: 3 real competitors mapped structurally (10-field + 6-dimension).
Phase 5: a specific missing mechanism, supported by competitor evidence.
```

A phase may be incomplete only if an earlier hard gate caps the decision; the
brief must say what was not completed and why.

## 12. E0 → E1-candidate acceptance gate

E1-candidate (TEST) requires: Phase 1 passed; ≥3 buyer-language artifacts OR
Grade-A behavioral; ≥3 observed prices; ≥3 structured competitors; a
supported/partially-supported missing mechanism; no hard gate capping below
TEST. Otherwise the idea stays E0. BUILD requires stronger evidence than
E1-candidate.

## 13. Adapters

- **General digital products / Etsy / Notion / seller templates** — buyer search
  intent, competitor listings, price bands, seller pain, fee stress, listing
  readiness, post-launch review.
- **Technical ecosystem products** — GitHub issues, API/dependency changelogs,
  forum threads, integration complaints; look for ecosystem fracture, dependency
  breakage, setup pain, version mismatch, maintenance burden.
- **Productized service / capacity products** — setup packages, customization
  offers, build services, scope/delivery/revision complaints; look for capacity
  collapse, scope creep, missed deadlines, revision overload, margin erosion.

## 14. Non-negotiables

Durable run folders; search logging; rejected-source logging; buyer-language
artifact requirements; evidence-grade separation; claim ledger; hard gates;
score breakdown; valid JSON artifacts; conservative PARK when buyer language is
missing; price band mapping; competitor structural teardown; missing-mechanism
analysis. Do not weaken the conservative gates. Optimize to avoid false BUILD.

## 15. Memory hook

```
Find the signal.
Capture the buyer's words.
Map the price.
Tear down competitors.
Name the missing mechanism.
Then let the gates decide.
```
