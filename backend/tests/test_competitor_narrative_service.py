from unittest.mock import patch

from app.services.competitor_narrative_service import (
    _NARRATIVE_KEYS, _already_claimed_prompt_parts, generate_competitor_narratives_batch,
)


def test_narrative_keys_match_new_schema():
    # 2026-09-11 rewrite: single-differentiator schema, shared_advantage
    # is optional so it's deliberately not in the required-keys set.
    assert _NARRATIVE_KEYS == {"best_at", "headline", "unique_angle", "gap"}


def test_already_claimed_empty_when_nothing_to_report():
    clause, block = _already_claimed_prompt_parts({})
    assert clause == "" and block == ""


def test_already_claimed_summarizes_prior_headline_and_angle():
    clause, block = _already_claimed_prompt_parts({
        "rival.com": {"headline": "usage-based pricing", "unique_angle": ["Charges per seat, billed monthly"]},
    })
    assert clause  # non-empty: tells the model to also check against earlier chunks
    assert "rival.com" in block
    assert "usage-based pricing" in block


def test_already_claimed_skips_entries_with_nothing_useful():
    clause, block = _already_claimed_prompt_parts({"errored.com": {"error": "AI call failed"}})
    assert clause == "" and block == ""


def test_batch_threads_already_claimed_across_chunks():
    # Force 2 domains into 2 separate single-domain chunks and verify the
    # SECOND chunk's call receives the first chunk's successful result as
    # already_claimed — this is what lets shared_advantage detection work
    # across a chunk boundary instead of only within one call.
    captured_already_claimed = []

    def fake_generate_chunk(client_name, client_domain, chunk_facts, already_claimed=None):
        captured_already_claimed.append(dict(already_claimed or {}))
        domain = list(chunk_facts.keys())[0]
        return {domain: {"best_at": ["x"], "headline": f"{domain} angle", "unique_angle": ["mechanic"], "gap": "gap", "shared_advantage": None}}

    with patch("app.services.competitor_narrative_service._chunk_domains", return_value=[["a.com"], ["b.com"]]), \
         patch("app.services.competitor_narrative_service._generate_chunk", side_effect=fake_generate_chunk):
        result = generate_competitor_narratives_batch("Client", "client.com", {"a.com": {}, "b.com": {}})

    assert captured_already_claimed[0] == {}
    assert "a.com" in captured_already_claimed[1]
    assert captured_already_claimed[1]["a.com"]["headline"] == "a.com angle"
    assert result["a.com"]["headline"] == "a.com angle"
    assert result["b.com"]["headline"] == "b.com angle"


def test_batch_does_not_carry_forward_errored_domains():
    def fake_generate_chunk(client_name, client_domain, chunk_facts, already_claimed=None):
        domain = list(chunk_facts.keys())[0]
        if domain == "a.com":
            return {domain: {"error": "AI call failed"}}
        return {domain: {"already_claimed_seen": dict(already_claimed or {})}}

    with patch("app.services.competitor_narrative_service._chunk_domains", return_value=[["a.com"], ["b.com"]]), \
         patch("app.services.competitor_narrative_service._generate_chunk", side_effect=fake_generate_chunk):
        result = generate_competitor_narratives_batch("Client", "client.com", {"a.com": {}, "b.com": {}})

    # a.com errored, so it must not appear in what b.com's chunk was shown.
    assert result["b.com"]["already_claimed_seen"] == {}
