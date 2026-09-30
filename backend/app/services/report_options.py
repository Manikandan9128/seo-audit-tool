"""Per-report choices the user must make explicitly (2026-09-30). Nothing
here has a default: a report request without a valid choice is refused
(400) instead of the pipeline picking a method itself. Thread-local, same
per-job lifecycle as text_ai_client's provider pin."""

import threading

from fastapi import HTTPException

KEYWORD_CLUSTER_MODES = ("manual", "ai")

_local = threading.local()


def validated_keyword_cluster_mode(mode: str | None) -> str:
    if mode not in KEYWORD_CLUSTER_MODES:
        raise HTTPException(
            status_code=400,
            detail="Choose how keyword clusters are built: keyword_cluster_mode must be 'manual' "
            "(your uploaded cluster file) or 'ai' (AI clusters from Keyword Gap, Search Console and GA4).",
        )
    return mode


def set_keyword_cluster_mode(mode: str | None) -> None:
    _local.keyword_cluster_mode = mode


def keyword_cluster_mode() -> str | None:
    return getattr(_local, "keyword_cluster_mode", None)
