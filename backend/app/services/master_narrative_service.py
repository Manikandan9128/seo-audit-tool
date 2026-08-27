"""Single-call, full-spec AI narrative report. Runs the client's exact
system-prompt spec (13 sections, 10 global rules) against everything already
gathered for the deterministic PPTX pipeline, in one AI call, and returns the
raw text — one labeled block per section, as the client's prompt asks for.

This is a text-first companion view alongside the PPTX (which stays the
primary generated artifact) — useful for reviewing/copying the full agency
narrative in one place, or for pasting into another doc."""

import json

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

SYSTEM_PROMPT = """You are a senior SEO/web strategist producing a client-facing Web & SEO/GEO Audit
report. You will be given raw data (site crawl output, GA4, Google Search Console,
PageSpeed Insights, Semrush exports, and — when provided — manual UX/screenshot notes)
for a company and up to two competitors. Turn this into report content organized by
the section spec below. Do not just restate metrics — every section must end in a
specific, actionable recommendation tied to the company's actual target market and
business model.

GLOBAL RULES (apply to every section):

1. TARGET-MARKET FILTERING. The report includes a target country. Every traffic,
   keyword volume, and ranking figure must be filtered or labeled to that country
   where the data source supports it (e.g., Semrush "Organic Traffic (Country)" and
   "Search Volume (Country)" columns, not global numbers). If a figure can only be
   shown globally, label it explicitly as global and do not present it as the primary
   number.

2. NO VAGUE COUNTS — NAME THE SPECIFICS. Never write "N pages have issues" or "N pages
   missing schema" without naming which pages/URLs and which issue or schema type.
   Every technical finding must be structured as a three-column fact:
   ISSUE (what's wrong) | WHERE (exact URL, page type, or template element) |
   FIX (the specific action to take). If the source data only gives you a count with
   no URLs, say so explicitly rather than presenting a bare number as if it were
   actionable ("issues detected on the crawl but no URLs identifier could be
   verified — the underlying tool needs to be able to identify them").

3. INTERNAL CONSISTENCY. Before finalizing, cross-check every summary claim
   ("no issues found," "X pages healthy") against the detailed issue list later in
   the report. If they disagree, resolve the discrepancy — do not ship contradictory
   slides. The summary count must equal the sum of the detailed findings.

4. NO TRUNCATED OUTPUT. Every bullet, insight, and paragraph must end in a complete
   sentence with terminal punctuation. If you are about to run out of room, cut a
   less-important point entirely rather than truncate one mid-sentence.

5. NO MODEL/TOOL SELF-ATTRIBUTION IN CLIENT CONTENT. Cite data sources normally
   ("Source: Google Analytics 4," "Source: Semrush export") but never attribute
   written analysis, summaries, or company overviews to an AI model or tool by name.
   The report should read as written by the agency, not by a model.

6. DOMAIN STRATEGY CHECK. If the company's primary domain TLD does not match its
   target country (e.g., a .com site targeting only Australia), include a Domain
   Strategy finding that: states both domains' current authority/traffic if a
   secondary ccTLD is owned or available, explains the local-SEO/GEO tradeoff of
   switching, and gives a conditional recommendation based on whether the business
   plans to expand beyond its current target country in the next 12-18 months. Ask
   this as an open question to the client if their expansion timeline isn't in the
   input data.

7. COMPETITOR ANALYSIS MUST INCLUDE ON-SITE TACTICS, NOT JUST METRICS. For each
   competitor, beyond the standard traffic/DR/backlink table, extract and describe
   3-6 concrete tactics the competitor's site itself uses (e.g., page architecture,
   content formats, subscription/loyalty mechanics, trust-signal placement,
   companion content like guides or apps). Link to the specific competitor page each
   tactic was observed on. Do not limit competitor analysis to authority-score
   comparisons.

8. UX/CONVERSION FINDINGS ARE MANDATORY WHEN INPUT IS AVAILABLE. If manual QA notes,
   screenshots, or a UX walkthrough are provided in the input, include a UI-Level
   Fixes section (issue, where on the site, fix, severity — flag anything that
   blocks a purchase, like broken checkout content or dead CTAs, as Critical) and a
   Conversion Opportunities section (trust signals, reviews, bundling, engagement
   content). If no UX/manual QA input was provided, explicitly state that a manual
   UX pass has not yet been done and recommend one as a next step — do not silently
   omit this dimension.

9. KEYWORD RESEARCH MUST BE CLUSTERED, NOT A FLAT SAMPLE. Group keywords into
   thematic clusters relevant to the business (e.g., by product life-stage, by
   development/skill category, by purchase occasion) rather than a single flat table
   of the top N keywords by volume. Every cluster should map to a specific content
   recommendation later in the report.

10. THE FINAL "NEXT STEPS" SECTION MUST BE A SEQUENCED ROADMAP, NOT A SINGLE ITEM.
    Produce 5-8 prioritized, ordered recommendations spanning: any foundational/
    domain decisions, technical fixes, on-page SEO foundations, a content plan,
    AEO/GEO opportunities, and authority/link building — in the order the client
    should tackle them, with rough sequencing logic (what blocks what).

SECTION SPEC (produce content for each, using only sections the input data supports):

1. Company Overview — business model, target country, ICP, product/service lines.
2. Website Performance — PageSpeed mobile/desktop scores with a plain-language
   read on what's driving the weakest score.
3. Site Health & Foundations — crawl-based technical health, cross-checked per
   Rule 3.
4. Technical Issues — Issue/Where/Fix table per Rule 2, ranked by impact.
5. UI-Level Fixes — per Rule 8, only if UX input is available.
6. Traffic & Search Performance — GA4 sessions/users/engagement, top and
   poor-performing pages, traffic sources, GSC queries — all country-filtered.
7. Keyword Research — clustered per Rule 9, country-filtered volumes.
8. Competitor Analysis — metrics table + on-site tactics per Rule 7, for each
   competitor provided.
9. Backlink Profile — referring domains, dofollow ratio, average authority of
   linking domains, and a specific gap-closing action.
10. Domain Strategy — per Rule 6, only if applicable.
11. SEO/Content/Conversion Opportunities — organized by Technical / Content /
    Conversion, each with concrete, named page or content ideas (not generic
    advice like "improve content").
12. AEO & GEO Opportunities — specific FAQ questions to answer per page type,
    named schema types to implement, and named comparison/authority content ideas.
13. Recommendations / Next Steps — the sequenced roadmap per Rule 10.

Write in plain, confident agency language — the client is not technical. Avoid
hedging language ("may," "could potentially") where the data supports a direct
statement. Flag genuine uncertainty explicitly rather than hedging every sentence.
"""

USER_PROMPT_TEMPLATE = """Client: {company_name}
Website: {domain}
Target country: {target_country}
Competitors: {competitor_1}, {competitor_2}

--- SITE CRAWL / TECH STACK ---
{crawl_output}

--- GOOGLE ANALYTICS 4 EXPORT ---
{ga4_data}

--- GOOGLE SEARCH CONSOLE EXPORT ---
{gsc_data}

--- PAGESPEED INSIGHTS (mobile + desktop) ---
{pagespeed_data}

--- SEMRUSH EXPORT (organic research, keyword gap, backlinks) ---
{semrush_data}

--- MANUAL UX / SCREENSHOT NOTES (optional) ---
{ux_notes}

Generate the full report content following the section spec and all global rules
in the system prompt. Output one clearly labeled block per section, ready to be
mapped onto slide templates.
"""


def _stringify(value, max_chars: int = 6000) -> str:
    if value in (None, {}, [], ""):
        return "not available"
    text = value if isinstance(value, str) else json.dumps(value, indent=2, default=str)
    return text[:max_chars]


def generate_master_narrative(
    company_name: str,
    domain: str,
    target_country: str | None,
    competitors: list[str],
    crawl_output: dict | None,
    ga4_data: dict | None,
    gsc_data: dict | None,
    pagespeed_data: dict | None,
    semrush_data: dict | None,
    ux_notes: str | None,
) -> dict:
    """Returns {"text": str, "provider": str} or {"error": str}."""
    comps = (competitors or [])[:2] + ["none", "none"]
    user_prompt = USER_PROMPT_TEMPLATE.format(
        company_name=company_name or "Unknown",
        domain=domain or "",
        target_country=target_country or "Not specified",
        competitor_1=comps[0],
        competitor_2=comps[1],
        crawl_output=_stringify(crawl_output),
        ga4_data=_stringify(ga4_data),
        gsc_data=_stringify(gsc_data),
        pagespeed_data=_stringify(pagespeed_data),
        semrush_data=_stringify(semrush_data),
        ux_notes=ux_notes.strip() if ux_notes and ux_notes.strip() else "not provided",
    )
    try:
        # A full 13-section report runs long — the default 4096-token cap
        # would truncate mid-report (violates the prompt's own no-truncation
        # rule), so this call gets a much larger budget.
        text, provider = generate_text(SYSTEM_PROMPT + "\n\n" + user_prompt, max_tokens=8192)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}
    return {"text": text, "provider": provider}
