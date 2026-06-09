"""Evidence-seeking query planner.

This module makes the repo's *search* smarter without touching any gate,
threshold, or verdict rule. It turns the unchanged product hypothesis fields
(product_name, target_buyer, buyer_job, product_format, primary_channel,
secondary_channel, missing_mechanism_hypothesis) into diversified query
families for each research phase.

Design principles:
  - Reusable, not hard-coded for one marketplace. Every family is generated from
    the hypothesis fields via templates, so an Etsy seller and a Gumroad course
    creator both get appropriate, channel-aware queries.
  - It only *proposes searches*. It never invents sources, prices, quotes, or
    URLs. Whatever a search returns is still validated and graded downstream and
    still fails closed if it is thin or fabricated.
  - It never renames the product or edits the hypothesis. The product_name is
    read, never rewritten.

The output is consumed two ways:
  - phase agents append `prompt_appendix(...)` to their research prompt so the
    web-search model diversifies its angles;
  - the orchestrator writes the full `build_search_plan(...)` to search_plan.json
    and reuses the families in the next-evidence plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from demand_research.models import ProductHypothesis

# Known marketplace community forums, so a channel like "Etsy" yields the real
# `site:community.etsy.com` query the methodology calls for. Unknown channels
# fall back to plain "<channel> forum / community" text queries (still valid
# searches — never invented source URLs).
_COMMUNITY_SITES = {
    "etsy": "community.etsy.com",
    "shopify": "community.shopify.com",
    "gumroad": "gumroad.com/discover",
    "notion": "reddit.com/r/Notion",
}

# Generic social/forum search surfaces that work for any niche.
_SOCIAL_SITES = ("reddit.com", "quora.com", "youtube.com")

# Tokens too generic to be a useful search subject on their own.
_STOPWORDS = {
    "the", "a", "an", "for", "of", "and", "or", "os", "system", "tool", "toolkit",
    "kit", "app", "platform", "—", "-", "to", "with", "your", "my",
}


@dataclass
class QueryFamily:
    """A themed cluster of related search queries for one phase."""

    name: str
    intent: str
    phase: int
    queries: List[str] = field(default_factory=list)
    # The source types that, if found, would actually satisfy this family's gate.
    satisfying_source_types: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "intent": self.intent,
            "phase": self.phase,
            "queries": list(self.queries),
            "satisfying_source_types": list(self.satisfying_source_types),
        }


# --------------------------------------------------------------------------- #
# Field tokenization (generic — derives search vocabulary from the hypothesis)
# --------------------------------------------------------------------------- #
def _clean(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _keywords(text: Optional[str], limit: int = 6) -> List[str]:
    """Pull distinctive lowercase keywords from a field, dropping stopwords."""
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]+", (text or "").lower())
    out: List[str] = []
    for w in words:
        if w in _STOPWORDS or len(w) < 3:
            continue
        if w not in out:
            out.append(w)
        if len(out) >= limit:
            break
    return out


def _buyer_noun(hypothesis: ProductHypothesis) -> str:
    """A short, natural noun for the buyer (e.g. 'Etsy seller'), channel-aware."""
    buyer = _clean(hypothesis.target_buyer).lower()
    channel = _channel_word(hypothesis.primary_channel)
    # Prefer an explicit role word if present in the buyer description.
    for role in ("seller", "creator", "maker", "founder", "owner", "freelancer",
                 "designer", "developer", "shop owner", "entrepreneur"):
        if role in buyer:
            return f"{channel} {role}".strip() if channel else role
    return f"{channel} seller".strip() if channel else (buyer.split(",")[0][:40] or "seller")


def _channel_word(channel: Optional[str]) -> str:
    """First meaningful channel token, e.g. 'Etsy-first' -> 'Etsy'."""
    c = _clean(channel)
    if not c:
        return ""
    token = re.split(r"[\s/\-]+", c)[0]
    return token if token.lower() not in _STOPWORDS else c


def _format_noun(hypothesis: ProductHypothesis) -> str:
    """A natural product-format noun, e.g. 'digital download', 'Notion template'."""
    fmt = _clean(hypothesis.product_format).lower()
    if "instant" in fmt or "download" in fmt or "digital" in fmt:
        return "digital download"
    if "notion" in fmt:
        return "Notion template"
    if "spreadsheet" in fmt or "sheet" in fmt:
        return "spreadsheet template"
    if "course" in fmt:
        return "online course"
    kws = _keywords(hypothesis.product_format, limit=2)
    return " ".join(kws) if kws else "product"


def _channels(hypothesis: ProductHypothesis) -> List[str]:
    out = []
    for ch in (hypothesis.primary_channel, getattr(hypothesis, "secondary_channel", None)):
        w = _channel_word(ch)
        if w and w not in out:
            out.append(w)
    return out


def _site_queries(channel: str, phrase: str) -> List[str]:
    """site:-scoped queries: reddit always, plus the channel community if known."""
    out = [f"site:{site} {phrase}" for site in _SOCIAL_SITES]
    community = _COMMUNITY_SITES.get(channel.lower())
    if community:
        out.append(f"site:{community} {phrase}")
    else:
        out.append(f"{channel} forum {phrase}".strip())
    return out


# --------------------------------------------------------------------------- #
# Per-phase query families
# --------------------------------------------------------------------------- #
def _phase1_families(h: ProductHypothesis) -> List[QueryFamily]:
    buyer = _buyer_noun(h)
    fmt = _format_noun(h)
    channels = _channels(h) or [""]
    cat_q: List[str] = []
    for ch in channels:
        base = f"{ch} {fmt}".strip()
        cat_q += [
            f"{base} best sellers",
            f"{base} category",
            f"best selling {base}",
            f"{base} popular {f'on {ch}' if ch else ''}".strip(),
        ]
    intent_q = [
        f"how many people search for {fmt}",
        f"{fmt} demand trend",
        f"{buyer} {fmt} market size",
    ]
    return [
        QueryFamily(
            "category_existence", "Show the category/market visibly exists.", 1,
            _dedupe(cat_q),
            ["marketplace category page", "best-seller listing", "search-results page"],
        ),
        QueryFamily(
            "search_interest", "Show buyers are actively searching this need.", 1,
            _dedupe(intent_q),
            ["search-trend page", "keyword-volume source", "marketplace search page"],
        ),
    ]


def _phase2_families(h: ProductHypothesis) -> List[QueryFamily]:
    """Buyer-language pain mining — the heart of the search-intelligence upgrade.

    Generates emotional/venting, problem-first, symptom/workaround, question, and
    site-scoped families from the buyer + job + pain fields, for ANY niche.
    """
    buyer = _buyer_noun(h)
    fmt = _format_noun(h)
    channel = _channel_word(h.primary_channel)
    pain_kws = _keywords(h.buyer_job, limit=4) + _keywords(h.missing_mechanism_hypothesis, limit=3)
    pain_phrase = " ".join(pain_kws[:4]) or "no sales"

    venting = [
        f"{buyer} spent hours making {fmt} no sales",
        f"{buyer} wasted time making products that don't sell",
        f"so tired of making {fmt} that don't sell",
        f"anyone else struggle to decide what {fmt} to make",
        f"is it just me {buyer} no sales",
    ]
    problem_first = [
        f"{buyer} how decide what {fmt} to make",
        f"{buyer} how do I know what will sell",
        f"{channel} product idea validation before making".strip(),
        f"{buyer} product research before creating",
    ]
    symptom = [
        f"{channel} {fmt} no sales spent hours".strip(),
        f"{buyer} too many ideas don't know what to make",
        f"{fmt} no sales after making",
        f"{buyer} {pain_phrase}",
    ]
    questions = [
        f"how do {buyer}s decide what to sell",
        f"what do {buyer}s use to validate a {fmt} idea",
        f"best way to pick a {fmt} that sells",
    ]
    site_scoped = _site_queries(channel or "reddit.com",
                                f"{channel} {fmt} no sales".strip()) + [
        f"YouTube comments {channel} {fmt} no sales".strip(),
    ]
    return [
        QueryFamily("venting_pain", "Emotional, first-person frustration in the buyer's own words.",
                    2, _dedupe(venting),
                    ["reddit/forum complaint", "review with verbatim pain", "YouTube comment"]),
        QueryFamily("problem_first", "Problem-first phrasings, not solution/product names.",
                    2, _dedupe(problem_first),
                    ["forum question thread", "community post"]),
        QueryFamily("symptom_workaround", "How they describe the messy status quo today.",
                    2, _dedupe(symptom),
                    ["forum thread", "review", "comment"]),
        QueryFamily("buyer_questions", "Questions buyers actually type.",
                    2, _dedupe(questions),
                    ["Q&A thread", "forum question"]),
        QueryFamily("site_scoped", "Site-scoped digs into the highest-signal surfaces.",
                    2, _dedupe(site_scoped),
                    ["reddit.com thread", "channel community thread", "YouTube comment"]),
    ]


def _phase3_families(h: ProductHypothesis) -> List[QueryFamily]:
    fmt = _format_noun(h)
    channels = _channels(h) or [""]
    q: List[str] = []
    for ch in channels:
        base = f"{ch} {fmt}".strip()
        q += [f"{base} price", f"{base} pricing", f"how much {base} cost", f"{base} for sale"]
    return [
        QueryFamily("observed_prices", "Observed, listed prices of comparable products.",
                    3, _dedupe(q),
                    ["competitor listing with a visible price", "marketplace product page"]),
    ]


def _phase4_families(h: ProductHypothesis) -> List[QueryFamily]:
    fmt = _format_noun(h)
    buyer = _buyer_noun(h)
    channels = _channels(h) or [""]
    q: List[str] = []
    for ch in channels:
        base = f"{ch} {fmt}".strip()
        q += [f"{base} alternatives", f"best {base}", f"{base} vs", f"{base} review what it does"]
    q.append(f"{buyer} {fmt} comparison")
    return [
        QueryFamily("comparable_alternatives", "Structural competitors / comparable alternatives.",
                    4, _dedupe(q),
                    ["competitor product page", "comparison/teardown article", "review of features"]),
    ]


def _phase5_families(h: ProductHypothesis) -> List[QueryFamily]:
    fmt = _format_noun(h)
    mech = _keywords(h.missing_mechanism_hypothesis, limit=5)
    mech_phrase = " ".join(mech[:4]) or "missing feature"
    q = [
        f"{fmt} does not {mech_phrase}",
        f"limitations of {fmt}",
        f"{fmt} can't {mech_phrase}",
        f"what {fmt} are missing",
    ]
    return [
        QueryFamily("mechanism_gap", "Evidence comparing existing alternatives to the proposed mechanism.",
                    5, _dedupe(q),
                    ["competitor teardown showing absence of the mechanism",
                     "review naming the missing capability"]),
    ]


_PHASE_BUILDERS = {
    1: _phase1_families,
    2: _phase2_families,
    3: _phase3_families,
    4: _phase4_families,
    5: _phase5_families,
}


def _dedupe(items: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for it in items:
        norm = re.sub(r"\s+", " ", it).strip()
        if norm and norm.lower() not in seen:
            seen.add(norm.lower())
            out.append(norm)
    return out


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def build_query_families(hypothesis: ProductHypothesis, phase: int) -> List[QueryFamily]:
    """Return the diversified query families for one phase (1–5)."""
    builder = _PHASE_BUILDERS.get(phase)
    return builder(hypothesis) if builder else []


def build_search_plan(hypothesis: ProductHypothesis) -> dict:
    """Return the full, serializable search plan for all five phases.

    Note: `product_name` is echoed verbatim — never rewritten — to make the
    no-rename guarantee auditable.
    """
    phases = {}
    for phase in range(1, 6):
        families = build_query_families(hypothesis, phase)
        phases[f"phase_{phase}"] = [f.to_dict() for f in families]
    return {
        "product_name": hypothesis.product_name,  # unchanged, by contract
        "derived_subject": {
            "buyer_noun": _buyer_noun(hypothesis),
            "format_noun": _format_noun(hypothesis),
            "channels": _channels(hypothesis),
        },
        "note": (
            "Search guidance only — proposes diversified queries. It changes no "
            "gate, threshold, or verdict, and never invents sources/prices/quotes."
        ),
        "phases": phases,
    }


def prompt_appendix(hypothesis: ProductHypothesis, phase: int, max_per_family: int = 6) -> str:
    """A compact, model-facing appendix listing concrete query families to run.

    Appended to a phase's research prompt to diversify search angles. It does not
    instruct the model to lower any bar; it only widens the net.
    """
    families = build_query_families(hypothesis, phase)
    if not families:
        return ""
    lines = [
        "",
        "SUGGESTED SEARCH FAMILIES (run several across DIFFERENT angles; these are "
        "starting points derived from the hypothesis, not an exhaustive list — and "
        "they do NOT lower any evidence bar):",
    ]
    for fam in families:
        lines.append(f"- {fam.name} — {fam.intent}")
        for q in fam.queries[:max_per_family]:
            lines.append(f"    • {q}")
    lines.append(
        "Only report what you actually find, with real URLs. Finding nothing is a "
        "valid result. Never fabricate listings, prices, or quotes."
    )
    return "\n".join(lines)
