"""Up-front AI usage estimate for one report (2026-09-29).

Before Preview/Download on a paid provider (Claude, OpenRouter, Browser Use)
the page shows the heavy AI steps with estimated input/output tokens and
cost; steps over the warning threshold say why, steps over the hard safety
limit can't run, and the user unticks any step they don't want (skipped via
text_ai_client.ai_step_skipped). Limits and prices live in one place,
app.integrations.ai_usage (AI_CONFIG / PRICES). Estimates are deliberately
rough — sized from what this client actually uploaded plus typical prompt
and answer sizes (thinking included for reasoning providers) — and are
always labelled as estimates.
"""

import uuid

from sqlalchemy.orm import Session

from app.integrations import ai_usage
from app.integrations.ai_usage import AI_CONFIG, PROVIDER_REASONS
from app.integrations.text_ai_client import CLAUDE_MODEL
from app.models.semrush_import import SemrushImport

# Providers that bill per use. Groq/Gemini free tiers never show the popup.
PAID_PROVIDERS = {"claude", "openrouter", "browser_use"}

# key -> label. Order is the order shown in the popup.
HEAVY_AI_STEPS = {
    "aeo_geo": "AEO / GEO analysis (GeoPulse)",
    "ui_audit": "UI/UX screenshot audit",
    "core_problem": "Core Problem",
    "competitor_narratives": "Competitor analysis narratives",
    "next_steps": "Tailored Next Steps",
}

_IMAGE_TOKENS = 1600  # one screenshot, after the 8000px cap
_MAX_COMPETITORS = 5  # same cap as site_audit._generate_competitor_narratives
_OTHER_STEPS = (40_000, 30_000)  # company overview, insights, keyword steps


def _step_tokens(client_id: uuid.UUID, db: Session, reasoning: bool) -> dict[str, tuple[int, int, str]]:
    """(input, output, why) per heavy step that applies to this client.
    Reasoning providers spend extra output tokens thinking first."""
    imports = db.query(SemrushImport).filter(SemrushImport.client_id == client_id).all()
    geopulse_chars = sum(
        len(row.get("raw_text") or "")
        for imp in imports if imp.import_type == "geopulse" and imp.is_own_site
        for row in (imp.parsed_data or {}).get("rows", [])
    )
    competitors = {
        (imp.domain_label or "").strip().lower()
        for imp in imports if not imp.is_own_site and (imp.domain_label or "").strip()
    }
    n_comp = min(len(competitors), _MAX_COMPETITORS)
    think = 1.0 if reasoning else 0.5

    steps = {
        "ui_audit": (4 * _IMAGE_TOKENS + 3500, int(9000 * think),
                     "4 homepage screenshots (desktop and mobile) are sent to the model"),
        "core_problem": (7000, int(5000 * think), "summarizes findings from every section of the report"),
        "next_steps": (8000, int(7000 * think), "writes recommendations across every Next Steps category"),
    }
    if geopulse_chars:
        steps["aeo_geo"] = (geopulse_chars // 4 + 1600, int(5000 * think),
                            f"the GeoPulse export is about {geopulse_chars // 4:,} tokens of text")
    if n_comp:
        steps["competitor_narratives"] = (1500 + 2500 * n_comp, int((3000 + 3000 * n_comp) * think),
                                          f"{n_comp} competitor{'s' if n_comp != 1 else ''} analysed in one request")
    return steps


def estimate_report_ai_usage(client_id: uuid.UUID, db: Session, provider: str, claude_model: str | None) -> dict:
    model = (claude_model or CLAUDE_MODEL) if provider == "claude" else None
    tokens = _step_tokens(client_id, db, provider in PROVIDER_REASONS)
    steps = []
    for key, label in HEAVY_AI_STEPS.items():
        if key not in tokens:
            continue
        t_in, t_out, why = tokens[key]
        total = t_in + t_out
        over_warning = total > AI_CONFIG["step_warning_tokens"]
        over_hard = total > AI_CONFIG["step_hard_limit_tokens"]
        steps.append({
            "key": key, "label": label, "input_tokens": t_in, "output_tokens": t_out, "total_tokens": total,
            "cost_usd": ai_usage.cost_usd(provider, model, t_in, t_out),
            "over_warning": over_warning, "over_hard_limit": over_hard,
            "reason": f"High usage: {why}." if over_warning else None,
        })
    other_in, other_out = _OTHER_STEPS
    total_all = sum(s["total_tokens"] for s in steps) + other_in + other_out
    return {
        "provider": provider,
        "provider_label": ai_usage.PROVIDER_LABELS[provider],
        "model": model,
        "paid": provider in PAID_PROVIDERS,
        "per_run_billing": ai_usage.price_for(provider, model) is None,
        "steps": steps,
        "other": {
            "label": "Other AI steps (always run)", "input_tokens": other_in, "output_tokens": other_out,
            "total_tokens": other_in + other_out, "cost_usd": ai_usage.cost_usd(provider, model, other_in, other_out),
        },
        "total_tokens": total_all,
        "needs_confirmation": provider in PAID_PROVIDERS and (
            any(s["over_warning"] for s in steps) or total_all > AI_CONFIG["report_warning_tokens"]
        ),
        "limits": {
            "step_warning_tokens": AI_CONFIG["step_warning_tokens"],
            "step_hard_limit_tokens": AI_CONFIG["step_hard_limit_tokens"],
            "report_warning_tokens": AI_CONFIG["report_warning_tokens"],
            "report_hard_limit_tokens": AI_CONFIG["report_hard_limit_tokens"],
        },
    }


HARD_LIMIT_MESSAGE = (
    "This analysis exceeds the configured maximum token limit. Reduce the analysis scope or increase the "
    "maximum limit before continuing."
)


def hard_limit_violation(estimate: dict, skipped: list[str]) -> str | None:
    """The hard-limit message when the steps that WILL run exceed a hard
    safety limit (a single step, or the whole report), else None."""
    running = [s for s in estimate["steps"] if s["key"] not in set(skipped)]
    over = [s["label"] for s in running if s["over_hard_limit"]]
    if over:
        return f"{HARD_LIMIT_MESSAGE} Over the per-step limit: {', '.join(over)}."
    total = sum(s["total_tokens"] for s in running) + estimate["other"]["total_tokens"]
    if total > estimate["limits"]["report_hard_limit_tokens"]:
        return f"{HARD_LIMIT_MESSAGE} Estimated total: {total:,} tokens."
    return None
