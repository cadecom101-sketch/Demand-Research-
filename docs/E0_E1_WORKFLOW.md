# E0 → E1 Demand Brief Research Workflow

This document preserves the original workflow concept for the Demand Research
repository. The **workflow layer** defines *what* the five phases must research;
the **audit layer** (`runs/{run_id}/`) proves *whether* those phases can be
trusted. See also `docs/PROJECT_SOURCE.md` (source of truth) and
`docs/EVIDENCE_RULES.md` (grades, gates, scoring).

> Memory hook:
> Find the signal. Capture the buyer's words. Map the price. Tear down
> competitors. Name the missing mechanism. Then let the gates decide.

---

## Purpose

The workflow helps decide whether a product idea deserves build time. It stops
product building from excitement alone. The goal is not to prove a product will
sell — it is to collect enough real-world evidence to answer:

- Is there a real buyer job?
- Are people already looking for this kind of solution?
- Are comparable products already present?
- What do buyers complain about or ask for?
- What price range exists?
- What mechanism is missing from current products?

If those questions cannot be answered with real observed sources, the idea
stays **E0**.

---

## Product hypothesis input

Before running the five phases, write one clear hypothesis: product idea,
target buyer, buyer job, product format, primary channel, and a one-sentence
missing-mechanism guess. Every product idea runs through the workflow
separately — do not reuse one brief across products.

---

## The five phases (fixed names)

### Phase 1 — Signal Discovery
Check whether the category, buyer job, or search intent exists in the real
market. **PASS** if at least 1–3 real market/category/search signals show the
category or buyer job exists. **FAIL** if no real presence is found. Never claim
exact keyword volume without a retrievable number/screenshot.

### Phase 2 — Buyer Language Mining
Collect the words buyers/sellers use to describe the pain. **PASS** with at
least 3 valid buyer-language artifacts or review/forum observations. **FAIL** if
only invented/composite language exists. Composite language must be labeled
`COMPOSITE BUYER LANGUAGE — NOT A DIRECT QUOTE` and never passed off as a real
quote. Repo hard gate: zero valid artifacts ⇒ BUILD forbidden; Phase 2 fail ⇒
default PARK.

### Phase 3 — Price Band Mapping
(Fixed name — *not* "Price and Mapping".) Find what comparable products actually
cost. **PASS** with at least 3 real competitor prices and a low/mid/premium map
(low $0–$12, mid $15–$24.99, premium $29+). **FAIL** if prices are guessed or
lack URLs/artifacts. A premium price must be *earned structurally* (gates,
formulas, decision logic, review cycles, fee stress, authorship, launch
readiness, prune/revise/expand logic).

### Phase 4 — Competitor Presence
Phase 3 asks what they charge; Phase 4 asks what they actually *do*. Produce a
10-field competitor map and a 6-dimension teardown (demand validation,
authorship evidence, build-readiness gate, listing-readiness gate, fee-stress
logic, post-launch decision loop — each none/weak/present/strong/unknown).
**PASS** with at least 3 real competitors mapped structurally. **FAIL** if the
brief only says competitors exist. Distinguish a product that **stores**
information from one that **forces** a decision.

### Phase 5 — Missing-Mechanism Gap
Identify the specific *operating mechanism* missing from the market — not a
feature or aesthetic gap. Key question: *What can my product force the buyer to
do that current products do not?* Test: if a competitor added better design,
more pages, or better copy, would my system still be structurally different?
**PASS** if a specific missing mechanism is named and supported by competitor
evidence. **FAIL** if the gap is only "looks better / cleaner / cheaper / more
pages / different buyer label / more features."

---

## Evidence grades

- **A** — behavioral / purchase-intent (purchases, reviews tied to a need,
  checkout attempts, waitlist signups, bestseller indicators, fake-door data).
- **B** — direct buyer-language (Reddit/forum complaints, review language,
  verbatim statements of pain).
- **C** — category / competitor / content (listings, competitor pages, category
  pages, titles, blog posts). Proves *category only* — not pain or intent.
- **D** — weak / inferred / adjacent (trend articles, generic/AI content).

Grade C cannot satisfy a Phase 2 buyer-language requirement.

---

## Decision definitions and evidence stages

The conservative engine still scores each run internally
(KILL/PARK/REVISE/TEST). **BUILD is disabled** in the E1 demand-brief workflow
(capped to TEST). The internal score tier maps to the E1 review verdict:

| Internal tier | E1 review verdict              | Evidence stage           |
| ------------- | ------------------------------ | ------------------------ |
| KILL          | E1_KILL (via no_fabrication)   | E0_AUTHORED_CAPTURED     |
| PARK          | E1_PARK                        | E0_AUTHORED_CAPTURED     |
| REVISE        | E1_REVISE_BEFORE_RECORDING     | E0_AUTHORED_CAPTURED     |
| TEST          | E1_APPROVED_TO_RECORD          | E1_APPROVED_TO_RECORD    |
| BUILD         | *disabled — capped to TEST*    | —                        |

The headline verdict is produced by the nine E1 review gates (see
`e1_review.py`). The repo can reach at most `E1_APPROVED_TO_RECORD`;
`E1_RECORDED` and B2 acceptance happen externally in Revenue OS.

---

## Hard gates (conservative; cannot be overridden by prose)

- `category_signal_minimum` — < required signals ⇒ KILL (zero) / PARK.
- `buyer_language_missing` — no verbatim Grade-B artifacts ⇒ PARK.
- `no_grade_a_or_b_evidence` — no Grade A/B ⇒ PARK.
- `grade_c_only_ceiling` — only Grade C ⇒ PARK.
- `phase_2_failed` — Phase 2 failed ⇒ PARK.
- `tool_failure` — partial run ⇒ PARK.
- `e1_candidate_acceptance` — incomplete evidence chain ⇒ cannot exceed REVISE
  (stays E0).

---

## B2 acceptance gate (E0 → E1-candidate)

An idea may reach E1-candidate (TEST) only with: Phase 1 passed; ≥3 buyer-
language artifacts (or explicit Grade-A behavioral); ≥3 observed prices; ≥3
structured competitors; a supported/partially-supported missing mechanism; and
no hard gate capping below TEST. Otherwise it stays E0.

`E1_APPROVED_TO_RECORD` requires *more* than this: all nine E1 review gates must
pass (including ≥5 buyer-language phrases, verified observed prices, scope lock
to the Base member, and fit to the authored primitive) **and** the conservative
engine must have cleared its TEST bar. Recording into Revenue OS — which turns
`E1_CANDIDATE / E1_APPROVED_TO_RECORD` into `E1_RECORDED` and satisfies B2 — is
external to this repo.

---

## Non-negotiables

Durable run folders; search logging; rejected-source logging; buyer-language
artifact requirements; evidence-grade separation; claim ledger; hard gates;
score breakdown; valid JSON artifacts; conservative PARK behavior when buyer
language is missing; price band mapping; competitor structural teardown;
missing-mechanism gap analysis. **The run folder is the truth layer.** Do not
optimize the repo to produce more BUILD decisions — optimize it to avoid false
BUILD decisions.
