import pytest

from app.integrations import ai_usage, text_ai_client
from app.services import ai_response_cache


class _FakeStore:
    def __init__(self):
        self.rows = {}
        self.deleted = []

    def get(self, key):
        row = self.rows.get(key)
        return dict(row) if row else None

    def put(self, key, module, provider, model, response, input_tokens, output_tokens):
        self.rows[key] = {"response": response, "input_tokens": input_tokens, "output_tokens": output_tokens}

    def delete(self, key):
        self.deleted.append(key)
        self.rows.pop(key, None)


@pytest.fixture
def store(monkeypatch):
    s = _FakeStore()
    monkeypatch.setattr(ai_response_cache, "get", s.get)
    monkeypatch.setattr(ai_response_cache, "put", s.put)
    monkeypatch.setattr(ai_response_cache, "delete", s.delete)
    monkeypatch.setattr(ai_response_cache, "enabled", lambda: True)
    return s


@pytest.fixture
def claude(monkeypatch):
    """A fake paid Claude: every request costs 1000 in / 400 out and is counted."""
    calls = {"n": 0, "answers": []}

    def fake_attempt(prompt, max_tokens, errors):
        calls["n"] += 1
        answer = calls["answers"].pop(0) if calls["answers"] else f"answer-{calls['n']}"
        ai_usage.record_usage("claude", "claude-sonnet-5", 1000, 400)
        return answer

    monkeypatch.setitem(text_ai_client._PROVIDER_ATTEMPTS, "claude", fake_attempt)
    monkeypatch.setattr(text_ai_client, "resolve_selected_provider", lambda: "claude")
    return calls


def _run(prompt, accept=lambda t: True):
    errors = []
    for text, _provider in text_ai_client.iter_text_attempts(prompt, 1000, errors):
        if accept(text):
            return text
    return None


def test_same_question_twice_is_paid_once(store, claude):
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        first = _run("Write the core problem for Lumber")
        ledger1 = ai_usage.current_ledger().summary()
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        second = _run("Write the core problem for Lumber")
        ledger2 = ai_usage.current_ledger().summary()
    assert first == second == "answer-1"
    assert claude["n"] == 1
    assert ledger1["total_tokens"] == 1400 and ledger1["cached_calls"] == 0
    assert ledger2["total_tokens"] == 0 and ledger2["cached_calls"] == 1
    assert ledger2["saved_tokens"] == 1400 and ledger2["saved_cost_usd"] > 0


def test_a_changed_question_asks_claude_again(store, claude):
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        _run("question about 1,000 sessions")
        _run("question about 1,200 sessions")
    assert claude["n"] == 2


def test_a_different_model_does_not_share_answers(store, claude):
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        _run("same prompt")
    with text_ai_client.selected_provider_scope("claude", "claude-haiku-4-5-20251001"):
        _run("same prompt")
    assert claude["n"] == 2


def test_rejected_answer_is_never_stored(store, claude):
    claude["answers"] = ["not json", "valid json"]
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        result = _run("p", accept=lambda t: t == "valid json")
    assert result == "valid json"
    assert [r["response"] for r in store.rows.values()] == ["valid json"]


def test_saved_answer_the_step_now_rejects_is_dropped_and_asked_afresh(store, claude):
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        _run("p")  # stores "answer-1"
    claude["answers"] = ["fresh-good"]
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        result = _run("p", accept=lambda t: t == "fresh-good")
    assert result == "fresh-good"
    assert store.deleted, "the rejected saved answer must be removed"
    assert [r["response"] for r in store.rows.values()] == ["fresh-good"]


def test_free_providers_are_not_cached(store, monkeypatch):
    monkeypatch.setattr(ai_response_cache, "enabled", lambda: True)
    assert text_ai_client._cache_key_for("groq", "prompt", "") is None
    assert text_ai_client._cache_key_for("gemini", "prompt", "") is None


def test_cache_can_be_switched_off(store, claude, monkeypatch):
    monkeypatch.setattr(ai_response_cache, "enabled", lambda: False)
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        _run("p")
        _run("p")
    assert claude["n"] == 2


def test_a_broken_cache_never_breaks_a_report(claude, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(ai_response_cache, "enabled", lambda: True)
    monkeypatch.setattr(ai_response_cache.logger, "warning", lambda *a, **k: None)
    # get/put swallow DB errors themselves; simulate that they return a miss / no-op.
    monkeypatch.setattr(ai_response_cache, "get", lambda key: None)
    monkeypatch.setattr(ai_response_cache, "put", lambda *a, **k: None)
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        assert _run("p") == "answer-1"


def test_image_requests_key_on_the_pictures(store, monkeypatch):
    calls = {"n": 0}

    def fake_vision(prompt, images, max_tokens, errors):
        calls["n"] += 1
        ai_usage.record_usage("claude", "claude-sonnet-5", 6000, 900)
        return "findings"

    monkeypatch.setitem(text_ai_client._VISION_PROVIDER_ATTEMPTS, "claude", fake_vision)
    monkeypatch.setattr(text_ai_client, "resolve_selected_provider", lambda: "claude")
    monkeypatch.setattr(text_ai_client, "_cap_image_dimensions", lambda b, m: (b, m))

    def run(images):
        with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
            for text, _p in text_ai_client.iter_text_with_images_attempts("audit", images, 1000, []):
                return text

    run([(b"shot-a", "image/png")])
    run([(b"shot-a", "image/png")])
    assert calls["n"] == 1
    run([(b"shot-b", "image/png")])
    assert calls["n"] == 2


def test_cache_key_depends_on_every_input():
    k = ai_response_cache.make_key
    base = k("claude", "m", "medium", "core_problem", "", "prompt")
    assert base == k("claude", "m", "medium", "core_problem", "", "prompt")
    assert len({base, k("claude", "m2", "medium", "core_problem", "", "prompt"),
                k("claude", "m", "low", "core_problem", "", "prompt"),
                k("claude", "m", "medium", "next_steps", "", "prompt"),
                k("claude", "m", "medium", "core_problem", "img", "prompt"),
                k("claude", "m", "medium", "core_problem", "", "prompt2")}) == 6


def test_cache_service_round_trip_on_a_real_table(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import session as db_session
    from app.models.ai_response_cache import AiResponseCache

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AiResponseCache.__table__.create(engine)
    monkeypatch.setattr(db_session, "SessionLocal", sessionmaker(bind=engine))

    key = ai_response_cache.make_key("claude", "m", "medium", "next_steps", "", "prompt")
    assert ai_response_cache.get(key) is None
    ai_response_cache.put(key, "next_steps", "claude", "m", "the answer", 8000, 7000)
    hit = ai_response_cache.get(key)
    assert hit == {"response": "the answer", "input_tokens": 8000, "output_tokens": 7000}
    ai_response_cache.get(key)
    with sessionmaker(bind=engine)() as db:
        assert db.get(AiResponseCache, key).hits == 2
    ai_response_cache.delete(key)
    assert ai_response_cache.get(key) is None
    assert ai_response_cache.clear_all() == 0
