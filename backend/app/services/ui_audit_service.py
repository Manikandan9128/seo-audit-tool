"""UI-Level Fixes analysis pipeline, Part A3+A4 (2026-09-25 spec): the AI
pass over the real capture from ui_audit_capture.py, and the code-only
validation gate that runs on its output before anything reaches a slide
or the full issue sheet.

Distinct from ux_findings_service.py's older generate_ui_fixes_from_
screenshot — that pass used one desktop screenshot and a fixed 3-6 item
cap; this one uses all 4 captured images (desktop/mobile x first-screen/
full-page) plus measured page_facts, and returns up to 25 evidence-backed
issues. ux_findings_service.py's manual-notes path (generate_ux_findings,
Conversion Opportunities) is untouched — out of this spec's scope."""

import json
import logging
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text_with_images

logger = logging.getLogger(__name__)

_OUTCOME_TAGS = {"clarity", "cta", "friction", "trust", "mobile_access", "speed_focus"}
_PRIORITIES = ("High", "Medium", "Low")
_DEVICES = ("Both", "Desktop", "Mobile")
_PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}

_MAX_ISSUES = 25

_PROMPT_TEMPLATE = """You are a UX/conversion consultant writing part of a client-facing SEO/web audit report \
for {client_name} ({website_url}). {company_context}

You are given 4 real screenshots of the homepage: the desktop first screen, the full desktop page, the mobile \
first screen, and the full mobile page — in that order. You are also given page_facts, real measurements taken \
directly from the page's own HTML/CSS (not a guess from the images) for the same two viewports:

page_facts:
{page_facts_json}

Return EVERY real UI/UX issue you can support with evidence, up to {max_issues}. Do not pad the list — if only \
4 real issues exist, return 4. Each issue MUST cite measured evidence from page_facts or a clearly visible \
screenshot detail (a number, exact on-page text, or position). Never report an issue page_facts contradicts — \
for example, do not claim two elements overlap unless page_facts.overlaps actually lists that overlap, and do \
not claim a CTA is hidden/missing if page_facts shows it present and visible. Write for a business reader, not \
a designer. Never promise specific uplift percentages or guaranteed outcomes.

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "issues": [
    {{
      "title": string,        // short plain-language problem, max ~40 characters, e.g. "Visitors can't tell what we do"
      "evidence": string,     // 1 sentence with the measured fact, max ~120 characters
      "where": string,        // location on the page, max ~50 characters, e.g. "Hero section (first screen)"
      "fix": string,          // concrete action, max ~150 characters
      "outcome_tags": [string, ...],  // any of: clarity, cta, friction, trust, mobile_access, speed_focus
      "priority": "High" | "Medium" | "Low",
      "device": "Both" | "Desktop" | "Mobile",
      "impact_score": number  // 1-10
    }}
  ]
}}

Priority guidance: High = blocks understanding of the offer or the main enquiry path. Medium = weakens trust, \
focus, speed or compliance. Low = minor usability polish."""


def _company_context(company_profile: dict | None) -> str:
    if not company_profile:
        return "No company profile was supplied — judge issues on general usability/clarity grounds only."
    name = company_profile.get("company_name") or ""
    description = company_profile.get("description") or ""
    buyers = ", ".join(company_profile.get("primary_buyers") or []) or None
    parts = [p for p in (
        f"They sell: {description}" if description else None,
        f"Their ideal customer: {buyers}" if buyers else None,
    ) if p]
    return " ".join(parts) or f"Company: {name}" if name else ""


def generate_ui_audit_issues(
    client_name: str, website_url: str, company_profile: dict | None,
    page_facts: dict, images: list[tuple[bytes, str]],
) -> dict:
    """Part A3. `images` must be exactly the 4 (bytes, mime_type) pairs in
    the order the prompt describes: desktop first-screen, desktop
    full-page, mobile first-screen, mobile full-page. Returns
    {"issues": [...]} (raw, NOT yet validated — see validate_ui_audit_
    issues) or {"error": str}."""
    if not images:
        return {"error": "No screenshots captured — cannot run the UI audit vision pass."}
    prompt = _PROMPT_TEMPLATE.format(
        client_name=client_name, website_url=website_url, company_context=_company_context(company_profile),
        page_facts_json=json.dumps(page_facts, separators=(",", ":"))[:12000], max_issues=_MAX_ISSUES,
    )
    try:
        raw, _provider = generate_text_with_images(prompt, images, max_tokens=4096)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return {"error": "AI did not return valid JSON", "raw": raw[:500]}
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {"error": "AI did not return valid JSON", "raw": raw[:500]}
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        return {"error": "AI response missing an 'issues' array", "raw": raw[:500]}
    return {"issues": data["issues"]}


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def _claims_overlap(issue: dict) -> bool:
    """Heuristic for the spec's explicit example of an evidence/page_facts
    conflict: "do not claim an overlap page_facts shows does not exist."
    An issue is treated as making an overlap claim when it's tagged
    "friction" (the outcome theme this validation exists to gate) or its
    own text uses overlap language directly."""
    if "friction" in (issue.get("outcome_tags") or []):
        return True
    haystack = f"{issue.get('title', '')} {issue.get('evidence', '')}".lower()
    return any(w in haystack for w in ("overlap", "overlaps", "covers", "covering", "hidden behind", "blocks", "blocked by"))


def validate_ui_audit_issues(issues: list, page_facts: dict | None) -> dict:
    """Part A4 — code, not AI. Drops issues with empty evidence or an
    unsupported overlap claim, deduplicates issues describing the same
    element, sorts by priority then impact_score, and clamps every field
    to the spec's shape/length so a malformed AI response can never reach
    a slide or the issue sheet with a missing/oversized field. Returns
    {"issues": [...], "total_count": int, "counts_by_priority": {...}}."""
    page_facts = page_facts or {}
    has_real_overlaps = bool(page_facts.get("overlaps")) or any(
        (page_facts.get(device) or {}).get("overlaps") for device in ("desktop", "mobile")
    )

    cleaned: list[dict] = []
    for raw in issues or []:
        if not isinstance(raw, dict):
            continue
        evidence = (raw.get("evidence") or "").strip()
        if not evidence:
            continue
        priority = raw.get("priority") if raw.get("priority") in _PRIORITIES else "Medium"
        device = raw.get("device") if raw.get("device") in _DEVICES else "Both"
        try:
            impact_score = max(1, min(10, round(float(raw.get("impact_score", 5)))))
        except (TypeError, ValueError):
            impact_score = 5
        tags = [t for t in (raw.get("outcome_tags") or []) if t in _OUTCOME_TAGS]
        issue = {
            "title": _truncate(raw.get("title") or "", 45),
            "evidence": _truncate(evidence, 130),
            "where": _truncate(raw.get("where") or "", 55),
            "fix": _truncate(raw.get("fix") or "", 160),
            "outcome_tags": tags,
            "priority": priority,
            "device": device,
            "impact_score": impact_score,
        }
        if _claims_overlap(issue) and not has_real_overlaps:
            logger.info("UI audit issue dropped — claims an overlap page_facts doesn't show: %r", issue["title"])
            continue
        cleaned.append(issue)

    # Deduplicate issues describing the same element: same normalized
    # `where` plus meaningfully overlapping title wording. Keeps the
    # FIRST occurrence (the AI's own presented order) — sorting happens
    # after this, on the deduplicated set.
    deduped: list[dict] = []
    seen: list[tuple[str, set]] = []
    for issue in cleaned:
        where_key = re.sub(r"[^a-z0-9]+", " ", issue["where"].lower()).strip()
        title_tokens = set(re.findall(r"[a-z0-9]+", issue["title"].lower()))
        is_dup = False
        for seen_where, seen_tokens in seen:
            if seen_where != where_key:
                continue
            overlap = len(title_tokens & seen_tokens) / max(1, min(len(title_tokens), len(seen_tokens)))
            if overlap >= 0.5:
                is_dup = True
                break
        if is_dup:
            continue
        seen.append((where_key, title_tokens))
        deduped.append(issue)

    deduped.sort(key=lambda i: (_PRIORITY_RANK[i["priority"]], -i["impact_score"]))
    deduped = deduped[:_MAX_ISSUES]

    counts_by_priority = {p: sum(1 for i in deduped if i["priority"] == p) for p in _PRIORITIES}
    return {"issues": deduped, "total_count": len(deduped), "counts_by_priority": counts_by_priority}
