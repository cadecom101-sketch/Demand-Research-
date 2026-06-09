"""E1 demand-brief review layer.

This sits ON TOP of the existing audit + decision infrastructure. The decision
engine still produces a conservative score-tier (KILL/PARK/REVISE/TEST, with
BUILD disabled in this workflow); this module turns a completed run into a
*recording-readiness review* for the FIRST member under the primitive:

    Governed Solo-Operator Launch OS  (primitive — Andrew-authored)
    └── Base — Retail Instant-Download OS   (member validated here)

It evaluates nine deterministic E1 review gates, derives a review verdict
(E1_APPROVED_TO_RECORD / E1_REVISE_BEFORE_RECORDING / E1_PARK / E1_KILL), and
reports recording/B2/B3/public-execution status. It NEVER records into Revenue
OS, never marks B2 accepted, never unlocks B3, and never reviews Member A or B.

The gates are intentionally at least as strict as the conservative decision
gates — they can only block approval, never manufacture it.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from demand_research.audit.models import ClaimLedgerEntry, E1ReviewGate
from demand_research.models import (
    BASE_MEMBER_NAME,
    EXCLUDED_MEMBERS,
    MEMBER_A_NAME,
    MEMBER_B_NAME,
    PRIMITIVE_NAME,
    B2Status,
    B3Status,
    DemandBrief,
    EvidenceStage,
    ProductHypothesis,
    RecordingStatus,
    ReviewVerdict,
)

# Thresholds. These are recording-readiness bars; they are never lowered to make
# approval easier (e.g. buyer language requires 5 phrases, not the Phase 2 floor
# of 3; the price band still requires VERIFIED prices, never leads/directional).
MIN_OBSERVED_SOURCES = 3
MIN_BUYER_LANGUAGE_PHRASES = 5
MIN_VERIFIED_PRICES = 3
MIN_COMPETITORS = 3
MIN_FIT_MARKERS = 2

CURRENT_STATE = "E0_AUTHORED_CAPTURED"
CANDIDATE_STATE = "E1_CANDIDATE"

# --------------------------------------------------------------------------- #
# Scope-lock vocabulary
# --------------------------------------------------------------------------- #
# Markers that mean the evidence is actually about Member A (deferred — must NOT
# be validated here): dependency/version/integration/code-toolsmith operations.
_MEMBER_A_MARKERS = (
    "dependency", "dependencies", "api breakage", "api break", "breaking change",
    "version pin", "versioning", "environment maintenance", "integration risk",
    "code toolsmith", "toolsmith", "package update", "sdk update", "library upgrade",
)
# Markers that mean the evidence is actually about Member B (deferred — must NOT
# be validated here): client/capacity/scope/milestone/high-touch service ops.
_MEMBER_B_MARKERS = (
    "client intake", "scope control", "scope creep", "milestone signoff",
    "milestone sign-off", "capacity planning", "custom build margin",
    "high-touch service", "high touch service", "retainer", "statement of work",
    "client onboarding",
)
# Base/member anchors: retail instant-download / digital-product seller ops.
_BASE_MARKERS = (
    "instant download", "instant-download", "digital product", "digital-product",
    "etsy", "gumroad", "marketplace", "listing", "launch", "seller", "shop",
    "template", "planner", "demand", "fee", "margin", "review", "sales tracker",
    "checklist",
)
# Andrew-authored primitive mechanism anchors: evidence-before-motion, gating,
# authorship rationale, forced human decision, listing readiness, launch
# tracking, review cycles, ADVANCE/REVISE/KILL/PARK/EXPAND governance.
_PRIMITIVE_FIT_MARKERS = (
    "gate", "gates", "gating", "before launch", "before build", "before motion",
    "demand proof", "demand evidence", "evidence before", "listing readiness",
    "listing-readiness", "launch track", "launch tracker", "review cycle",
    "review loop", "post-launch", "post launch", "quality gate", "force", "forces",
    "advance", "revise", "kill", "park", "expand", "fee", "margin", "decision loop",
)
# Off-family drift markers (generic / agency / consulting / PM — not the member).
_GENERIC_DRIFT_MARKERS = (
    "agency crm", "client crm", "hourly consulting", "consulting tool",
    "local service", "project manager", "project management tool", "broad pm",
)
# Placeholder / fabricated URL fragments that must never appear in an accepted
# source. (The validator drops these upstream; this is a final integrity guard.)
_FABRICATION_URL_MARKERS = (
    "example.com", "example.org", "placeholder", "localhost", "test.test",
    "yoursite.", "fakeurl",
)


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
@dataclass
class E1ReviewResult:
    primitive_name: str
    target_member: str
    excluded_members: List[str]
    current_state: str
    candidate_state: str
    review_verdict: str
    recording_status: str
    b2_acceptance_status: str
    b3_status: str
    public_execution_status: str
    gates: List[E1ReviewGate] = field(default_factory=list)
    revenue_os_payload_draft: dict = field(default_factory=dict)

    @property
    def evidence_stage(self) -> EvidenceStage:
        if self.review_verdict == ReviewVerdict.E1_APPROVED_TO_RECORD.value:
            return EvidenceStage.E1_APPROVED_TO_RECORD
        return EvidenceStage.E0_AUTHORED_CAPTURED

    def to_artifact(self, run_id: str) -> dict:
        return {
            "run_id": run_id,
            "primitive_name": self.primitive_name,
            "target_member": self.target_member,
            "excluded_members": list(self.excluded_members),
            "current_state": self.current_state,
            "candidate_state": self.candidate_state,
            "review_verdict": self.review_verdict,
            "recording_status": self.recording_status,
            "b2_acceptance_status": self.b2_acceptance_status,
            "b3_status": self.b3_status,
            "public_execution_status": self.public_execution_status,
            "gates": [g.model_dump(mode="json") for g in self.gates],
        }

    def gate_passed(self, gate_id: str) -> bool:
        for g in self.gates:
            if g.gate_id == gate_id:
                return g.status == "PASS"
        return False

    def gate_source_ids(self, gate_id: str) -> List[str]:
        for g in self.gates:
            if g.gate_id == gate_id:
                return list(g.supporting_source_ids)
        return []

    def failing_gates(self) -> List[str]:
        return [g.gate_id for g in self.gates if g.status == "FAIL"]

    def status_block(self) -> List[tuple]:
        """Ordered (label, value) pairs for the markdown / CLI status block."""
        return [
            ("Primitive", self.primitive_name),
            ("Target Member", self.target_member),
            ("Excluded Members", ", ".join(self.excluded_members)),
            ("Current State", self.current_state),
            ("Candidate State", self.candidate_state),
            ("Review Verdict", self.review_verdict),
            ("Recording Status", self.recording_status),
            ("B2 Acceptance", self.b2_acceptance_status),
            ("B3 Status", self.b3_status),
            ("Public Execution Status", self.public_execution_status),
        ]


# --------------------------------------------------------------------------- #
# Member normalization
# --------------------------------------------------------------------------- #
def normalize_target_member(value: Optional[str]) -> tuple[str, str]:
    """Return (canonical_member_name, member_key) where member_key in {base,a,b,unknown}."""
    raw = (value or "").strip().lower()
    if raw in ("", "base", "base member", BASE_MEMBER_NAME.lower(),
               "retail instant-download os", "retail instant download os"):
        return BASE_MEMBER_NAME, "base"
    if raw in ("a", "member a", MEMBER_A_NAME.lower()) or raw.startswith("member a"):
        return MEMBER_A_NAME, "a"
    if raw in ("b", "member b", MEMBER_B_NAME.lower()) or raw.startswith("member b"):
        return MEMBER_B_NAME, "b"
    if "base" in raw or "instant" in raw:
        return BASE_MEMBER_NAME, "base"
    return value or BASE_MEMBER_NAME, "unknown"


def is_excluded_member(member_key: str) -> bool:
    return member_key in ("a", "b")


# --------------------------------------------------------------------------- #
# Haystack + helpers
# --------------------------------------------------------------------------- #
def _haystack(hypothesis: ProductHypothesis, brief: DemandBrief, missing_mechanism: dict) -> str:
    parts = [
        hypothesis.product_name, hypothesis.target_buyer, hypothesis.buyer_job,
        hypothesis.product_format, hypothesis.primary_channel,
        hypothesis.missing_mechanism_hypothesis,
    ]
    for s in brief.all_sources():
        parts += [
            s.source_name or "", str(s.url), s.what_this_proves or "",
            s.what_this_does_not_prove or "", s.gap_note or "",
            s.buyer_language_captured or "",
        ]
    mm = missing_mechanism or {}
    parts += [
        str(mm.get("gap_statement", "")), str(mm.get("missing_mechanism", "")),
        str(mm.get("current_competitor_pattern", "")), str(mm.get("why_it_matters", "")),
    ]
    return " ".join(parts).lower()


def _count_markers(haystack: str, markers: tuple) -> int:
    return sum(1 for m in markers if m in haystack)


def _ids_for_phase(graded: List[dict], phase: int, grade: Optional[str] = None) -> List[str]:
    out = []
    for g in graded:
        if g.get("phase") != phase:
            continue
        if grade is not None and g.get("grade") != grade:
            continue
        out.append(g["source_id"])
    return out


# --------------------------------------------------------------------------- #
# The nine gates
# --------------------------------------------------------------------------- #
def evaluate_e1_review(
    *,
    hypothesis: ProductHypothesis,
    brief: DemandBrief,
    signals: dict,
    graded: List[dict],
    price_bands: List[dict],
    directional: List[dict],
    competitors: List[dict],
    missing_mechanism: dict,
    target_member: Optional[str] = "Base",
    decision_cleared_test: bool = True,
) -> E1ReviewResult:
    """Run the nine E1 review gates and derive the verdict + recording status.

    `decision_cleared_test` couples approval to the conservative engine: the
    review may only APPROVE for recording when the existing decision engine also
    cleared its TEST bar. This guarantees E1 approval is never *easier* than the
    conservative TEST gate (the nine gates can only make it harder)."""
    member_name, member_key = normalize_target_member(target_member)
    excluded = is_excluded_member(member_key)
    hay = _haystack(hypothesis, brief, missing_mechanism)

    all_ids = [g["source_id"] for g in graded]
    p1_ids = _ids_for_phase(graded, 1)
    blang_ids = _ids_for_phase(graded, 2, grade="B")
    price_ids = [r["source_id"] for r in price_bands if r.get("source_id")]
    comp_ids = [r["source_id"] for r in competitors if r.get("source_id")]

    n_sources = len(all_ids)
    n_buyer_phrases = int(signals.get("buyer_language_artifact_count", 0))
    n_verified_prices = len(price_bands)
    n_competitors = len(competitors)
    phase1_pass = bool(signals.get("phase1_pass", False))
    category_signals = int(signals.get("category_signal_count", 0))
    mm_status = (missing_mechanism or {}).get("status", "unsupported")
    mm_structural = bool((missing_mechanism or {}).get("is_structural", False))

    member_a_hits = _count_markers(hay, _MEMBER_A_MARKERS)
    member_b_hits = _count_markers(hay, _MEMBER_B_MARKERS)
    base_hits = _count_markers(hay, _BASE_MARKERS)
    fit_hits = _count_markers(hay, _PRIMITIVE_FIT_MARKERS)
    generic_drift = _count_markers(hay, _GENERIC_DRIFT_MARKERS) > 0

    gates: List[E1ReviewGate] = []

    def add(gate_id: str, passed: bool, reason: str, ids: List[str]) -> E1ReviewGate:
        g = E1ReviewGate(
            gate_id=gate_id, status="PASS" if passed else "FAIL",
            reason=reason, supporting_source_ids=list(ids),
        )
        gates.append(g)
        return g

    # Gate 1 — scope_lock -------------------------------------------------- #
    a_drift = member_a_hits >= 2 and base_hits == 0
    b_drift = member_b_hits >= 2 and base_hits == 0
    off_family = base_hits == 0 and fit_hits == 0
    if excluded:
        g_scope = add("scope_lock", False,
                      f"Target member is {member_name}, not {BASE_MEMBER_NAME}. "
                      "This repo validates the Base member only; Member A/B are deferred at E0.",
                      [])
    elif member_key == "unknown":
        g_scope = add("scope_lock", False,
                      f"Target member '{target_member}' did not resolve to the Base member "
                      f"under {PRIMITIVE_NAME}.", [])
    elif not (hypothesis.target_buyer or "").strip():
        g_scope = add("scope_lock", False, "Target buyer is not specified.", [])
    elif a_drift:
        g_scope = add("scope_lock", False,
                      "Evidence reads as Member A (dependency/version/integration toolsmith) "
                      "operations, not Base retail instant-download seller operations.", all_ids)
    elif b_drift:
        g_scope = add("scope_lock", False,
                      "Evidence reads as Member B (client/capacity/scope/high-touch service) "
                      "operations, not Base retail instant-download seller operations.", all_ids)
    elif generic_drift or off_family:
        g_scope = add("scope_lock", False,
                      "Brief drifts off-family (generic Notion template / agency CRM / "
                      "consulting / project manager) with no Base launch/listing/tracking "
                      "mechanism.", all_ids)
    else:
        g_scope = add("scope_lock", True,
                      f"Locked to {BASE_MEMBER_NAME} under {PRIMITIVE_NAME}; specific buyer; "
                      "gap maps to the Base demand→build→quality→listing→launch→sales pipeline.",
                      p1_ids + comp_ids)

    # Gate 2 — minimum_real_observed_evidence ----------------------------- #
    g_min = add("minimum_real_observed_evidence", n_sources >= MIN_OBSERVED_SOURCES,
                f"{n_sources} validated observed source(s) (need {MIN_OBSERVED_SOURCES}).",
                all_ids)

    # Gate 3 — demand_signal_exists --------------------------------------- #
    demand_ok = phase1_pass and category_signals >= 1
    g_demand = add("demand_signal_exists", demand_ok,
                   (f"{category_signals} external market signal(s) show the niche/problem is "
                    "visible (someone is searching/selling/buying related)."
                    if demand_ok else
                    "No external demand signal: no one is observably expressing/searching/"
                    "buying/selling comparable products for this buyer."),
                   p1_ids)

    # Gate 4 — buyer_language_captured ------------------------------------ #
    g_buyer = add("buyer_language_captured", n_buyer_phrases >= MIN_BUYER_LANGUAGE_PHRASES,
                  (f"{n_buyer_phrases} verbatim buyer-language phrase(s) captured "
                   f"(need {MIN_BUYER_LANGUAGE_PHRASES}); the market has its own words for the pain."
                   if n_buyer_phrases >= MIN_BUYER_LANGUAGE_PHRASES else
                   f"only {n_buyer_phrases} verbatim buyer-language phrase(s) "
                   f"(need {MIN_BUYER_LANGUAGE_PHRASES}); cannot show the market names this pain."),
                  blang_ids)

    # Gate 5 — observed_price_band (verified prices only) ----------------- #
    g_price = add("observed_price_band", n_verified_prices >= MIN_VERIFIED_PRICES,
                  (f"{n_verified_prices} verified observed competitor price(s) map a commercial "
                   f"frame (need {MIN_VERIFIED_PRICES})."
                   if n_verified_prices >= MIN_VERIFIED_PRICES else
                   f"only {n_verified_prices} verified observed price(s) (need {MIN_VERIFIED_PRICES}); "
                   f"{len(directional)} directional article(s) and unpriced leads do NOT satisfy "
                   "the price band."),
                  price_ids)

    # Gate 6 — competitor_presence ---------------------------------------- #
    g_comp = add("competitor_presence", n_competitors >= MIN_COMPETITORS,
                 (f"{n_competitors} comparable competitor(s) mapped — the niche is not imaginary "
                  f"(need {MIN_COMPETITORS})."
                  if n_competitors >= MIN_COMPETITORS else
                  f"only {n_competitors} comparable competitor(s) (need {MIN_COMPETITORS})."),
                 comp_ids)

    # Gate 7 — specific_missing_mechanism_gap ----------------------------- #
    gap_ok = mm_structural and mm_status in ("supported", "partially_supported")
    g_gap = add("specific_missing_mechanism_gap", gap_ok,
                (f"A structural missing-mechanism gap is named (status={mm_status}); it cannot be "
                 "closed by relabeling, color, title, icon, or page-count changes."
                 if gap_ok else
                 f"No structural gap (status={mm_status}, structural={mm_structural}); gap is "
                 "absent, aesthetic, or a reskin."),
                comp_ids)

    # Gate 8 — fit_to_andrew_authored_primitive --------------------------- #
    fit_ok = (not excluded) and fit_hits >= MIN_FIT_MARKERS and base_hits >= 1
    g_fit = add("fit_to_andrew_authored_primitive", fit_ok,
                (f"Evidence connects to the authored primitive (evidence-before-motion, state "
                 f"gates, listing readiness, launch tracking, review loop): {fit_hits} fit anchor(s)."
                 if fit_ok else
                 "Evidence justifies only a generic planner/dashboard; it does not need demand "
                 "gates, listing readiness, launch tracking, or review cycles."),
                blang_ids + comp_ids)

    # Gate 9 — no_fabrication --------------------------------------------- #
    fabricated = [
        s for s in brief.all_sources()
        if any(m in str(s.url).lower() for m in _FABRICATION_URL_MARKERS)
    ]
    run_ok = (brief.run_status == "success") or (brief.run_status == "partial")
    fab_ok = not fabricated and run_ok
    g_fab = add("no_fabrication", fab_ok,
                ("No fabricated/placeholder sources, invented quotes, fake statistics, or public "
                 "execution; all accepted sources are real/validated."
                 if fab_ok else
                 (f"{len(fabricated)} accepted source(s) use placeholder/fabricated URLs."
                  if fabricated else
                  f"Run did not complete cleanly (status={brief.run_status}); cannot certify "
                  "evidence integrity.")),
                [])

    # ----------------------------------------------------------------- #
    # Verdict (deterministic precedence — only blocks, never manufactures)
    # ----------------------------------------------------------------- #
    def failed(g: E1ReviewGate) -> bool:
        return g.status == "FAIL"

    if failed(g_fab):
        verdict = ReviewVerdict.E1_KILL
    elif failed(g_scope):
        verdict = ReviewVerdict.E1_REVISE_BEFORE_RECORDING
    elif failed(g_min):
        verdict = ReviewVerdict.E1_PARK
    elif failed(g_demand):
        verdict = ReviewVerdict.E1_PARK
    elif failed(g_buyer):
        verdict = ReviewVerdict.E1_REVISE_BEFORE_RECORDING
    elif failed(g_price):
        verdict = ReviewVerdict.E1_REVISE_BEFORE_RECORDING
    elif failed(g_comp):
        verdict = ReviewVerdict.E1_REVISE_BEFORE_RECORDING
    elif failed(g_gap):
        verdict = ReviewVerdict.E1_REVISE_BEFORE_RECORDING
    elif failed(g_fit):
        verdict = ReviewVerdict.E1_REVISE_BEFORE_RECORDING
    else:
        verdict = ReviewVerdict.E1_APPROVED_TO_RECORD

    # Conservative coupling: never approve recording above the engine's TEST bar.
    if verdict == ReviewVerdict.E1_APPROVED_TO_RECORD and not decision_cleared_test:
        verdict = ReviewVerdict.E1_REVISE_BEFORE_RECORDING

    approved = verdict == ReviewVerdict.E1_APPROVED_TO_RECORD
    recording = RecordingStatus.READY_TO_RECORD if approved else RecordingStatus.NOT_RECORDED
    b2 = B2Status.READY_FOR_ACCEPTANCE if approved else B2Status.NOT_MET

    result = E1ReviewResult(
        primitive_name=PRIMITIVE_NAME,
        target_member=member_name,
        excluded_members=list(EXCLUDED_MEMBERS),
        current_state=CURRENT_STATE,
        candidate_state=CANDIDATE_STATE,
        review_verdict=verdict.value,
        recording_status=recording.value,
        b2_acceptance_status=b2.value,
        b3_status=B3Status.LOCKED.value,        # repo never unlocks B3
        public_execution_status="NONE",         # repo never publishes/tests/executes
        gates=gates,
        revenue_os_payload_draft=build_revenue_os_payload_draft(verdict),
    )
    return result


# --------------------------------------------------------------------------- #
# Narrative helpers (recording-oriented; never recommend public execution)
# --------------------------------------------------------------------------- #
def next_step_for(result: E1ReviewResult) -> str:
    """The next step — framed around recording readiness, NOT public execution.

    The repo never recommends publishing, listing, advertising, contacting
    sellers/customers, or building. It recommends human review and (if approved)
    external recording into Revenue OS."""
    v = result.review_verdict
    if v == ReviewVerdict.E1_APPROVED_TO_RECORD.value:
        return (
            "Human review, then record the Base demand brief into Revenue OS "
            "(authorship_primitives + demand_briefs rows). Recording is the separate, "
            "external act that satisfies B2; this repo does not write it. B3 stays LOCKED."
        )
    if v == ReviewVerdict.E1_REVISE_BEFORE_RECORDING.value:
        failing = ", ".join(result.failing_gates()) or "scope/fit"
        return (
            f"Revise before recording: address the failing E1 gate(s) ({failing}), then "
            "re-run the Base E1 demand brief. Do not record yet."
        )
    if v == ReviewVerdict.E1_PARK.value:
        return (
            "Park: insufficient documented desk evidence to become an E1 candidate. Re-run "
            "later with more real observed sources / a clearer external demand signal."
        )
    return (
        "Kill: do not record. Evidence integrity failed (fabrication/incomplete run); "
        "restart with real, traceable, observed sources only."
    )


def what_would_change_for(result: E1ReviewResult) -> str:
    """What would change the verdict — in E1 review terms."""
    if result.review_verdict == ReviewVerdict.E1_APPROVED_TO_RECORD.value:
        return (
            "A scope-lock or fit regression, weaker/again-unverified prices, fewer buyer-language "
            "phrases, or any fabrication would lower the verdict below E1_APPROVED_TO_RECORD."
        )
    failing = result.failing_gates()
    if failing:
        return (
            "Clearing the failing E1 gate(s) would raise the verdict toward "
            f"E1_APPROVED_TO_RECORD: {', '.join(failing)}."
        )
    return "Stronger, member-aligned documented desk evidence would raise the verdict."


# --------------------------------------------------------------------------- #
# Revenue OS recording payload — DRAFT ONLY, never written from this repo
# --------------------------------------------------------------------------- #
REVENUE_OS_DRAFT_MARKERS = [
    "DRAFT ONLY",
    "NOT RECORDED",
    "DO NOT WRITE TO REVENUE OS FROM THIS REPO",
    "HUMAN REVIEW REQUIRED",
]


def build_revenue_os_payload_draft(verdict: ReviewVerdict) -> dict:
    """Draft the two Revenue OS rows. This is a DRAFT only; the repo never writes
    to Revenue OS — recording is the separate, external act that satisfies B2."""
    return {
        "_marking": list(REVENUE_OS_DRAFT_MARKERS),
        "authorship_primitives": {
            "name": PRIMITIVE_NAME,
            "status": "active",
            "evidence_level": "E1_PENDING_RECORDING",
            "recording_status": "DRAFT_ONLY_NOT_RECORDED",
        },
        "demand_briefs": {
            "primitive_name": PRIMITIVE_NAME,
            "member_name": BASE_MEMBER_NAME,
            "brief_status": "E1_CANDIDATE_APPROVED_TO_RECORD_OR_REVISE",
            "recording_status": "DRAFT_ONLY_NOT_RECORDED",
            "review_verdict": verdict.value,
        },
    }


# --------------------------------------------------------------------------- #
# E1 review / boundary claims (claims 6–9 of the ledger)
# --------------------------------------------------------------------------- #
def build_e1_claims(
    run_id: str,
    result: E1ReviewResult,
    *,
    start_index: int,
    fit_gate_passed: bool,
    scope_gate_passed: bool,
    fit_source_ids: List[str],
) -> List[ClaimLedgerEntry]:
    """Append the E1-specific claims (fit + workflow-boundary) to the ledger."""
    claims: List[ClaimLedgerEntry] = []
    n = start_index

    # 6 — evidence fits the authored primitive and the Base member.
    n += 1
    claims.append(ClaimLedgerEntry(
        claim_id=f"C{n:03d}", run_id=run_id,
        claim=f"The demand evidence fits {PRIMITIVE_NAME} and justifies {BASE_MEMBER_NAME} specifically.",
        claim_type="fit_to_primitive", required_evidence_grade="C",
        supporting_source_ids=list(fit_source_ids) if (fit_gate_passed and scope_gate_passed) else [],
        status="supported" if (fit_gate_passed and scope_gate_passed) else "unsupported",
        confidence=0.7 if (fit_gate_passed and scope_gate_passed) else 0.2,
        reason=("Evidence connects to evidence-before-motion, state gates, listing readiness, "
                "launch tracking, and review loops for the Base member."
                if (fit_gate_passed and scope_gate_passed) else
                "Evidence does not require the authored primitive's mechanisms, or scope lock failed."),
    ))

    # 7 — nothing in the evidence validates Member A or Member B (boundary).
    n += 1
    claims.append(ClaimLedgerEntry(
        claim_id=f"C{n:03d}", run_id=run_id,
        claim=f"Nothing in this evidence validates {MEMBER_A_NAME} or {MEMBER_B_NAME}.",
        claim_type="scope_boundary", required_evidence_grade="C",
        supporting_source_ids=[],
        status="supported" if scope_gate_passed else "unsupported",
        confidence=0.6 if scope_gate_passed else 0.2,
        reason=("Scope lock held: the evidence is about Base retail instant-download seller "
                "operations, not Member A/B operations."
                if scope_gate_passed else
                "Scope lock failed: evidence may belong to a different member; review required."),
    ))

    # 8 — nothing has been recorded yet (workflow-boundary claim).
    n += 1
    claims.append(ClaimLedgerEntry(
        claim_id=f"C{n:03d}", run_id=run_id,
        claim="Nothing has been recorded into Revenue OS; B2 is not accepted and B3 is locked.",
        claim_type="workflow_boundary", required_evidence_grade="C",
        supporting_source_ids=[], status="supported", confidence=1.0,
        reason=(f"recording_status={result.recording_status}, b2={result.b2_acceptance_status}, "
                f"b3={result.b3_status}. Recording is external (Revenue OS) and out of repo scope."),
    ))

    # 9 — nothing has been published / tested publicly / built (boundary claim).
    n += 1
    claims.append(ClaimLedgerEntry(
        claim_id=f"C{n:03d}", run_id=run_id,
        claim="Nothing has been published, publicly tested, listed, advertised, or built.",
        claim_type="workflow_boundary", required_evidence_grade="C",
        supporting_source_ids=[], status="supported", confidence=1.0,
        reason=f"public_execution_status={result.public_execution_status}; this repo is desk research only.",
    ))

    return claims
