"""Universal SEO Keyword engine — §31/§63 stored decision history and
opportunity-score recalibration.

There is no review UI yet (2026-09-24 decision: backend-only this pass), so
`outcome` on a history entry is never set by report generation itself — it
is a manual DB flag someone sets directly against
`clients.keyword_decision_history` (a JSONB dict keyed by cluster_key,
{cluster_key: {..., "outcome": "accepted" | "rejected" | None}}).
`recalibrate()` only nudges scoring once a (cluster_type, business_rule)
group has enough outcomes recorded — with none set yet, every multiplier is
neutral (1.0) and behavior is unchanged. This is the mechanism, not a
finished learning loop; see docs/keyword_engine_spec_coverage.md §31/§63.
"""

import re
from collections import defaultdict

_MIN_GROUP_SIZE = 5
_MAX_HISTORY_ENTRIES = 2000


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def cluster_key(summary: dict) -> str:
    """Stable across regenerations — the cluster's base name and topic
    rarely change even when its cluster_id/primary keyword does."""
    base_name = (summary.get("name") or "").split(" — ")[0]
    return f"{_slug(summary.get('parent_topic') or '')}::{_slug(base_name)}"


def record_run(history: dict | None, strategy: dict, generated_at: str) -> dict:
    """Upserts this run's decision for every cluster in `strategy` into
    `history` (keyed by cluster_key) — decision fields overwrite, `outcome`
    is preserved from whatever was there before. Returns the updated dict;
    caller persists it (`client.keyword_decision_history = ...`)."""
    history = dict(history or {})
    for c in (strategy or {}).get("clusters") or []:
        key = cluster_key(c)
        prior = history.get(key) or {}
        history[key] = {
            "cluster_name": c.get("name"), "parent_topic": c.get("parent_topic"),
            "cluster_type": c.get("cluster_type"), "business_rule": c.get("business_rule"),
            "decision": c.get("decision"), "roadmap_priority": c.get("priority"),
            "opportunity": c.get("opportunity"), "confidence": c.get("confidence"),
            "last_seen_at": generated_at, "outcome": prior.get("outcome"),
        }
    if len(history) > _MAX_HISTORY_ENTRIES:
        # Drop the oldest-seen entries first — a client re-running reports
        # for years shouldn't grow this column unbounded.
        for key in sorted(history, key=lambda k: history[k].get("last_seen_at") or "")[:len(history) - _MAX_HISTORY_ENTRIES]:
            del history[key]
    return history


def recalibrate(history: dict | None) -> dict[tuple, float]:
    """§31 — groups judged entries by (cluster_type, business_rule) and
    returns a multiplier per group: 0.8x at 0% accepted, 1.0x at 100%
    accepted, linear between. A group with fewer than _MIN_GROUP_SIZE
    judged entries (or none at all, the default state) gets 1.0 — neutral,
    no behavior change until real outcomes accumulate."""
    groups: dict[tuple, list[str]] = defaultdict(list)
    for entry in (history or {}).values():
        outcome = entry.get("outcome")
        if outcome not in ("accepted", "rejected"):
            continue
        groups[(entry.get("cluster_type"), entry.get("business_rule"))].append(outcome)
    multipliers: dict[tuple, float] = {}
    for group, outcomes in groups.items():
        if len(outcomes) < _MIN_GROUP_SIZE:
            continue
        accept_rate = outcomes.count("accepted") / len(outcomes)
        multipliers[group] = round(0.8 + 0.2 * accept_rate, 3)
    return multipliers


def multiplier_for(summary: dict, multipliers: dict[tuple, float]) -> float:
    return multipliers.get((summary.get("cluster_type"), summary.get("business_rule")), 1.0)
