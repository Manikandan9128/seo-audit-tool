"""Parses a client's own manually-clustered keyword file — an SEO
strategist's hand-built topic grouping, uploaded as the source of truth for
a report's Target Keywords clustering. When present, this OVERRIDES the
AI-based Phase 2/3 pipeline (keyword_semantic_cluster_service.py,
build_final_keyword_clusters's manual_cluster_map param) for whatever
keywords it covers — the AI pipeline only ever runs as a fallback when no
manual file has been uploaded for a client. Deliberately tiny, tolerant
format: any CSV/XLSX with a Keyword column and a Cluster column, since a
person writes this by hand, not a tool exporting a fixed schema."""

import io

import pandas as pd

_COLUMN_ALIASES = {
    "keyword": ["keyword", "keywords"],
    "cluster": ["cluster", "cluster name", "topic", "group"],
    "primary_or_secondary": ["primary/secondary", "primary or secondary", "role", "primary_secondary"],
}


def _read_table(filename: str, content: bytes) -> pd.DataFrame:
    buffer = io.BytesIO(content)
    name = filename.lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(buffer)
    try:
        return pd.read_csv(buffer, sep=None, engine="python", index_col=False)
    except Exception:
        buffer.seek(0)
        return pd.read_csv(buffer, index_col=False)


def _map_columns(df: pd.DataFrame) -> pd.DataFrame:
    lower_cols = {str(c).lower().strip(): c for c in df.columns}
    rename_map = {}
    for field, candidates in _COLUMN_ALIASES.items():
        for candidate in candidates:
            if candidate in lower_cols:
                rename_map[lower_cols[candidate]] = field
                break
    mapped = df.rename(columns=rename_map)
    keep_cols = [c for c in _COLUMN_ALIASES if c in mapped.columns]
    return mapped[keep_cols]


def parse_manual_keyword_cluster_file(filename: str, content: bytes) -> dict:
    """Returns {"rows": [{"keyword", "cluster", "primary_or_secondary"}, ...],
    "row_count": int}. Raises ValueError with a message fit to show the
    uploader directly when the file has no recognizable Keyword/Cluster
    columns — this file is handwritten, so a clear rejection beats a
    silent, wrong best-effort guess."""
    df = _read_table(filename, content)
    mapped = _map_columns(df)
    if "keyword" not in mapped.columns or "cluster" not in mapped.columns:
        raise ValueError(
            'This file needs a "Keyword" column and a "Cluster" (or "Topic"/"Group") column — '
            f"found columns: {', '.join(str(c) for c in df.columns)}."
        )

    mapped = mapped.dropna(subset=["keyword", "cluster"]).copy()
    mapped["keyword"] = mapped["keyword"].astype(str).str.strip()
    mapped["cluster"] = mapped["cluster"].astype(str).str.strip()
    mapped = mapped[(mapped["keyword"] != "") & (mapped["cluster"] != "")]

    rows: list[dict] = []
    for _, row in mapped.iterrows():
        entry: dict = {"keyword": row["keyword"], "cluster": row["cluster"]}
        pos = row.get("primary_or_secondary") if "primary_or_secondary" in mapped.columns else None
        if isinstance(pos, str) and pos.strip():
            entry["primary_or_secondary"] = "Primary" if pos.strip().lower().startswith("p") else "Secondary"
        rows.append(entry)

    # Every cluster the strategist hands off must have exactly one Primary —
    # downstream rendering and _final_cluster_acceptance_check both require
    # it. A cluster with no explicit Primary marked in the file defaults its
    # first listed keyword to Primary rather than leaving every row
    # Secondary (which _final_cluster_acceptance_check would then reject
    # outright as "no primary keyword selected").
    cluster_has_primary: set[str] = {r["cluster"] for r in rows if r.get("primary_or_secondary") == "Primary"}
    for r in rows:
        if "primary_or_secondary" not in r:
            if r["cluster"] not in cluster_has_primary:
                r["primary_or_secondary"] = "Primary"
                cluster_has_primary.add(r["cluster"])
            else:
                r["primary_or_secondary"] = "Secondary"

    return {"rows": rows, "row_count": len(rows)}
