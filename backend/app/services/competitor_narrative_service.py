"""Competitor narrative — matches the reference decks' "What They're Best
At" + "Areas of Focus for {Client} (vs {Competitor})" slide pair: objective
bullets on the competitor's own tactics, specific recommendations in
response, and a closing Strategic Growth Opportunity paragraph. Grounded
only in whatever data was actually uploaded for that competitor — same
"don't invent numbers" discipline as the AI Summary feature.

generate_competitor_narratives_batch writes narratives for ALL of a
client's competitors in ONE AI call instead of one call per competitor —
deliberate: this app's free-tier AI quota (Gemini's daily cap, Groq's
per-minute/hourly cap) is the binding constraint on report generation, and
competitor narratives were routinely the single largest chunk of a
report's 7-8 total AI calls (up to 5, one per competitor). Collapsing them
to 1 call roughly halves a report's total AI-call footprint. Trade-off,
accepted deliberately: if this one call fails outright, every
competitor's narrative is lost together, instead of just one — but a
malformed/missing individual competitor within an otherwise-successful
response is still recovered per-domain (see _parse_batch_result)."""

import json
import re

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

Then, separately per competitor, write "areas_of_focus": specific, actionable steps {client_name} should take \
in response to THAT competitor — grounded in both its best_at findings and its metric gaps, not just a \
restatement of what it does. Metrics alone are not a substitute for what a competitor is actually doing \
on-site — prioritize recommendations that respond to a concrete best_at tactic over pure metric comparisons \
whenever that competitor's homepage_text gave you something to work with.

Also write "opportunity_analysis" for that competitor, a structured WHAT COMPETITOR HAS -> WHAT CLIENT LACKS -> \
WHY IT MATTERS -> WHAT CLIENT SHOULD BUILD breakdown, covering (whichever of these the data actually supports — \
never invent ones it doesn't): what topics/page types it ranks for, which audiences or industries it targets, \
which content formats it uses, whether it has tools/calculators, comparison pages, an FAQ section, trust signals \
(badges, certifications, review counts, guarantees), or dedicated commercial/pricing pages. If "ranking_page_types" \
is present in a competitor's data, treat those counts as real evidence of the page types it has (e.g. several \
comparison-shaped ranking keywords means it likely has comparison pages) rather than guessing from homepage_text \
alone. Each of the four lists should be SHORT — at most 4 items, each one sentence — and line up 1:1 where \
possible — item 1 of "competitor_has" pairs with item 1 of "client_lacks", "why_it_matters", and \
"client_should_build". If the data is \
too thin to support this breakdown for a competitor, omit "opportunity_analysis" for that competitor entirely \
rather than inventing content — never invent tools, calculators, FAQs, or trust signals that aren't evidenced.

Write in plain, confident agency language — this is client-facing content, not an AI-generated draft. \
Never mention that you are an AI, a language model, or any tool by name; write as the agency's own analysis. \
Avoid hedging ("may," "could potentially") where the data supports a direct statement — flag genuine \
uncertainty explicitly instead of hedging every sentence. Every bullet and sentence must be complete, with \
terminal punctuation — if you're about to run out of room on one competitor, drop a less-important point for \
that competitor entirely rather than truncate one mid-sentence, and never let it cost another competitor its \
own section.

Data per competitor, each keyed by its exact domain string:
{data_json}

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape — one entry per competitor \
domain listed above, using the EXACT domain string as the key:
{{
  "narratives": {{
    "<competitor-domain>": {{
      "best_at": [string],          // 3-6 short, objective bullets naming concrete tactics/strengths this competitor actually uses — written about the competitor, not advice for {client_name}
      "areas_of_focus": [string],   // 6-9 short, specific, actionable bullets — what {client_name} should do in response to this competitor, ordered by impact
      "growth_opportunity": string, // 2-4 sentence closing paragraph naming the strategic opening {client_name} has vs. this specific competitor
      "opportunity_analysis": {{    // OPTIONAL — omit entirely for a competitor if the data is too thin to support it
        "competitor_has": [string],       // at most 4 short bullets: concrete things this competitor has (page types, audiences/industries targeted, content formats, tools/calculators, comparison pages, FAQs, trust signals, commercial pages) — only ones the data actually evidences
        "client_lacks": [string],         // at most 4 bullets, lined up 1:1 with competitor_has: the matching thing {client_name} doesn't have
        "why_it_matters": [string],       // at most 4 bullets, lined up 1:1: why each gap matters (traffic, trust, conversion)
        "client_should_build": [string]   // at most 4 bullets, lined up 1:1: the concrete thing {client_name} should build in response
      }}
    }}
  }}
}}
"""

_NARRATIVE_KEYS = {"best_at", "areas_of_focus", "growth_opportunity"}


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
    competitor's narrative can't meaningfully shrink below that."""
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


def _generate_chunk(
    client_name: str, client_domain: str, chunk_facts: dict[str, dict]
) -> dict[str, dict]:
    """One AI call covering just this chunk's competitors — same retry-once
    discipline as the old single-batch call, scoped to a subset of domains
    small enough that Groq can actually serve it instead of always
    overflowing to the next provider."""
    domains = list(chunk_facts.keys())
    prompt = BATCH_PROMPT_TEMPLATE.format(
        client_name=client_name,
        client_domain=client_domain,
        competitor_count=len(domains),
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
    """Returns {domain: {"best_at", "areas_of_focus", "growth_opportunity"}}
    for each domain that came back well-formed, and {domain: {"error": ...}}
    for any that didn't — a domain missing entirely from its chunk's
    response, or present but missing one of the three required keys, is
    reported as failed for just that domain rather than silently dropped or
    treated as a total failure.

    Splits competitors_facts into GROQ_TPM_BUDGET-sized chunks (one AI call
    per chunk, not per competitor) rather than one call for all of them —
    confirmed real: a single call covering 4-5 competitors needs up to
    16000 output tokens, which Groq's ~7500 tokens/minute shared budget can
    never serve regardless of retries or provider order, so it always fell
    through past Groq (or worse, got silently truncated before that
    fallthrough existed). Chunking keeps each call within what Groq can
    actually deliver, and text_ai_client's rolling-window budget reservation
    naturally spaces multiple Groq-served chunks across successive minutes
    instead of bursting past the shared limit."""
    if not competitors_facts:
        return {}

    template_overhead_chars = len(BATCH_PROMPT_TEMPLATE.format(
        client_name=client_name, client_domain=client_domain, competitor_count=1, data_json="",
    ))
    chunks = _chunk_domains(competitors_facts, template_overhead_chars)

    results: dict[str, dict] = {}
    for chunk_domains in chunks:
        chunk_facts = {d: competitors_facts[d] for d in chunk_domains}
        results.update(_generate_chunk(client_name, client_domain, chunk_facts))
    return results
