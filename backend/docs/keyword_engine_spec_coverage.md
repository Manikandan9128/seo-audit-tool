# Universal SEO Keyword Intelligence — spec coverage

Source prompt: `UNIVERSAL SEO KEYWORD INTELLIGENCE, CLUSTERING & TARGETING ENGINE.docx` (given 2026-09-23).

This file is the single place that says what of the prompt is built. **Update it in the same commit as any
keyword-engine change.** "Done" means: in the code, reaching the report (slide and/or Sheet), and covered by
a test. Anything left out is listed with its reason — never dropped silently.

Status: **Done** · **Partial** (what's missing is named) · **Blocked** (needs a data source, not code)

Main files: `app/services/keyword_intelligence_service.py` (per keyword), `keyword_cluster_pipeline.py`
(automatic clustering), `keyword_strategy_service.py` (cluster/strategy layer), `keyword_relevance_service.py`
(relevance, brands, junk, page matching), `strategic_keyword_selection_service.py` (client cluster sheet),
`google_sheets_service.py` (Sheet), `app/reporting/pptx_builder.py` (slides), `app/api/routes/site_audit.py` (wiring).

Tests: `tests/test_keyword_intelligence_engine.py`, `test_keyword_strategy_service.py`,
`test_keyword_engine_quality_fixes.py`, `test_keyword_spec_coverage.py`.

**Strict count (every point inside a section must be built for Done): 41 Done · 24 Partial · 4 Blocked — of 69.** Of
the Partial ones, all now need either SERP data (Semrush API, ruled out 2026-09-24), the AI grouping step (beyond
the curated abbreviation list), or a review-queue UI (§63's `outcome` flag) — none are code-only anymore as of the
2026-09-24 depth pass (`keyword_site_model.py`, `keyword_strategy_depth.py`, `keyword_history_service.py`).

| § | Section | Status | What's missing | Needs |
|---|---|---|---|---|
| 1 | Primary objective (full intelligence model) | Partial | SERP intent, semantic similarity outputs | SERP data (Semrush API) |
| 2 | Core principle, answer Q1-Q7 per keyword | Partial | Q5 (do SERPs treat them as same intent) | SERP data (Semrush API) |
| 3 | Website understanding (business model, entity graph) | Done | `keyword_site_model.build_site_model` entity graph (products/services/industries/audiences/locations/use cases/features/integrations/docs/technologies) | — |
| 4 | Website architecture analysis (14 fields per URL) | Done | per-URL page type/primary entity/topic/audience/intent/funnel/geo/content depth/inlinks/crawl depth/link role/purpose/traffic — Sheet "Site Pages" tab | — |
| 5 | Keyword normalization | Done | — | — |
| 6 | Entity extraction + entity relationship | Done | `entity_relationship()` (product->category/industry/audience/location/attribute/use case, problem->solution) — Sheet "Entity Relationship" column | — |
| 7 | Modifier extraction | Done | — | — |
| 8 | Search intent (primary, secondary, confidence, mixed) | Done | — | — |
| 9 | User need | Done | — | — |
| 10 | Funnel stage | Done | — | — |
| 11 | Audience | Done | `keyword_audience_from_site` — audience/industry the site itself serves, read from its pages | — |
| 12 | Geographic intent | Done | `geo_served` — listed location / country-level / not listed, read from crawl + overview | — |
| 13 | Semantic relationship (embeddings) | Blocked | no similarity score | AI grouping step working |
| 14 | Search intent relationship (intent similarity) | Done | `intent_similarity` cosine score; sibling pairs >=0.9 surfaced as a "Possible same-need clusters" review item | — |
| 15 | SERP analysis / overlap | Blocked | no SERP data | SERP data (Semrush API) |
| 16 | Page cohesion score | Blocked | needs SERP overlap | SERP data (Semrush API) |
| 17 | Same page vs separate page | Partial | curated-abbreviation merges now work without AI (dba/hr/it/...); a real synonym pair outside that list, or the SERP criterion, still needs AI/SERP | AI grouping step working |
| 18 | Topic hierarchy (root->entity->parent->subtopic->intent->cluster) | Done | `deepen_topics` hierarchy dict — Sheet "Topic Hierarchy" tab | — |
| 19 | Primary keyword selection | Done | `primary_keyword_scores` — 7-factor score picks the primary in non-AI clusters | — |
| 20 | Primary keyword score (normalized components) | Done | 0-100 `primary_score` per keyword — Sheet column | — |
| 21 | Business relevance gate | Done | — | — |
| 22 | Page type classification | Done | — | — |
| 23 | Existing URL mapping (11 checks) | Partial | content depth/internal links/traffic/conversion role/per-page authority (grouped from the Backlinks export by target URL) now checked (`target_evidence`); only SERP alignment still missing | SERP data (Semrush API) |
| 24 | Cannibalization detection | Partial | Search Console pairs + near-identical-title pairs (crawl-only) now similarity-scored; true SERP-overlap signal still missing | SERP data (Semrush API) |
| 25 | Content gap detection | Done | — | — |
| 26 | Competitor gap analysis | Done | — | — |
| 27 | Programmatic SEO detection | Partial | entity x dimension patterns built (`programmatic_patterns`); 'search engines would rank each page' still needs SERP | SERP data (Semrush API) |
| 28 | Clustering algorithm (10 signals) | Partial | no SERP / semantic similarity in scoring | SERP data (Semrush API) |
| 29 | Cluster types | Done | — | — |
| 30 | Cluster confidence | Done | — | — |
| 31 | Opportunity score (+ recalibration) | Done | `keyword_history_service` — per-cluster decisions stored on `clients.keyword_decision_history`, `recalibrate()` nudges opportunity by (cluster_type, business_rule) accept-rate once a group has 5+ judged outcomes; neutral (no effect) until `outcome` is set (see §63) | — |
| 32 | Difficulty interpretation | Done | KD read against own DR AND own topical authority (pages already covering the topic) | — |
| 33 | Search volume interpretation | Done | — | — |
| 34 | SERP intent validation | Blocked | no SERP data | SERP data (Semrush API) |
| 35 | Intent changes by modifier | Done | — | — |
| 36 | Page purpose (11 fields) | Done | primary entity + supporting topics added to the page map | — |
| 37 | Internal linking model (9 relation types) | Done | now has parent/child, guide->product, comparison->product, location->service, category->product, problem->solution, audience->product, service->guide | — |
| 38 | Topical authority model | Done | entity/audience/commercial-vs-informational coverage feeds each topic's `authority_level` — Sheet column | — |
| 39 | Near-duplicate intent | Done | — | — |
| 40 | Do not over-cluster | Done | — | — |
| 41 | Do not over-split | Partial | curated-abbreviation pairs no longer split without AI; other synonym clusters still do | AI grouping step working |
| 42 | Cluster split test | Partial | SERP questions unanswered | SERP data (Semrush API) |
| 43 | Cluster merge test | Partial | same-entity by wording + curated abbreviations; SERP question unanswered | AI grouping step working |
| 44 | Temporal analysis | Done | — | — |
| 45 | Brand vs non-brand | Done | — | — |
| 46 | Competitor / alternative intent checks | Done | `apply_business_rules` §46 — a named-competitor comparison without a matching own offer goes to REVIEW | — |
| 47 | Local SEO logic | Done | §47 — a location cluster with no listed service area goes to REVIEW instead of NEW URL | — |
| 48 | E-commerce logic | Done | §48 — buying guide / attribute-filter / subcategory page types from the keyword + modifier mix | — |
| 49 | Service business logic | Done | §49 — service x industry page type + "cover as a section" call when demand is thin | — |
| 50 | Informational / publisher logic | Done | §50 — refresh-not-new-page note for temporal informational clusters on a publisher site | — |
| 51 | SaaS logic | Done | §51 — feature / integration / documentation page types from keyword wording | — |
| 52 | Multi-industry / multi-service logic | Done | shares the §49 service x industry rule | — |
| 53 | Keyword-to-URL decision (8 options) | Done | — | — |
| 54 | Master keyword dataset (50 fields) | Partial | language/entity relationship/geo served/primary score/topical authority/existing-URL traffic/programmatic flag added; serp_* fields and page_cohesion_score still missing; still needs Google Sheets for the FULL dataset (a 7-column fallback slide now covers the no-Sheets case, §66-B) | SERP data (Semrush API) |
| 55 | Cluster output (19 fields) | Partial | audience/geography/difficulty/search demand/business value/target evidence added; SERP evidence per cluster still missing | SERP data (Semrush API) |
| 56 | Page map output | Done | — | — |
| 57 | Content gap output (12 fields) | Done | business relevance, competitor evidence, suggested URL, supporting keywords, reason — Sheet "Content Gaps" tab | — |
| 58 | Cannibalization output | Partial | similarity score + source added; true SERP-overlap field still missing | SERP data (Semrush API) |
| 59 | Programmatic output (11 fields) | Partial | dimensions/example queries/demand/relevance/uniqueness/content requirements/risk/recommendation built; SERP validation explicitly marked unavailable | SERP data (Semrush API) |
| 60 | Explainability | Done | — | — |
| 61 | Confidence model | Done | — | — |
| 62 | Human-in-the-loop review queue (10 types) | Done | added Programmatic opportunity, Conflicting signals, and Possible same-need clusters (§14) review types | — |
| 63 | Learning system | Partial | approvals/outcomes now stored (`keyword_decision_history`, one row per cluster, upserted every report run) and feed §31's recalibration; `outcome` itself is a manual DB flag — there's no review-queue UI yet to set accepted/rejected from the report | Review-queue UI |
| 64 | Anti-bias rules | Done | — | — |
| 65 | Final decision framework | Partial | SERP step | SERP data (Semrush API) |
| 66 | Required final report A-K | Done | B now always reaches the report (Sheet tab, or a fallback slide when Sheets isn't connected); C has subtopic/intent levels | — |
| 67 | Quality control (14 checks) | Partial | checks 3 and 11 not checked (SERP), 12 delegated | SERP data (Semrush API) |
| 68 | Operating principle | Done | — | — |
| 69 | Final principle | Done | — | — |

## Real-report verification
"Done" above also needs the row to hold up on a real client report, not only on test data. Replays kept as
regression cases:

- **Geopits (1), 2026-09-24** (no cluster file, real sitemap of 284 URLs, old URLs that 301): found and fixed
  §53 REDIRECT on already-redirecting URLs, §23 blog / vs-article chosen as a service cluster's target, §43
  merges on one shared word, §22 "Category Page" on a service business, §3 "E-commerce" from the company's
  own `/products/<name>` pages, §62 review queue of 497 items. After the fix: 0 REDIRECTs, every service
  cluster on a `/service/` page, model = Product/Service/B2B/Publisher, review queue 10 items.
  Tests: `test_keyword_spec_coverage.py` (`test_own_product_pages_are_not_e_commerce`,
  `test_redirect_only_for_error_pages_never_for_existing_301s`,
  `test_service_cluster_never_targets_a_blog_just_because_it_ranks`, `test_one_shared_word_does_not_merge_clusters`,
  `test_review_queue_skips_low_priority_and_caps_each_type`,
  `test_bare_concept_is_not_a_core_topic_and_service_business_gets_service_page`).
- **Fixed 2026-09-24 (abbreviation-aware merge):** the exact case above — "database support services" /
  "remote dba services" now merges without the AI grouping step, via a small curated abbreviation dictionary
  (`keyword_intelligence_service.expand_abbreviations`: dba/hr/it/crm/erp/cms/seo/ppc/qa/ux/ui/b2b/b2c/saas/pos/hvac)
  read into `_same_topic`'s token overlap. Narrower than real synonym/embedding matching — a genuine synonym pair
  outside this curated list (e.g. "cheap" / "affordable") still needs the AI step. Test:
  `test_abbreviation_expansion_catches_the_documented_known_limit`.

## Change log
- 2026-09-24 `d51d6a8` ranking-first targets, relevance-first tables, own-brand family, other-website intent.
- 2026-09-24 `401bea0` topic map, opportunity roadmap, funnel/audience, Sheet master columns.
- 2026-09-24 `289e50a` same-page merge, category split, junk filter, names, vendor pricing.
- 2026-09-24 `d2df10b` §3, §5, §6, §7, §8 mixed, §22, §24/§58, §25/§57, §29, §32, §36, §45, §53, §54 fields, §56 shown, §59, §62, §67.
- 2026-09-24 `4e8ec8c` real-report fixes from Geopits (1): §3, §22, §23, §43, §53, §62.
- 2026-09-24 `20e7cc8` `keyword_site_model.py` + `keyword_strategy_depth.py`: §3, §4, §6, §11, §12, §14, §18, §19,
  §20, §32, §36, §37, §38, §46-§52, §66 -> Done; code-buildable part of §23, §24, §27, §54, §55, §58, §59 finished.
- 2026-09-24 curated abbreviation dictionary (`expand_abbreviations`) fixes the documented "dba" / "database"
  merge known-limit without needing the AI grouping step or the (ruled-out) Semrush API: §17, §41, §43.
- 2026-09-24 `db59704` per-page authority (`_target_page_authority`) grouped from the Backlinks export already
  parsed, by target URL — no new data source. Closes the last code-buildable piece of §23.
- 2026-09-24 (this change) `keyword_history_service.py` (new): per-cluster decision history stored on
  `clients.keyword_decision_history` (migration `a4c8e2f61d90`), upserted every report run; `recalibrate()` nudges
  §31 opportunity scoring once a (cluster_type, business_rule) group has 5+ manually-judged outcomes. No review UI
  this pass (user's call) — `outcome` is a manual DB flag, so this is inert until someone sets one. §31 -> Done,
  §63 Blocked -> Partial. 537 tests pass (5 new).
