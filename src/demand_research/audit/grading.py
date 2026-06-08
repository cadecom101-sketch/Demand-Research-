"""Deterministic evidence grading and run-signal computation.

This is the single source of truth for how a source is graded (A/B/C/D) and how
the high-level run signals (category-signal count, buyer-language artifact
count, behavioral-intent count, grade distribution) are derived. Both the
source ledger and the hard decision gates use these functions so a brief and
its audit trail can never disagree about a source's grade.
"""

from collections import Counter
from typing import Optional

from demand_research.models import PhaseResult, PhaseStatus, SourceCard

# Grade ordering. A is the strongest (behavioral / purchase intent); D the weakest.
GRADE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}

# Default evidence type produced by each phase.
PHASE_EVIDENCE_TYPE = {
    1: "market_signal",
    2: "buyer_language",
    3: "competitor",
    4: "competitor",
    5: "competitor",
}


def grade_source(
    evidence_type: str,
    *,
    is_direct_quote: Optional[bool],
    buyer_language: Optional[str],
    price: Optional[float],
) -> tuple[str, float]:
    """Grade a single source deterministically.

    - Grade A: behavioral / purchase-intent evidence (not auto-detectable from a
      listing; reserved for explicit behavioral signals).
    - Grade B: direct, verbatim buyer-language evidence.
    - Grade C: category / competitor / content evidence (listings, pages).
    - Grade D: weak / inferred / paraphrased evidence.
    """
    if evidence_type == "buyer_language":
        if is_direct_quote and buyer_language:
            return "B", 0.7
        if buyer_language:
            # Paraphrase / composite is explicitly weaker — it cannot satisfy a
            # buyer-language requirement on its own.
            return "D", 0.4
        return "C", 0.5
    if evidence_type == "purchase_intent":
        return "A", 0.85
    if evidence_type in ("market_signal", "competitor", "channel_signal"):
        return "C", 0.55
    return "C", 0.5


def grade_card(evidence_type: str, card: SourceCard) -> tuple[str, float]:
    """Grade a SourceCard using its quote/price fields."""
    return grade_source(
        evidence_type,
        is_direct_quote=card.is_direct_quote,
        buyer_language=card.buyer_language_captured,
        price=card.price_observed,
    )


def best_grade(grades: list[str]) -> Optional[str]:
    """Return the strongest grade in a list, or None if empty."""
    if not grades:
        return None
    return max(grades, key=lambda g: GRADE_RANK.get(g, 0))


def grade_meets(actual: Optional[str], required: str) -> bool:
    """True if `actual` is at least as strong as `required`."""
    if actual is None:
        return False
    return GRADE_RANK.get(actual, 0) >= GRADE_RANK.get(required, 0)


def compute_signals(phase_by_num: dict[int, PhaseResult], run_status: str) -> dict:
    """Derive the high-level run signals the hard gates need.

    Returns a dict with deterministic, auditable counts.
    """
    p1 = phase_by_num.get(1)
    p2 = phase_by_num.get(2)

    category_signal_count = len(p1.sources_collected) if p1 else 0

    buyer_language_artifact_count = 0
    if p2:
        for s in p2.sources_collected:
            if s.is_direct_quote and s.buyer_language_captured:
                buyer_language_artifact_count += 1

    grade_counts: Counter = Counter()
    behavioral_intent_count = 0
    for num, phase in phase_by_num.items():
        if phase is None:
            continue
        evidence_type = PHASE_EVIDENCE_TYPE.get(num, "other")
        for s in phase.sources_collected:
            grade, _ = grade_card(evidence_type, s)
            grade_counts[grade] += 1
            if grade == "A":
                behavioral_intent_count += 1

    phase2_present = p2 is not None
    phase2_failed = phase2_present and p2.status == PhaseStatus.FAIL

    return {
        "category_signal_count": category_signal_count,
        "buyer_language_artifact_count": buyer_language_artifact_count,
        "behavioral_intent_count": behavioral_intent_count,
        "grade_counts": dict(grade_counts),
        "run_status": run_status,
        "phase2_present": phase2_present,
        "phase2_failed": phase2_failed,
    }


def classify_pain_type(text: str) -> str:
    """Best-effort, deterministic pain-type tag for a buyer-language artifact."""
    low = (text or "").lower()
    if any(k in low for k in ("wasted", "spent hours", "spent time", "build time", "hours making")):
        return "wasted_build_time"
    if any(k in low for k in ("no sales", "no one buys", "zero sales", "not selling")):
        return "no_sales"
    if any(k in low for k in ("too many ideas", "which idea", "what to build", "what to make")):
        return "too_many_ideas"
    if any(k in low for k in ("validate", "validation", "is there demand", "will it sell")):
        return "validation_uncertainty"
    if any(k in low for k in ("overwhelm", "decision", "can't decide", "paralyzed", "stuck")):
        return "decision_fatigue"
    return "other"
