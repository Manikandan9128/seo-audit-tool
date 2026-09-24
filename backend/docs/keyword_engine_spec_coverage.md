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

**Strict count (every point inside a section must be built for Done): 24 Done · 40 Partial · 5 Blocked — of 69.** Of the Partial ones, 29 can be finished in code; 11 need SERP data, the AI grouping step or stored history.

| § | Section | Status | What's missing | Needs |
|---|---|---|---|---|
| 1 | Primary objective (full intelligence model) | Partial | SERP intent, semantic similarity outputs | SERP data (Semrush API) |
| 2 | Core principle, answer Q1-Q7 per keyword | Partial | Q5 (do SERPs treat them as same intent) | SERP data (Semrush API) |
| 3 | Website understanding (business model, entity graph) | Partial | no entity graph object linking products/services/audiences/use cases | Code |
| 4 | Website architecture analysis (14 fields per URL) | Partial | only URL type + live status; no per-URL entity/topic/audience/intent/funnel/depth | Code |
| 5 | Keyword normalization | Done | — | — |
| 6 | Entity extraction + entity relationship | Partial | entity type done; entity relationship (product->category, service->industry...) not built | Code |
| 7 | Modifier extraction | Done | — | — |
| 8 | Search intent (primary, secondary, confidence, mixed) | Done | — | — |
| 9 | User need | Done | — | — |
| 10 | Funnel stage | Done | — | — |
| 11 | Audience | Partial | from keyword wording only; not inferred from site/page content/competitor pages | Code |
| 12 | Geographic intent | Partial | service area known at country level only | Code |
| 13 | Semantic relationship (embeddings) | Blocked | no similarity score | AI grouping step working |
| 14 | Search intent relationship (intent similarity) | Partial | grouping by intent family; no intent-similarity score | Code |
| 15 | SERP analysis / overlap | Blocked | no SERP data | SERP data (Semrush API) |
| 16 | Page cohesion score | Blocked | needs SERP overlap | SERP data (Semrush API) |
| 17 | Same page vs separate page | Partial | synonym-only clusters not merged when AI grouping fails; SERP criterion missing | AI grouping step working |
| 18 | Topic hierarchy (root->entity->parent->subtopic->intent->cluster) | Partial | parent->cluster only; no subtopic/intent levels | Code |
| 19 | Primary keyword selection | Partial | ordering rule, not all 10 factors (SERP fit, page fit, conversion potential) | Code |
| 20 | Primary keyword score (normalized components) | Partial | no normalized primary-keyword score | Code |
| 21 | Business relevance gate | Done | — | — |
| 22 | Page type classification | Done | — | — |
| 23 | Existing URL mapping (11 checks) | Partial | no content depth, internal links, page traffic, authority, conversion role; SERP alignment | Code |
| 24 | Cannibalization detection | Partial | no SERP-overlap / similar-content / similar-title signals; GSC part needs Search Console data | Code |
| 25 | Content gap detection | Done | — | — |
| 26 | Competitor gap analysis | Done | — | — |
| 27 | Programmatic SEO detection | Partial | 'search engines would rank each page' needs SERP | SERP data (Semrush API) |
| 28 | Clustering algorithm (10 signals) | Partial | no SERP / semantic / audience / geo similarity in scoring | SERP data (Semrush API) |
| 29 | Cluster types | Done | — | — |
| 30 | Cluster confidence | Done | — | — |
| 31 | Opportunity score (+ recalibration) | Partial | weights configurable; recalibration from history not possible | Stored history |
| 32 | Difficulty interpretation | Partial | uses KD + own DR only; not topical authority, SERP composition, competitor quality | Code |
| 33 | Search volume interpretation | Done | — | — |
| 34 | SERP intent validation | Blocked | no SERP data | SERP data (Semrush API) |
| 35 | Intent changes by modifier | Done | — | — |
| 36 | Page purpose (11 fields) | Partial | missing primary entity, supporting topics | Code |
| 37 | Internal linking model (9 relation types) | Partial | has parent/child, guide->product, comparison->product, location->service; missing category->product, problem->solution, audience->product, service->guide | Code |
| 38 | Topical authority model | Partial | page-coverage only; not entity/audience/commercial-vs-informational coverage | Code |
| 39 | Near-duplicate intent | Done | — | — |
| 40 | Do not over-cluster | Done | — | — |
| 41 | Do not over-split | Partial | synonym clusters on one page stay separate without AI | AI grouping step working |
| 42 | Cluster split test | Partial | SERP questions unanswered | SERP data (Semrush API) |
| 43 | Cluster merge test | Partial | same-entity by wording only; SERP question unanswered | AI grouping step working |
| 44 | Temporal analysis | Done | — | — |
| 45 | Brand vs non-brand | Done | — | — |
| 46 | Competitor / alternative intent checks | Partial | no legal/brand-consideration or 'actual alternative offering' check | Code |
| 47 | Local SEO logic | Partial | no unique-local-value check | Code |
| 48 | E-commerce logic | Partial | no category/subcategory/product/attribute rule set | Code |
| 49 | Service business logic | Partial | service page type only; no service x industry / process / eligibility handling | Code |
| 50 | Informational / publisher logic | Partial | no freshness/depth/monetization handling | Code |
| 51 | SaaS logic | Partial | no feature vs product vs integration vs documentation split | Code |
| 52 | Multi-industry / multi-service logic | Partial | no service x industry architecture check | Code |
| 53 | Keyword-to-URL decision (8 options) | Done | — | — |
| 54 | Master keyword dataset (50 fields) | Partial | missing language, serp_*, similarity scores, page_cohesion_score, existing_url_traffic, programmatic_flag; Sheet needs Google login | Code |
| 55 | Cluster output (19 fields) | Partial | missing audience, geography, difficulty, search demand, business value, SERP evidence per cluster | Code |
| 56 | Page map output | Done | — | — |
| 57 | Content gap output (12 fields) | Partial | missing business relevance, competitor evidence, suggested URL, supporting keywords, reason | Code |
| 58 | Cannibalization output | Partial | missing SERP overlap and similarity fields | Code |
| 59 | Programmatic output (11 fields) | Partial | missing SERP validation; dimensions/risk only as a sentence | Code |
| 60 | Explainability | Done | — | — |
| 61 | Confidence model | Done | — | — |
| 62 | Human-in-the-loop review queue (10 types) | Partial | missing conflicting-SERP and programmatic-opportunity review types | Code |
| 63 | Learning system | Blocked | needs stored approvals + outcomes | Stored history |
| 64 | Anti-bias rules | Done | — | — |
| 65 | Final decision framework | Partial | SERP step | SERP data (Semrush API) |
| 66 | Required final report A-K | Partial | B (keyword master) only when Google Sheets is connected; C lacks subtopic/intent levels | Code |
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
- Known limit on the same replay: with the AI grouping step not running, same-need clusters that share no
  words ("database support services" / "remote dba services") stay separate and are marked
  EXISTING URL — SECONDARY TARGET on one page instead of merged. The AI step is what merges those.

## Change log
- 2026-09-24 `d51d6a8` ranking-first targets, relevance-first tables, own-brand family, other-website intent.
- 2026-09-24 `401bea0` topic map, opportunity roadmap, funnel/audience, Sheet master columns.
- 2026-09-24 `289e50a` same-page merge, category split, junk filter, names, vendor pricing.
- 2026-09-24 `d2df10b` §3, §5, §6, §7, §8 mixed, §22, §24/§58, §25/§57, §29, §32, §36, §45, §53, §54 fields, §56 shown, §59, §62, §67.
- 2026-09-24 (this change) real-report fixes from Geopits (1): §3, §22, §23, §43, §53, §62.
