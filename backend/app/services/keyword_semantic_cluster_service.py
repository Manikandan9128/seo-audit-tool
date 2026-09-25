"""External lead's Phase 2/3 keyword-clustering spec (2026-09-21): two
SEPARATE, sequential LLM calls — semantic candidate grouping, then
validate/split/merge/finalize — replacing this pipeline's earlier
single-call bucket-sub-split design (keyword_cluster_service.py's
generate_batched_candidate_clusters, still used as a fallback nowhere else
now but left in place since nothing else references removing it).

Existing-page matching (the spec's own Phase 3 Step 6) is deliberately NOT
done by an LLM call here — keyword_relevance_service.match_existing_page_for_
cluster already does this deterministically (word-overlap, no AI, already
tested) and keyword_cluster_pipeline.py still calls it AFTER these two
phases finish, exactly as before. Asking an LLM to pick the right page out
of a client's full crawled-page list is strictly worse (larger prompt,
non-deterministic, another AI-quota dependency) than a free, deterministic
check this codebase already has and trusts — so Phase 3's own `match_
strength`/`recommended_action` output fields are populated from that
existing deterministic step's result, not invented by this module. This
module only ever owns semantic membership, splitting/merging, naming, and
primary-keyword selection (spec Phase 3 Steps 1-5).

Same "one retry on a fresh sample, then fail open" discipline as
keyword_relevance_service.classify_keywords and keyword_cluster_service's
own AI calls — a keyword's own business_theme/intent/page_category bucket
(already computed upstream) is passed through as `source_cluster`, per the
spec's explicit "prior evidence only — never treat it as the answer" —
used here only as advisory prompt context, never trusted directly."""

import json
import logging
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, iter_text_attempts

logger = logging.getLogger(__name__)


_PHASE2_PROMPT_TEMPLATE = """You are grouping keywords into candidate SEO topic clusters based on \
semantic meaning — not shared words, not search volume, not any pre-existing cluster label.

## Input

keywords: {keywords_json}
// [{{keyword, search_volume, keyword_difficulty, source_cluster, variants, group_size}}, ...]
// source_cluster is prior evidence only — never treat it as the answer.
// Each keyword already stands for a small pre-grouped set of close variants (same core entity, same
// intent family — plural/word-order/price-modifier variants). `variants` lists a few of them and
// `group_size` how many there are. Decide for the keyword as a whole; its variants follow it.

client_context: {client_context}

## Step 1 — Identify each keyword's main entity and semantic topic
For each keyword, state what it is actually about (main entity + specific topic), not the \
broadest category it could belong to. Example: "6x4" -> entity: truck, topic: 6x4 truck \
configuration — never generalize this to "Heavy Trucks."

## Step 2 — Separate core topic from modifier
Identify whether each keyword is a core topic or a topic + a modifier (pricing, guide, basics, \
calculator, etc.). A modifier does NOT create a new cluster by default.

## Step 3 — Group into candidate clusters by semantic topic + intent
Group keywords sharing the same entity, semantic topic, underlying user need (what the searcher is \
trying to accomplish), audience, and compatible search intent — i.e. keywords ONE page could genuinely \
satisfy. Do NOT group keywords just because they share words or a parent category ("tipper truck" and \
"mining truck" are different products, not one "Trucks" page). Do NOT let a high-volume keyword pull \
unrelated keywords into its group. Informational ("how/what/guide") and commercial/transactional \
("price/buy/software") searches for the same entity usually need different pages.

## Step 4 — Apply the modifier-to-cluster test
A modifier becomes its own cluster ONLY if it represents a genuinely distinct topic, intent, page \
type, or user need from its core topic — never because it has high volume alone.

## Step 5 — Output
Return each keyword mapped to a candidate_cluster_id, plus for each candidate cluster: \
main_entity, semantic_topic, and a one-line justification for why these keywords share it.

Hard rules:
- Only output mappings for keywords literally present in the input — never invent a keyword, \
never drop one silently (unmapped keywords must be explicitly listed under "unmapped").
- Never let source_cluster values pass through unchanged without this grouping logic actually \
running — every keyword must go through Steps 1-4, even if it already had a source_cluster.
- Never name a cluster in this step — naming happens later, after validation.

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "clusters": {{
    "<candidate_cluster_id>": {{"main_entity": string, "semantic_topic": string, "justification": string, "keywords": [string, ...]}}
  }},
  "unmapped": [string, ...]
}}
Every keyword from the input must appear exactly once, either inside one cluster's "keywords" \
list or in "unmapped" — using its exact original text.
"""


_PHASE3_PROMPT_TEMPLATE = """You are validating candidate keyword clusters before they become \
final. Apply every check below; split, merge, or flag "Needs Review" as needed — never force a \
keyword into a cluster it doesn't cleanly belong to.

## Input

candidate_clusters: {candidate_clusters_json}
// {{candidate_cluster_id: {{main_entity, semantic_topic, justification, keywords: [{{keyword, search_volume, keyword_difficulty, intent}}, ...]}}}}

## Step 1 — One-page satisfaction test
For each candidate cluster: would ONE well-optimized page genuinely satisfy the dominant intent, \
need, entity, and topic of every keyword in it? If no, split the cluster along the line where \
satisfaction breaks.

## Step 2 — Intent and page-type compatibility check
Flag and split out any keyword whose intent or implied page type doesn't match the rest of the \
cluster (e.g. a comparison-intent keyword sitting inside a product-topic cluster).

## Step 2b — Over-cluster / over-split tests
Merge two candidate clusters only if they share the same entity, user need, intent, audience and page \
format. Split only if the user need, intent, audience, entity or expected page format materially differs \
— never because of plural/singular, word order, or a minor modifier. Aim for the smallest number of \
genuinely useful pages that satisfy these searches. Do not assume that keywords sharing one broad topic \
belong on one page — "Database Support", "Oracle DBA Support", "MySQL Managed Services" and "Remote DBA \
Services" all sit under "Database" but are different page-level search needs. Watch specifically for: \
entity conflicts (e.g. Oracle vs MySQL), intent conflicts (Service vs Guide), page-purpose conflicts \
(Provider vs Comparison, Troubleshooting vs Commercial Service, Case Study vs Service Page), and audience \
conflicts — do not merge across any of these just because the keywords share a broad topic.

## Step 2c — Cluster purity and conflict signals
For each final cluster, estimate cluster_purity: the fraction of its member keywords that genuinely \
share the same intent, user need, entity, and page purpose as the cluster's dominant group (1.0 = every \
keyword cleanly fits; lower it whenever a member is only a loose or forced fit). A cluster with high \
wording similarity but a real intent or page-purpose conflict inside it must NOT get purity near 1.0. \
List any specific conflicts you noticed but judged not severe enough to split out as short \
conflict_signals strings (e.g. "informational modifier inside a commercial cluster") — empty list when \
none. Also classify the cluster's expected page_purpose as one of: service, product, category, location, \
guide, comparison, pricing, troubleshooting, documentation, case_study, resource, other.

## Step 3 — Catch-all prevention
Reject any cluster name from this list unless the keyword set genuinely matches it exactly: \
General, Miscellaneous, Other, Information, Overview, Corporate, Tech Info, Platform Info, or any \
"[Topic] General" pattern. If no coherent topic can be named, set cluster_status = "Needs Review" \
instead of forcing a name.

## Step 4 — Name the cluster (only now, after membership is final)
Generate a cluster name that describes the validated keyword set specifically — never the \
broader category it happens to sit under.

## Step 5 — Select the primary keyword
From the final validated membership, select the keyword that best represents the cluster's actual \
topic — not automatically the highest-volume one, though it may coincide.

Hard rules:
- Never publish a cluster whose name doesn't match Step 3's catch-all restriction without explicit \
evidence it's genuinely that topic.
- Every keyword from candidate_clusters must receive a final disposition — Needs Review is valid, \
silent omission is not.
- Never assign a cluster_purity near 1.0 merely because the keywords share similar wording — base it on \
intent/need/entity/page-purpose compatibility, per Step 2c.

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "final_clusters": [
    {{"cluster_name": string, "primary_keyword": string, "member_keywords": [string, ...], \
"cluster_status": "Validated" | "Needs Review", "cluster_purity": number (0.0-1.0), \
"conflict_signals": [string, ...], "page_purpose": string}}
  ]
}}
Every keyword from every candidate cluster must appear in exactly one final cluster's \
member_keywords, using its exact original text.
"""


def _parse(raw: str) -> dict | None:
    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _call_and_parse(prompt: str, max_tokens: int) -> dict:
    """Tries every configured provider in order until one parses (2026-09-22,
    same fix as structured_data_insights_service, 2026-09-20; see
    core_problem_service.generate_core_problem's docstring for why)."""
    errors: list[str] = []
    last_raw = ""
    try:
        for raw, provider in iter_text_attempts(prompt, max_tokens=max_tokens, errors=errors):
            last_raw = raw
            data = _parse(raw)
            if data is not None:
                return data
            errors.append(f"{provider} did not return valid JSON")
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    return {"error": " | ".join(errors) if errors else "AI did not return valid JSON", "raw": last_raw[:500]}


def generate_phase2_candidate_clusters(
    keyword_meta: list[dict], client_context: str | None,
) -> tuple[dict[str, dict], list[str]]:
    """Phase 2 — semantic candidate grouping. `keyword_meta` is
    [{keyword, search_volume, keyword_difficulty, source_cluster}, ...].
    Returns (candidate_clusters, unmapped_keywords) where candidate_clusters
    is {candidate_cluster_id: {main_entity, semantic_topic, justification,
    keywords: [str, ...]}} — every value here is the AI's, re-validated
    below against the real input so a hallucinated keyword can never enter
    the pipeline. Empty dict + every keyword in unmapped on total failure
    (no AI configured, malformed JSON twice in a row) — same fail-open
    discipline as every other AI call in this pipeline: never crash the
    report, never invent data."""
    if not keyword_meta:
        return {}, []
    valid_keywords = {m["keyword"] for m in keyword_meta if m.get("keyword")}
    if not valid_keywords:
        return {}, []

    prompt = _PHASE2_PROMPT_TEMPLATE.format(
        keywords_json=json.dumps(keyword_meta, separators=(",", ":"))[:24000],
        client_context=client_context or "no additional business context available",
    )
    max_tokens = min(500 + 40 * len(keyword_meta), 8000)

    def _apply(parsed: dict) -> tuple[dict[str, dict], list[str]] | None:
        clusters = parsed.get("clusters") if isinstance(parsed, dict) else None
        if not isinstance(clusters, dict):
            return None
        seen: set[str] = set()
        result: dict[str, dict] = {}
        for cluster_id, info in clusters.items():
            if not isinstance(info, dict):
                continue
            kws = [kw for kw in (info.get("keywords") or []) if kw in valid_keywords and kw not in seen]
            if not kws:
                continue
            seen.update(kws)
            result[str(cluster_id)] = {
                "main_entity": info.get("main_entity"),
                "semantic_topic": info.get("semantic_topic"),
                "justification": info.get("justification"),
                "keywords": kws,
            }
        unmapped_ai = [kw for kw in (parsed.get("unmapped") or []) if kw in valid_keywords and kw not in seen]
        seen.update(unmapped_ai)
        # Every real keyword not accounted for by the AI's own response
        # (dropped silently, or the AI simply forgot it) still gets an
        # explicit disposition here — the spec's own "never drop one
        # silently" rule, enforced by this caller rather than trusted to
        # the AI's compliance alone.
        truly_unmapped = unmapped_ai + [kw for kw in valid_keywords if kw not in seen]
        return result, truly_unmapped

    parsed = _call_and_parse(prompt, max_tokens)
    if "error" not in parsed:
        applied = _apply(parsed)
        if applied:
            return applied

    retry_parsed = _call_and_parse(prompt, max_tokens)
    if "error" not in retry_parsed:
        applied = _apply(retry_parsed)
        if applied:
            return applied

    logger.warning(
        "Phase 2 candidate clustering failed (%d keywords) — failing open, no candidate clusters formed: %s",
        len(keyword_meta), retry_parsed.get("error") or parsed.get("error"),
    )
    return {}, list(valid_keywords)


def generate_phase3_validated_clusters(candidate_clusters: dict[str, dict]) -> list[dict]:
    """Phase 3 — validate/split/merge/finalize. `candidate_clusters` is
    Phase 2's own output shape. Returns a list of {cluster_name,
    primary_keyword, member_keywords, cluster_status, cluster_purity,
    conflict_signals, page_purpose} — re-validated against Phase 2's real
    keyword set so a hallucinated keyword can never enter a final cluster,
    and any keyword Phase 3 dropped is appended as its own "Needs Review"
    singleton cluster rather than silently vanishing. Empty list on total
    failure — caller leaves every keyword unclustered, same fail-safe
    discipline as Phase 2.

    cluster_purity/conflict_signals/page_purpose (2026-09-25, "Prompt 3"
    quality-control layer folded into this SAME call rather than added as
    a separate third AI pass — an audit found Phase 3's existing checks
    already cover almost all of that prompt's substance nearly verbatim
    [one-page satisfaction test, catch-all prevention, over-split test],
    so a whole extra paid call would have mostly re-asked questions this
    call already answers, at real added token cost the user had just asked
    to minimize. These three fields are the genuinely new, cheap value:
    an explicit purity score and named conflicts even for a cluster that
    still passes, and a page-purpose label used downstream to cap
    evidence_confidence when purity is low — see keyword_cluster_pipeline.
    build_final_keyword_clusters."""
    if not candidate_clusters:
        return []

    def _keyword_text(entry) -> str | None:
        # Phase 3's real input (built by keyword_cluster_pipeline.py) enriches
        # each keyword into {keyword, search_volume, keyword_difficulty,
        # intent} per the spec's own Phase 3 input shape; a bare string is
        # also accepted (Phase 2's raw output shape) for direct callers/tests.
        if isinstance(entry, dict):
            return entry.get("keyword")
        return entry

    valid_keywords: set[str] = set()
    for info in candidate_clusters.values():
        valid_keywords.update(kw for kw in (_keyword_text(e) for e in (info.get("keywords") or [])) if kw)
    if not valid_keywords:
        return []

    prompt = _PHASE3_PROMPT_TEMPLATE.format(candidate_clusters_json=json.dumps(candidate_clusters, separators=(",", ":"))[:24000])
    max_tokens = min(500 + 30 * len(valid_keywords), 8000)

    def _apply(parsed: dict) -> list[dict] | None:
        finals = parsed.get("final_clusters") if isinstance(parsed, dict) else None
        if not isinstance(finals, list):
            return None
        seen: set[str] = set()
        result: list[dict] = []
        for entry in finals:
            if not isinstance(entry, dict):
                continue
            members = [kw for kw in (entry.get("member_keywords") or []) if kw in valid_keywords and kw not in seen]
            if not members:
                continue
            seen.update(members)
            primary = entry.get("primary_keyword")
            if primary not in members:
                primary = members[0]
            purity = entry.get("cluster_purity")
            try:
                purity = max(0.0, min(1.0, float(purity)))
            except (TypeError, ValueError):
                purity = 1.0
            conflicts = [c for c in (entry.get("conflict_signals") or []) if isinstance(c, str) and c.strip()]
            purpose = entry.get("page_purpose")
            purpose = purpose.strip().lower() if isinstance(purpose, str) and purpose.strip() else None
            result.append({
                "cluster_name": (entry.get("cluster_name") or "").strip(),
                "primary_keyword": primary,
                "member_keywords": members,
                "cluster_status": entry.get("cluster_status") if entry.get("cluster_status") in ("Validated", "Needs Review") else "Validated",
                "cluster_purity": purity,
                "conflict_signals": conflicts,
                "page_purpose": purpose,
            })
        # Spec's own hard rule: every keyword from candidate_clusters must
        # receive a final disposition. A keyword Phase 3 dropped becomes
        # its own Needs Review singleton rather than silently vanishing.
        # A singleton is trivially pure (nothing else in it to conflict with).
        for kw in valid_keywords - seen:
            result.append({
                "cluster_name": "", "primary_keyword": kw, "member_keywords": [kw], "cluster_status": "Needs Review",
                "cluster_purity": 1.0, "conflict_signals": [], "page_purpose": None,
            })
        return result

    parsed = _call_and_parse(prompt, max_tokens)
    if "error" not in parsed:
        applied = _apply(parsed)
        if applied is not None:
            return applied

    retry_parsed = _call_and_parse(prompt, max_tokens)
    if "error" not in retry_parsed:
        applied = _apply(retry_parsed)
        if applied is not None:
            return applied

    logger.warning(
        "Phase 3 cluster validation failed (%d keywords) — failing open, no final clusters formed: %s",
        len(valid_keywords), retry_parsed.get("error") or parsed.get("error"),
    )
    return []
