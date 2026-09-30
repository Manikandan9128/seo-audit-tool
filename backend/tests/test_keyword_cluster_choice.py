"""Keyword clusters are an explicit user choice (2026-09-30): 'manual' (the
uploaded file only) or 'ai' (AI clusters only). No default, no fallback, no
mixing."""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api.routes import site_audit
from app.services import keyword_cluster_pipeline as pipeline
from app.services import report_options


class _Db:
    def __init__(self, types):
        self._types = types

    def query(self, *a):
        return self

    def filter(self, *a):
        return self

    def distinct(self):
        return self

    def all(self):
        return [(t,) for t in self._types]


def test_missing_or_unknown_choice_is_refused():
    for bad in (None, "", "auto", "semrush"):
        with pytest.raises(HTTPException) as exc:
            site_audit._require_keyword_cluster_choice(bad, uuid.uuid4(), _Db([]))
        assert exc.value.status_code == 400


def test_manual_needs_an_uploaded_cluster_file():
    with pytest.raises(HTTPException):
        site_audit._require_keyword_cluster_choice("manual", uuid.uuid4(), _Db(["keyword_gap"]))
    assert site_audit._require_keyword_cluster_choice("manual", uuid.uuid4(), _Db(["keyword_cluster_manual"])) == "manual"


def test_ai_needs_keyword_data_and_never_silently_uses_the_file():
    with pytest.raises(HTTPException):
        site_audit._require_keyword_cluster_choice("ai", uuid.uuid4(), _Db(["keyword_cluster_manual"]))
    assert site_audit._require_keyword_cluster_choice("ai", uuid.uuid4(), _Db(["keyword_gap"])) == "ai"


def test_thread_local_choice_resets():
    report_options.set_keyword_cluster_mode("ai")
    assert report_options.keyword_cluster_mode() == "ai"
    report_options.set_keyword_cluster_mode(None)
    assert report_options.keyword_cluster_mode() is None


def _rows():
    return [
        {"keyword": "alpha service", "search_volume": 10, "cluster": "Semrush Native"},
        {"keyword": "beta service", "search_volume": 20},
    ]


def test_manual_mode_uses_only_the_file_and_never_ai_clusters_the_rest():
    rows = _rows()
    with patch.object(pipeline, "_autocluster_unmatched_rows") as auto, \
            patch.object(pipeline, "_build_candidate_clusters") as build:
        pipeline.build_final_keyword_clusters(
            rows, "Acme", None, None, manual_cluster_map={"alpha service": {"cluster": "Alpha"}}, cluster_mode="manual",
        )
    auto.assert_not_called()
    build.assert_not_called()
    by_kw = {r["keyword"]: r for r in rows}
    assert by_kw["alpha service"]["cluster"] == "Alpha"
    assert by_kw["alpha service"]["cluster_source"] == "manual"
    assert by_kw["beta service"]["cluster"] == pipeline._NEEDS_REVIEW_CLUSTER_LABEL


def test_ai_mode_ignores_manual_file_and_semrush_native_labels():
    rows = _rows()
    with patch.object(pipeline, "_apply_manual_clusters") as manual, \
            patch.object(pipeline, "_build_candidate_clusters") as build:
        pipeline.build_final_keyword_clusters(
            rows, "Acme", None, None, manual_cluster_map={"alpha service": {"cluster": "Alpha"}}, cluster_mode="ai",
        )
    manual.assert_not_called()
    build.assert_called_once()
    assert rows[0]["cluster"] != "Semrush Native"
