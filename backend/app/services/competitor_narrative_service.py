"""Competitor narrative for two slides: "What They're Best At" (best_at —
objective bullets on the competitor's own on-site tactics) and Competitor
Opportunity Analysis, rebuilt 2026-09-11 as a single-differentiator slide —
Headline naming the ONE thing only this competitor does, 1-2 Unique Angle
bullets (specific mechanics, not marketing adjectives), one Gap bullet tied
directly to that angle, and a Shared Advantage line ONLY when the same
angle is genuinely shared with another competitor already covered in this
deck (never presented as unique to both). Replaces the old three-quadrant
(WHAT COMPETITOR HAS / WHAT CLIENT LACKS / WHY IT MATTERS) +
areas_of_focus + growth_opportunity structure, which routinely surfaced
baseline hygiene tactics (demo CTAs, trust badges, live chat, generic case
studies) most competitors share rather than isolating what's genuinely
distinct — those belong on a separate shared comparison-matrix slide, not
here. Grounded only in whatever data was actually uploaded for that
competitor — same "don't invent numbers" discipline as the AI Summary
feature.

generate_competitor_narratives_batch writes narratives for ALL of a
client's competitors in ONE AI call instead of one call per competitor —
deliberate: this app's free-tier AI quota (Gemini's daily cap, Groq's
per-minute/hourly cap) is the binding constraint on report generation, and
competitor narratives were routinely the single largest chunk of a
report's 7-8 total AI calls (up to 5, one per competitor). Collapsing them
to 1 call roughly halves a report's total AI-call footprint — and, as a
second deliberate benefit, lets the model compare every competitor against
every other one in the SAME call to correctly label a shared differentiator
as "Shared Advantage" instead of claiming it as unique to more than one.
When Groq's per-minute budget forces the batch into multiple chunked calls
(_chunk_domains), each later chunk is given the unique angles already
claimed by earlier chunks specifically so that cross-chunk overlap is
still caught (see _already_claimed_context) — the alternative, checking
only within a chunk, would silently miss overlap across a chunk boundary.
Trade-off, accepted deliberately: if this one call fails outright, every
competitor's narrative is lost together, instead of just one — but a
malformed/missing individual competitor within an otherwise-successful
response is still recovered per-domain (see _parse_batch_result)."""

import json
import re

from app.config import settings
from app.integrations.text_ai_client import GROQ_TPM_BUDGET, NoAIProviderConfigured, generate_text

BATCH_PROMPT_TEMPLATE = """You are an SEO/growth consultant writing competitive-analysis sections for \
{client_name} ({client_domain}), comparing them against {competitor_count} competitors. Write ONE independent \
section per competitor listed below — never mix facts, tactics, or numbers between different competitors, and \
never invent traffic numbers, rankings, keywords, or product features that aren't stated for that specific \
competitor. If one competitor's data is thin, write fewer, more general (but still grounded) bullets for that \
one only — it must not affect the quality of any other competitor's section.

For each competitor, if "homepage_text" is present in its data, read it and pull out 3-6 concrete on-site \
tactics that competitor actually uses — page architecture, content formats, subscription/loyalty mechanics, \
trust-signal placement, companion content like guides or apps. Write these as "best_at": objective bullets \
stated ABOUT that competitor, not as advice for {client_name} — e.g. "Leads with a 30-day money-back badge \
above the fold (homepage_url)." Each bullet should name the specific tactic and cite "homepage_url" as the \
source. If homepage_text is thin or absent for a competitor, fall back to what its metrics alone support \
(e.g. a clear traffic or keyword-volume lead) rather than inventing on-site tactics.

Then, separately per competitor, identify its ONE genuinely distinct differentiator for the Competitor \
Opportunity Analysis slide. Rules for this part, strictly:
1. Do NOT count baseline hygiene tactics shared by most competitors in this category — demo CTAs, trust \
   badges, live chat, generic case studies, standard pricing pages, newsletter signups, and the like. Those \
   are not differentiators here even if they're genuinely present in best_at above.
2. Ask "what does ONLY this one do?", not "what does this one do well." A tactic every other competitor in \
   this batch also does is disqualified, no matter how effective it is.
3. Before finalizing a competitor's unique angle, check it against every OTHER competitor's angle in this \
   same batch{already_claimed_clause}. If two (or more) competitors genuinely share the same real \
   differentiator, do NOT present it as unique to either — give each of them a "shared_advantage" field \
   instead (see schema below), naming the other competitor(s) it's shared with, and find each one a separate, \
   still-genuinely-distinct angle for their "unique_angle"/"headline" if one exists; if a competitor truly has \
   no non-shared, non-baseline differentiator at all, still fill "headline"/"unique_angle" with the closest \
   real distinction available rather than leaving it empty, but do NOT invent one that isn't evidenced.
4. "unique_angle" bullets must describe specific mechanics — the actual mechanism, page type, pricing \
   structure, or on-site behavior — never marketing adjectives ("innovative," "best-in-class," "seamless") \
   with no mechanism behind them.
5. Write exactly one "gap" bullet per competitor, tied SPECIFICALLY to that competitor's unique angle — what \
   {client_name} lacks in direct response to that exact mechanism, not a generic recommendation that could \
   apply to any competitor in this batch.
6. "headline" is a short phrase (not a full sentence) naming the angle itself, e.g. "usage-based pricing \
   calculator" — the slide will render it as "{{competitor}}'s Unique Angle: {{headline}}".

Write in plain, confident agency language — this is client-facing content, not an AI-generated draft. \
Never mention that you are an AI, a language model, or any tool by name; write as the agency's own analysis. \
Avoid hedging ("may," "could potentially") where the data supports a direct statement — flag genuine \
uncertainty explicitly instead of hedging every sentence. Every bullet and sentence must be complete, with \
terminal punctuation — if you're about to run out of room on one competitor, drop a less-important point for \
that competitor entirely rather than truncate one mid-sentence, and never let it cost another competitor its \
own section.

Data per competitor, each keyed by its exact domain string:
{data_json}
{already_claimed_json}
Return ONLY valid JSON, no markdown fences, no commentary, matching this shape — one entry per competitor \
domain listed above, using the EXACT domain string as the key:
{{
  "narratives": {{
    "<competitor-domain>": {{
      "best_at": [string],          // 3-6 short, objective bullets naming concrete tactics/strengths this competitor actually uses — written about the competitor, not advice for {client_name}
      "headline": string,           // short phrase naming this competitor's ONE genuinely distinct differentiator (not a full sentence, not a baseline/shared tactic)
      "unique_angle": [string],     // 1-2 bullets, specific mechanics (the actual mechanism/page type/pricing structure), never marketing adjectives
      "gap": string,                // exactly ONE bullet: what {client_name} lacks, tied specifically to this competitor's unique angle above
      "shared_advantage": string | null   // ONLY non-null if this exact angle is genuinely shared with another competitor already covered (name it) — omit the differentiator framing above in that case; null/omit otherwise, never invented
    }}
  }}
}}
"""

_NARRATIVE_KEYS = {"best_at", "headline", "unique_angle", "gap"}


def _call_and_parse(prompt: str, max_tokens: int) -> dict:
    """One generate+parse attempt. Returns the parsed dict or {"error": ...}."""
    try:
        raw, _provider = generate_text(prompt, max_tokens=max_tokens)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Some models (seen with Groq's openai/gpt-oss-120b) prepend a line
        # or two of commentary/reasoning before the JSON object despite the
        # "return ONLY valid JSON" instruction - the fence-strip above only
        # catches ``` markers at the very start/end, not stray prose. Fall
        # back to grabbing the outermost {...} span before giving up.
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {"error": "AI did not return valid JSON", "raw": raw[:500]}


# Sizing a chunk against Groq's own TPM budget (not some independent
# number) means a chunk that fits is one Groq can actually serve outright
# instead of raising and falling through to Gemini/Claude every time — the
# rolling-window reserve in text_ai_client.py then naturally spaces
# multiple Groq-served chunks across successive minutes as needed. A chunk
# that still doesn't fit (one competitor's own homepage_text alone is huge)
# still falls through per-call exactly as before; this is a sizing target,
# not a hard guarantee.
_CHUNK_OUTPUT_TOKENS_PER_DOMAIN = 4096  # matches the pre-batching per-competitor budget


def _chunk_domains(competitors_facts: dict[str, dict], template_overhead_chars: int) -> list[list[str]]:
    """Greedily groups domains so each chunk's estimated prompt+output stays
    within GROQ_TPM_BUDGET — one domain that alone exceeds it still gets its
    own (oversized) chunk rather than being split further, since a single
    competitor's narrative can't meaningfully shrink below that.

    GROQ_TPM_BUDGET is Groq-specific (its real per-minute shared cap) and
    irrelevant when Groq isn't even configured — Gemini/Claude have no such
    constraint modeled here (generate_text()'s max_tokens is a no-op for
    Gemini, and Claude's own cap is far above what a handful of competitors
    needs). Confirmed real: with only a Gemini key set, this budget math
    forces a separate chunk (and separate AI call, since 2 domains' own
    _CHUNK_OUTPUT_TOKENS_PER_DOMAIN estimate alone already exceeds
    GROQ_TPM_BUDGET) per competitor instead of the intended single batched
    call, quadrupling Gemini calls for a 4-competitor report and burning
    its daily quota mid-report — later competitors (and the Onboarding
    Breakdown vision call after them) then silently fail once quota's gone.
    Skip the Groq-sized chunking entirely when Groq isn't configured."""
    if not settings.groq_api_key:
        return [list(competitors_facts.keys())]

    chunks: list[list[str]] = []
    current: list[str] = []
    current_data_chars = 0
    for domain, facts in competitors_facts.items():
        domain_chars = len(json.dumps({domain: facts}, default=str))
        candidate_chars = current_data_chars + domain_chars
        candidate_tokens = (template_overhead_chars + candidate_chars) // 4 + _CHUNK_OUTPUT_TOKENS_PER_DOMAIN * (len(current) + 1)
        if current and candidate_tokens > GROQ_TPM_BUDGET:
            chunks.append(current)
            current, current_data_chars = [], 0
        current.append(domain)
        current_data_chars += domain_chars
    if current:
        chunks.append(current)
    return chunks


def _already_claimed_prompt_parts(already_claimed: dict[str, dict]) -> tuple[str, str]:
    """Renders the two template placeholders that let a later chunk see
    what earlier chunks already claimed as a competitor's unique angle —
    without this, cross-chunk overlap (two competitors in DIFFERENT chunks
    sharing the same real differentiator) would never get caught, since
    each chunk otherwise only sees its own competitors."""
    if not already_claimed:
        return "", ""
    clause = " AND against the competitors already analyzed in an earlier batch, listed below"
    summary = {
        domain: {"headline": n.get("headline"), "unique_angle": n.get("unique_angle")}
        for domain, n in already_claimed.items()
        if n.get("headline") or n.get("unique_angle")
    }
    if not summary:
        return "", ""
    json_block = (
        "\nCompetitors already analyzed in an earlier chunk of this same report, with their claimed unique "
        "angles (for overlap-checking only — do not rewrite these, just cite by domain if a genuine shared "
        f"angle applies to a competitor below):\n{json.dumps(summary, indent=2, default=str)}\n"
    )
    return clause, json_block


def _generate_chunk(
    client_name: str, client_domain: str, chunk_facts: dict[str, dict], already_claimed: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """One AI call covering just this chunk's competitors — same retry-once
    discipline as the old single-batch call, scoped to a subset of domains
    small enough that Groq can actually serve it instead of always
    overflowing to the next provider. already_claimed carries forward the
    headline/unique_angle of every competitor from EARLIER chunks so this
    chunk's shared_advantage check can catch overlap across chunk
    boundaries, not just within this one call."""
    domains = list(chunk_facts.keys())
    already_claimed_clause, already_claimed_json = _already_claimed_prompt_parts(already_claimed or {})
    prompt = BATCH_PROMPT_TEMPLATE.format(
        client_name=client_name,
        client_domain=client_domain,
        competitor_count=len(domains),
        already_claimed_clause=already_claimed_clause,
        already_claimed_json=already_claimed_json,
        # ~8000 chars was the per-competitor budget before batching; scaled
        # up per-domain here (capped higher overall) so richer per-domain
        # data — especially homepage_text — isn't starved just because
        # several competitors now share one prompt.
        data_json=json.dumps(chunk_facts, indent=2, default=str)[:8000 * len(domains)],
    )
    max_tokens = min(_CHUNK_OUTPUT_TOKENS_PER_DOMAIN * len(domains), 16000)

    def _parse_batch_result(result: dict) -> dict[str, dict] | None:
        """Returns {domain: narrative-or-error} if the response was usable
        at all (has a "narratives" object), else None to signal a total
        failure worth retrying."""
        narratives = result.get("narratives")
        if not isinstance(narratives, dict):
            return None
        parsed: dict[str, dict] = {}
        for domain in domains:
            entry = narratives.get(domain)
            if isinstance(entry, dict) and _NARRATIVE_KEYS <= entry.keys():
                parsed[domain] = entry
            else:
                parsed[domain] = {"error": f"Missing or malformed narrative for {domain} in the batched response"}
        return parsed

    result = _call_and_parse(prompt, max_tokens)
    if "error" not in result:
        parsed = _parse_batch_result(result)
        if parsed is not None:
            return parsed
        result = {"error": "AI response had no usable \"narratives\" object", "raw": str(result)[:500]}

    # A single malformed-JSON (or unusable-shape) response is a
    # non-deterministic model hiccup, not a systemic failure — same
    # discipline as the old per-competitor retry, now applied per chunk:
    # one retry on a fresh sample before giving up on this chunk's domains.
    retry_result = _call_and_parse(prompt, max_tokens)
    if "error" not in retry_result:
        parsed = _parse_batch_result(retry_result)
        if parsed is not None:
            return parsed
        retry_result = {"error": "AI response had no usable \"narratives\" object", "raw": str(retry_result)[:500]}

    error = result.get("error", "Unknown error")
    return {domain: {"error": error} for domain in domains}


def generate_competitor_narratives_batch(
    client_name: str, client_domain: str, competitors_facts: dict[str, dict]
) -> dict[str, dict]:
    """Returns {domain: {"best_at", "headline", "unique_angle", "gap",
    "shared_advantage"}} for each domain that came back well-formed, and
    {domain: {"error": ...}} for any that didn't — a domain missing
    entirely from its chunk's response, or present but missing one of the
    required keys, is reported as failed for just that domain rather than
    silently dropped or treated as a total failure.

    Splits competitors_facts into GROQ_TPM_BUDGET-sized chunks (one AI call
    per chunk, not per competitor) rather than one call for all of them —
    confirmed real: a single call covering 4-5 competitors needs up to
    16000 output tokens, which Groq's ~7500 tokens/minute shared budget can
    never serve regardless of retries or provider order, so it always fell
    through past Groq (or worse, got silently truncated before that
    fallthrough existed). Chunking keeps each call within what Groq can
    actually deliver, and text_ai_client's rolling-window budget reservation
    naturally spaces multiple Groq-served chunks across successive minutes
    instead of bursting past the shared limit. Chunks are processed in
    order (not parallel) specifically so each one can be given the
    unique angles already claimed by every earlier chunk — see
    _already_claimed_prompt_parts — letting shared_advantage detection
    work across chunk boundaries, not just within a single call."""
    if not competitors_facts:
        return {}

    template_overhead_chars = len(BATCH_PROMPT_TEMPLATE.format(
        client_name=client_name, client_domain=client_domain, competitor_count=1,
        already_claimed_clause="", already_claimed_json="", data_json="",
    ))
    chunks = _chunk_domains(competitors_facts, template_overhead_chars)

    results: dict[str, dict] = {}
    for chunk_domains in chunks:
        chunk_facts = {d: competitors_facts[d] for d in chunk_domains}
        already_claimed = {d: n for d, n in results.items() if "error" not in n}
        results.update(_generate_chunk(client_name, client_domain, chunk_facts, already_claimed))
    return results
