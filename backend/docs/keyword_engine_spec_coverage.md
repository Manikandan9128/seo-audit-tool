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

| § | Requirement | Status | Where | Notes |
|---|---|---|---|---|
| 1 | Complete SEO intelligence model from site + keywords + metrics | Done | all of the above | SERP inputs blocked (§15) |
| 2 | Never cluster by lexical similarity alone; answer Q1–Q7 | Done | pipeline + strategy | Q5 (SERP) blocked |
| 3 | Website understanding: business model, entities | Partial | `infer_business_model`, `site_entity_summary`, company overview | Business model from site URL structure + target market; no full entity *graph* object |
| 4 | Architecture analysis: every URL's type, entity, topic, audience, intent, funnel… | Partial | `classify_page_type`, `is_live_target_page`, page map | URL type + live status only; per-URL topic/audience/funnel not built (would need reading every page) |
| 5 | Normalization: plural, order, spelling variants, abbreviations; no auto synonym merge | Done | `normalize_keyword`, `canonical_spelling` | British/US spelling pairs; synonyms never merged |
| 6 | Entity + entity type per keyword | Done | `entity_type`, row `entity_type` | From wording only; "Product or Service" when unclear (sent to review) |
| 7 | Modifier extraction + modifier type | Done | `normalize_keyword`, `modifier_types` | |
| 8 | Intent: primary, secondary, confidence, mixed; problem/support | Done | `detect_intent`, `secondary_intent`, `mixed_intent` | |
| 9 | User need | Done | `detect_intent` user_need | |
| 10 | Funnel stage | Done | `funnel_stage` | |
| 11 | Audience | Done | `keyword_audience` | From keyword wording only (no SERP/site inference) |
| 12 | Geography; location pages only when served | Partial | geo words, `assign_geo_status`, Location page type | Service area checked at country level only (company overview target country); city/region service areas not known |
| 13 | Semantic similarity (embeddings) | Blocked | — | Needs an embeddings service; AI clustering covers meaning without a score |
| 14 | Intent relationship | Done | rule groups by (entity, intent family) | |
| 15 | SERP analysis / overlap | Blocked | — | Semrush API deferred |
| 16 | Page cohesion score | Blocked | — | Main input is SERP overlap |
| 17 | Same page vs separate page | Done | `build_rule_groups`, `_merge_same_page_clusters`, AI phase 2/3 | |
| 18 | Topic hierarchy | Done | `build_topic_model`, "Keyword Topic Map" slide | Parent → cluster; compound nouns can land under a modifier word |
| 19–20 | Primary keyword by relevance/intent/rank, not volume | Partial | `_select_primary_secondary`, `select_primary_keyword` | Ordered by relevance → intent fit → commercial → ranking → demand; not a normalized weighted score; SERP fit blocked |
| 21 | Business relevance gate incl. competitor's proprietary product, junk | Done | classify prompt, `_rule_exclude`, `is_junk_keyword`, `vendor_product_pricing_reason` | |
| 22 | Page type (23 types) | Done | `assign_cluster_types` | Tool/Calculator, Template, Case Study, Glossary, FAQ, Comparison, Alternative, Location, Homepage/Brand, Guide, Industry/Audience, Category, Service, Product. "Programmatic page" lives on the Programmatic slide |
| 23 | Existing URL mapping: rankings first, live pages only | Partial | `ranking_page_target`, `match_existing_page_for_cluster` | Uses rankings, topic/entity overlap, page-type fit, live status; content depth, internal links, page traffic and authority are not scored |
| 24 | Cannibalization detection | Done | `detect_cannibalization` (Search Console page×query), page map overlap | |
| 25 | Content gap types | Done | `content_gap_type` | |
| 26 | Competitor gap analysis | Done | Keyword Gap slides + Sheet tabs | |
| 27 | Programmatic detection with safeguards | Done | `add_programmatic_seo_slide` | |
| 28 | Multi-signal clustering | Partial | rule groups + AI + merge test | No SERP / embedding signal |
| 29 | Cluster types | Done | `assign_cluster_types` | |
| 30 | Cluster confidence 0–100, explained | Done | `score_cluster` | Capped at 85 without SERP |
| 31 | Opportunity score, configurable weights | Done | `OPPORTUNITY_WEIGHTS`, `keyword_opportunity` | SERP weight redistributed |
| 32 | Difficulty read with site authority | Done | `_feasibility` (own DR / Authority Score) | |
| 33 | Volume ≠ priority | Done | roadmap ordering everywhere | |
| 34 | SERP intent validation | Blocked | — | Semrush API |
| 35 | Intent changes by modifier | Done | per-keyword intent | |
| 36 | Page purpose (purpose, goal, links in/out…) | Done | `build_page_map_v2`, Sheet "Page Map" | |
| 37 | Internal linking model | Done | `build_topic_model` links, Topic Map slide, Page Map | |
| 38 | Topical coverage | Done | Topic Map coverage column | |
| 39 | Near-duplicate intent | Done | normalization + same-page merge | |
| 40–41 | Don't over-cluster / over-split | Done | category-head rule, same-page merge | |
| 42–43 | Split / merge tests | Done | `distinct_needs`, `_merge_same_page_clusters` | SERP questions unanswered |
| 44 | Temporal keywords | Done | `is_temporal`, evergreen line | |
| 45 | Brand / non-brand / competitor / product / mixed | Done | `keyword_brand_type` | |
| 46 | Competitor / alternative intent | Done | comparison intent, relevance statuses | |
| 47 | Local SEO logic | Partial | Local family, Location page, geo mismatch routing | No check that a location page could carry unique local value; relies on the Local SEO Next Steps being AI-gated |
| 48–52 | E-commerce / service / publisher / SaaS / multi-industry logic | Partial | universal rules (intent family split, page types, audience) | No per-business-model rule sets; business model is detected but only used for QC check 9 |
| 53 | Keyword-to-URL decision (8 options) | Done | `assign_decisions` | |
| 54 | Master keyword dataset | Partial | Sheet client tab | Missing: language, serp_*, semantic/intent/user-need similarity, page_cohesion_score, existing_url_traffic, programmatic_flag. Sheet only exists when Google Sheets is connected |
| 55 | Cluster output | Done | slides + strategy `clusters` | SERP evidence blocked |
| 56 | Page map | Done | Sheet "Page Map", Content SEO slide | |
| 57 | Content gap output | Done | Sheet "Content Gaps" | |
| 58 | Cannibalization output + actions | Done | Sheet "Cannibalization", Content SEO line | |
| 59 | Programmatic output | Partial | Programmatic slide line: pattern, demand, risk, content needs | "SERP validation" blocked |
| 60–61 | Explainability, confidence | Done | reason lines, decision_reason | |
| 62 | Human review queue | Done | `build_review_queue`, Sheet "Review Queue", Content SEO line | |
| 63 | Learning system | Blocked | — | Needs stored human approvals + outcomes |
| 64 | Anti-bias rules | Done | across the above | |
| 65 | Final decision framework | Done | pipeline order | SERP step blocked |
| 66 | Final report A–K | Partial | Company Overview + Topic Map (A), Sheet (B), Topic Map (C), Target Keywords (D), Content SEO + Page Map (E), cannibalization (F), gaps (G), Keyword Gap (H), Programmatic (I), links (J), roadmap (K) | B depends on Google Sheets being connected |
| 67 | Quality control (14 checks) | Done | `run_quality_checks`, Sheet "Quality Checks" | Checks 3 and 11 "Not checked" (SERP) |
| 68–69 | Operating principles | Done | | |

## Change log
- 2026-09-24 `d51d6a8` ranking-first targets, relevance-first tables, own-brand family, other-website intent.
- 2026-09-24 `401bea0` topic map, opportunity roadmap, funnel/audience, Sheet master columns.
- 2026-09-24 `289e50a` same-page merge, category split, junk filter, names, vendor pricing.
- 2026-09-24 `d2df10b` §3, §5, §6, §7, §8 mixed, §22, §24/§58, §25/§57, §29, §32, §36, §45, §53, §54 fields, §56 shown, §59, §62, §67.
