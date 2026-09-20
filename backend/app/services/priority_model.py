"""Unified Priority Model — Universal SEO Audit Engine spec (2026-09-20)
section 40: prioritize opportunities using a dynamic combination of
business relevance, search demand, current visibility, ranking
opportunity, search intent, commercial value, conversion potential,
technical severity, effort, and evidence confidence — search volume alone
must never determine priority.

This is an ADDITIVE, shared scoring utility, not a replacement for any
existing module's own internal sort order. Three modules already rank
their own rows with real, working, already-spec-compliant logic of their
own (keyword_cluster_pipeline._cluster_score, pptx_builder's tech-fixes
page-value scoring, Search Opportunities - Pages' real estimated-click-
opportunity number) — none of them determines priority by volume alone
either, and several existing tests pin their exact relative ordering.
Replacing that internal logic wholesale risks silently reordering rows on
an already-verified report for no functional gain. Instead, this module
gives every caller ONE comparable 0-100 score (with its full per-factor
breakdown, never a black box) it can attach alongside its own existing
ranking as an additional field for any consumer — API, cross-module
comparison, a future unified Recommendations view — that needs a single
number comparable across modules rather than each module's own
differently-scaled internal score."""

_FACTOR_WEIGHTS = {
    "business_relevance": 0.15,
    "search_demand": 0.09,
    "current_visibility": 0.07,
    "ranking_opportunity": 0.11,
    "intent_strength": 0.09,
    "commercial_value": 0.13,
    "conversion_potential": 0.07,
    "technical_severity": 0.11,
    "evidence_confidence": 0.08,
}
_EFFORT_WEIGHT = 0.10  # effort is a cost, inverted before weighting — see compute_priority_score
# _FACTOR_WEIGHTS + _EFFORT_WEIGHT sum to exactly 1.0, so a fully-evidenced
# opportunity (every factor at 1.0, effort at 0.0) scores exactly 100 —
# never above, never a mysterious ceiling.


def _clamp01(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def compute_priority_score(
    business_relevance: float = 0.0,
    search_demand: float = 0.0,
    current_visibility: float = 0.0,
    ranking_opportunity: float = 0.0,
    intent_strength: float = 0.0,
    commercial_value: float = 0.0,
    conversion_potential: float = 0.0,
    technical_severity: float = 0.0,
    effort: float = 0.5,
    evidence_confidence: float = 0.0,
) -> dict:
    """Every argument is a 0-1 evidence-normalized input the CALLER derives
    from its own real, already-computed data — this function never invents
    or looks anything up itself, purely a weighted combination. Any factor
    the caller has no real evidence for should be left at its neutral
    default (0.0, or 0.5 for `effort`) rather than guessed. `effort` is 0
    (trivial) to 1 (very high effort) and is inverted internally — more
    effort never increases priority. Returns {"score": 0-100, "factors":
    {...}} so the full breakdown is always visible, never a black box."""
    factors = {
        "business_relevance": _clamp01(business_relevance),
        "search_demand": _clamp01(search_demand),
        "current_visibility": _clamp01(current_visibility),
        "ranking_opportunity": _clamp01(ranking_opportunity),
        "intent_strength": _clamp01(intent_strength),
        "commercial_value": _clamp01(commercial_value),
        "conversion_potential": _clamp01(conversion_potential),
        "technical_severity": _clamp01(technical_severity),
        "evidence_confidence": _clamp01(evidence_confidence),
    }
    effort_clamped = _clamp01(effort)
    total = sum(factors[k] * w for k, w in _FACTOR_WEIGHTS.items())
    total += (1.0 - effort_clamped) * _EFFORT_WEIGHT
    return {"score": round(total * 100, 1), "factors": {**factors, "effort": effort_clamped}}


def evidence_confidence_to_score(label: str | None) -> float:
    """Maps the spec's own High/Medium/Low evidence_confidence vocabulary
    (section 41) onto the 0-1 scale compute_priority_score expects — never
    a re-judgment, just a unit conversion of a value already decided
    elsewhere."""
    return {"High": 1.0, "Medium": 0.5, "Low": 0.0}.get((label or "").strip(), 0.0)
