# Evidence Rules

> **A demand brief is not trusted because it sounds rigorous. It is trusted only
> when its claims can be traced back to durable source, search, rejection, and
> scoring records.**

This document is the human-readable specification of how the demand-research
system decides what counts as evidence, how it grades that evidence, how it
turns evidence into a verdict, and what it refuses to do. The code in
`decision_engine.py`, `audit/grading.py`, `audit/claims.py`, and the phase
agents implements exactly what is written here.

---

## 1. Purpose

The system exists to **prevent premature product building**. It takes a product
hypothesis and tries — using real web evidence only — to decide whether the idea
deserves build time. A clean "no evidence found" is a valid and valuable result,
not a failure to paper over. The verdict is one of:

`BUILD` · `TEST` · `REVISE` · `PARK` · `KILL`

The final markdown brief is a summary. The `runs/{run_id}/` folder is the truth
layer; the brief is only trusted because that folder can corroborate it.

---

## 2. Evidence grades (A / B / C / D)

| Grade | Meaning | Examples |
| ----- | ------- | -------- |
| **A** | Behavioral / purchase-intent | Paid purchases, checkout attempts, waitlist/pre-order signups, paid-tool demand, bestseller indicators tied to a specific need |
| **B** | Direct buyer-language | Reddit/forum complaints, Etsy seller-forum posts, YouTube comments, review language — verbatim statements of pain |
| **C** | Category / competitor / content | Marketplace listings, competitor pages, category pages, video titles, blog posts, template directories |
| **D** | Weak / inferred / adjacent | Broad trend articles, generic content, AI-written pages, paraphrases, indirect adjacent demand |

Grading is deterministic (`audit/grading.py`). The same source always receives
the same grade, and the brief and its source ledger can never disagree.

---

## 3. What each grade can and cannot prove

- **Grade A** can prove people will *act* (pay, sign up). It is the only grade
  that, on its own, supports a purchase-intent claim.
- **Grade B** can prove people *feel and articulate* a pain. It supports
  buyer-pain claims. It does **not** prove they will pay.
- **Grade C** can prove a *category/competitor exists*. It supports
  category-existence and competitor-density claims. **A Grade C source may not
  satisfy a Grade B buyer-language requirement.**
- **Grade D** supports nothing on its own; it is context only.

---

## 4. Phase requirements

| Phase | Purpose | Pass condition |
| ----- | ------- | -------------- |
| 1 — Signal Discovery | Does the category/market exist? | ≥3 validated market signals (Grade C ok) |
| 2 — Buyer Language | Real pain in buyers' own words | **≥3 verbatim buyer-language artifacts (Grade B)** — listings/paraphrases do not count |
| 3 — Price Band Mapping | What comparables cost | ≥3 competitor prices with URLs |
| 4 — Competitor Presence | What competitors do structurally | ≥3 competitors with a structural teardown |
| 5 — Missing-Mechanism Gap | Is the gap structural, not cosmetic? | A named structural gap, judged against observed competitor structures |

---

## 5. Decision definitions

- **KILL** — Insufficient category or buyer evidence. Do not revisit unless the
  hypothesis changes.
- **PARK** — Market may exist, but required evidence is missing, or the run was
  partial. Revisit later with better searches or new data.
- **REVISE** — Evidence exists, but buyer, channel, mechanism, or positioning
  needs revision before testing.
- **TEST** — Enough evidence to justify a cheap external test (fake-door /
  pre-order), but not enough to build the full product.
- **BUILD** — Only after strong evidence or a successful behavioral/fake-door
  validation.

---

## 6. Hard gates (deterministic; cannot be overridden by prose)

Hard gates can only **lower** a verdict, never raise it. They are evaluated in
`decision_engine.evaluate_gates`:

1. **category_signal_minimum** — fewer than 3 category signals caps at PARK
   (KILL if zero).
2. **buyer_language_for_build** — fewer than 3 verbatim buyer-language artifacts
   caps the verdict at REVISE (cannot BUILD/TEST).
3. **any_pain_or_behavioral** — zero buyer-language *and* zero behavioral intent
   caps at REVISE.
4. **grade_c_ceiling** — if evidence is mostly Grade C/D (no Grade A and fewer
   than 3 Grade B), the verdict cannot exceed REVISE without a behavioral test.
5. **tool_failure** — a partial run (search/tool/model failure) caps at PARK.

The LLM cannot talk its way past these gates.

---

## 7. Scoring formula (v1)

The evidence-quality score is a weighted sum, exposed component-by-component in
`evidence_scorecard.json` and in the brief:

| Component | Weight | Raw score |
| --------- | ------ | --------- |
| phase_pass_ratio | 0.40 | passed phases / 5 |
| source_count | 0.30 | clamped (sources − 3) / (15 − 3) |
| recency | 0.20 | sources within 90 days / total |
| direct_quote_ratio | 0.10 | verbatim direct quotes / total |

Score-to-tier (before gates): `≥0.75 → BUILD`, `0.60–0.75 → TEST`,
`0.50–0.60 → REVISE`, `<0.50 → PARK`. Gates then apply.

---

## 8. Known limitations

- **Provider-level search capture** depends on the model emitting
  `server_tool_use` blocks. When those are unavailable, search attempts are
  marked `anthropic_web_search` and may under-count; the manifest notes this.
- **Grade A is conservative.** The system does not infer purchases from
  listings; behavioral intent must be explicit, so Grade A is rare by design.
- **Reproducibility is approximate.** Live web results change between runs; the
  run manifest captures model, commit, prompts, and counts so two runs can be
  *compared*, not byte-reproduced.

---

## 9. Rules against fabricated buyer language

- Buyer-language artifacts must be **verbatim**. Paraphrases are graded D and
  cannot satisfy a buyer-language requirement.
- Quotes flagged as AI-speak or generic/templated are rejected and logged to
  `rejected_sources.jsonl` with reason `ai_speak_detected`.
- Every artifact links back to a `source_id` and a real URL.
- If no valid artifacts are found, the file exists but is empty and Phase 2
  fails — the brief says so explicitly.

---

## 10. Rules for failed searches and tool errors

- Every search attempt is logged to `search_log.jsonl`, including
  `zero_results`, `tool_error`, and `rate_limited` outcomes.
- Failed searches are never buried in prose findings.
- A run that suffers tool/search failure is marked `partial` in the manifest and
  cannot produce a BUILD verdict.
- If research cannot run at all (e.g. missing credentials), the system writes a
  **failed** manifest and refuses to emit a verdict — it never fakes a KILL.

---

## 11. Rerun / reproducibility expectations

Each run writes `run_manifest.json` capturing: run id, UTC timestamps, repo
commit hash (or a note if git is unavailable), model name/provider, the exact
phase prompts sent, raw vs. validated vs. rejected source counts, the final
decision, and the score. To compare two runs of the same hypothesis, diff their
manifests, source ledgers, and search logs — the differences explain any change
in verdict.
