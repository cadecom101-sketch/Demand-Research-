"""Build the claim ledger from phase results and graded sources.

Every meaningful claim a brief would make is represented as one ledger record,
with the source IDs that support it and the evidence grade that claim *requires*.
A claim can only be marked "supported" when real source IDs back it at the
required grade — this is what stops the brief from stating conclusions it cannot
trace.
"""

from typing import List, Optional

from demand_research.audit.models import ClaimLedgerEntry
from demand_research.audit.grading import GRADE_RANK, best_grade, grade_meets
from demand_research.models import DemandBrief, PhaseStatus

# A graded source as seen by the claim builder.
GradedSource = dict  # {"source_id", "phase", "grade", "evidence_type"}


def _status_for(supporting: List[str], best: Optional[str], required: str, min_count: int) -> str:
    if not supporting or not grade_meets(best, required):
        return "unsupported"
    if len(supporting) >= min_count:
        return "supported"
    return "partially_supported"


def _confidence_for(status: str) -> float:
    return {"supported": 0.8, "partially_supported": 0.5, "unsupported": 0.2}.get(status, 0.2)


def build_claims(
    run_id: str,
    brief: DemandBrief,
    graded: List[GradedSource],
    *,
    min_count: int = 3,
    price_artifacts: Optional[List[dict]] = None,
    price_lead_count: int = 0,
    directional: Optional[List[dict]] = None,
) -> List[ClaimLedgerEntry]:
    """Construct the claim ledger for a brief.

    Buyer-pain claims require Grade B+, category claims accept Grade C, and the
    mechanism-gap claim requires an actual competitor comparison (Grade C
    competitor sources plus a passed Phase 5), never mere absence of evidence.
    """
    claims: List[ClaimLedgerEntry] = []
    n = 0

    def ids_for(phase: int, grade: Optional[str] = None) -> List[str]:
        out = []
        for g in graded:
            if g["phase"] != phase:
                continue
            if grade is not None and g["grade"] != grade:
                continue
            out.append(g["source_id"])
        return out

    def grades_for(phase: int) -> List[str]:
        return [g["grade"] for g in graded if g["phase"] == phase]

    hyp = brief.product_hypothesis

    # 1. Category existence — Grade C is acceptable.
    if brief.phase_1_result is not None:
        n += 1
        ids = ids_for(1)
        status = _status_for(ids, best_grade(grades_for(1)), "C", min_count)
        claims.append(ClaimLedgerEntry(
            claim_id=f"C{n:03d}", run_id=run_id,
            claim=f"A real market/category exists for: {hyp.product_name} ({hyp.product_format}).",
            claim_type="category_existence", required_evidence_grade="C",
            supporting_source_ids=ids, status=status, confidence=_confidence_for(status),
            reason=f"{len(ids)} category/market signals collected (need {min_count} at Grade C).",
        ))

    # 2. Buyer pain — requires Grade B (direct buyer language).
    if brief.phase_2_result is not None:
        n += 1
        b_ids = ids_for(2, grade="B")
        all_grades = grades_for(2)
        status = _status_for(b_ids, best_grade(all_grades), "B", min_count)
        claims.append(ClaimLedgerEntry(
            claim_id=f"C{n:03d}", run_id=run_id,
            claim=f"{hyp.target_buyer} experience real, articulated pain around: {hyp.buyer_job}.",
            claim_type="buyer_pain", required_evidence_grade="B",
            supporting_source_ids=b_ids, status=status, confidence=_confidence_for(status),
            reason=(
                f"{len(b_ids)} verbatim buyer-language artifacts (Grade B); "
                f"need {min_count}. Grade-C/D content cannot satisfy this claim."
            ),
        ))

    # 3. Channel viability / price band — supported ONLY by verified price
    # artifacts (specific competitor URL + observed price). Generic Phase 3
    # competitor leads and general market-pricing articles never satisfy it.
    if brief.phase_3_result is not None:
        n += 1
        verified = [
            r for r in (price_artifacts or [])
            if r.get("url") and isinstance(r.get("price_observed"), (int, float))
            and not r.get("directional")
        ]
        supporting = [r["source_id"] for r in verified if r.get("source_id")]
        n_verified = len(verified)
        if n_verified >= min_count:
            status = "supported"
        elif n_verified >= 1:
            status = "partially_supported"
        else:
            status = "unsupported"
        if n_verified == 0 and price_lead_count > 0:
            reason = (
                f"{price_lead_count} competitor leads were found, but 0 verified "
                "competitor prices were captured. Competitor leads do not satisfy "
                "the price-band requirement."
            )
        elif n_verified == 0:
            reason = "No verified competitor prices were captured."
        else:
            reason = (
                f"{n_verified} verified competitor prices captured (need {min_count}); "
                f"{price_lead_count} unpriced competitor leads excluded."
            )
        claims.append(ClaimLedgerEntry(
            claim_id=f"C{n:03d}", run_id=run_id,
            claim=f"A workable price band exists on {hyp.primary_channel} for this product.",
            claim_type="channel_viability", required_evidence_grade="C",
            supporting_source_ids=supporting, status=status, confidence=_confidence_for(status),
            reason=reason,
        ))
        # Weaker, separate directional-context claim — never the price-band claim.
        if directional:
            n += 1
            dids = [r.get("source_id") for r in directional if r.get("source_id")]
            claims.append(ClaimLedgerEntry(
                claim_id=f"C{n:03d}", run_id=run_id,
                claim="Directional market pricing context exists.",
                claim_type="channel_viability", required_evidence_grade="D",
                supporting_source_ids=dids, status="partially_supported", confidence=0.4,
                reason=(
                    f"{len(directional)} general market-pricing article(s) provide directional "
                    "context only; they do not establish a competitor price band."
                ),
            ))

    # 4. Competitor density — Grade C.
    if brief.phase_4_result is not None:
        n += 1
        ids = ids_for(4)
        status = _status_for(ids, best_grade(grades_for(4)), "C", min_count)
        claims.append(ClaimLedgerEntry(
            claim_id=f"C{n:03d}", run_id=run_id,
            claim="Enough real competitors exist to assess structural positioning.",
            claim_type="competitor_density", required_evidence_grade="C",
            supporting_source_ids=ids, status=status, confidence=_confidence_for(status),
            reason=f"{len(ids)} competitors analysed structurally (need {min_count}).",
        ))

    # 5. Mechanism gap — requires competitor comparison, not absence of evidence.
    if brief.phase_5_result is not None:
        n += 1
        competitor_ids = ids_for(4)
        p5_pass = brief.phase_5_result.status == PhaseStatus.PASS
        if p5_pass and competitor_ids:
            status = "supported"
        elif competitor_ids:
            status = "partially_supported"
        else:
            status = "unsupported"
        claims.append(ClaimLedgerEntry(
            claim_id=f"C{n:03d}", run_id=run_id,
            claim=f"The proposed mechanism is a STRUCTURAL gap competitors do not close: "
                  f"{hyp.missing_mechanism_hypothesis}",
            claim_type="mechanism_gap", required_evidence_grade="C",
            supporting_source_ids=competitor_ids, status=status, confidence=_confidence_for(status),
            reason=(
                "Gap is judged against observed competitor structures, not absence of evidence. "
                f"Phase 5 {'passed' if p5_pass else 'did not pass'}; "
                f"{len(competitor_ids)} competitor structures available for comparison."
            ),
        ))

    return claims


# Re-export for callers that want the rank table.
__all__ = ["build_claims", "ClaimLedgerEntry", "GRADE_RANK"]
