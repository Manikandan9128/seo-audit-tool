import logging

import pytest

from app.integrations import ai_usage, text_ai_client
from app.integrations.ai_usage import AIStatus, UsageLedger
from app.services import ai_response_cache


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(ai_response_cache, "enabled", lambda: False)


def _fake_claude(monkeypatch, answers, in_tok=1000, out_tok=400):
    calls = {"n": 0}

    def attempt(prompt, max_tokens, errors):
        calls["n"] += 1
        ai_usage.record_usage("claude", "claude-sonnet-5", in_tok, out_tok, stop_reason="end_turn", duration_s=12.34)
        return answers.pop(0)

    monkeypatch.setitem(text_ai_client._PROVIDER_ATTEMPTS, "claude", attempt)
    monkeypatch.setattr(text_ai_client, "resolve_selected_provider", lambda: "claude")
    return calls


def _run(accept):
    for text, _p in text_ai_client.iter_text_attempts("prompt", 1000, []):
        if accept(text):
            return text


def test_every_call_records_attempt_stop_reason_and_duration(monkeypatch):
    _fake_claude(monkeypatch, ["ok"])
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        ai_usage.set_module("core_problem")
        _run(lambda t: True)
        call = ai_usage.current_ledger().calls[0]
    assert call["attempt"] == 1 and call["stop_reason"] == "end_turn" and call["duration_s"] == 12.3
    assert call["module"] == "core_problem"


def test_an_answer_the_step_rejects_counts_as_wasted_and_the_retry_is_attempt_2(monkeypatch):
    _fake_claude(monkeypatch, ["garbage", "good"])
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        ai_usage.set_module("next_steps")
        _run(lambda t: t == "good")
        summary = ai_usage.current_ledger().summary()
        calls = ai_usage.current_ledger().calls
    assert [c["attempt"] for c in calls] == [1, 2]
    assert [c["status"] for c in calls] == [AIStatus.INVALID_JSON, AIStatus.COMPLETED]
    assert summary["wasted_calls"] == 1 and summary["wasted_tokens"] == 1400
    assert summary["wasted_cost_usd"] > 0
    step = summary["modules"]["next_steps"]
    assert step["max_attempt"] == 2 and step["wasted_tokens"] == 1400
    assert step["wasted_reasons"] == {AIStatus.INVALID_JSON: 1}


def test_a_clean_run_reports_no_waste(monkeypatch):
    _fake_claude(monkeypatch, ["good"])
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        _run(lambda t: True)
        summary = ai_usage.current_ledger().summary()
    assert summary["wasted_calls"] == 0 and summary["wasted_tokens"] == 0 and summary["timeouts"] == 0


def test_truncated_calls_stay_marked_and_count_as_waste():
    ledger = UsageLedger(provider="claude", model="claude-sonnet-5")
    ledger.record_call("keywords", "claude", "claude-sonnet-5", 2000, 12000)
    ledger.mark_last_call(AIStatus.TOKEN_LIMIT_EXCEEDED)
    ledger.mark_calls_since(0, AIStatus.INVALID_JSON)  # must not overwrite the truncation mark
    summary = ledger.summary()
    assert ledger.calls[0]["status"] == AIStatus.TOKEN_LIMIT_EXCEEDED
    assert summary["modules"]["keywords"]["wasted_reasons"] == {AIStatus.TOKEN_LIMIT_EXCEEDED: 1}


def test_timeouts_are_counted_per_step_even_though_their_tokens_are_unknown(monkeypatch):
    def attempt(prompt, max_tokens, errors):
        ai_usage.note_timeout()
        errors.append("Claude request failed: timed out")
        return None

    monkeypatch.setitem(text_ai_client._PROVIDER_ATTEMPTS, "claude", attempt)
    monkeypatch.setattr(text_ai_client, "resolve_selected_provider", lambda: "claude")
    monkeypatch.setitem(ai_usage.AI_CONFIG, "max_retries", 1)
    with text_ai_client.selected_provider_scope("claude", "claude-sonnet-5"):
        ai_usage.set_module("ui_audit")
        list(text_ai_client.iter_text_attempts("p", 1000, []))
        summary = ai_usage.current_ledger().summary()
    assert summary["timeouts"] >= 1 and summary["modules"]["ui_audit"]["timeouts"] >= 1


def test_log_report_usage_writes_a_line_per_step_and_a_waste_warning(caplog):
    ledger = UsageLedger(provider="claude", model="claude-sonnet-5")
    ledger.record_call("core_problem", "claude", "claude-sonnet-5", 7000, 5000)
    ledger.record_call("next_steps", "claude", "claude-sonnet-5", 8000, 9000, attempt=2)
    ledger.mark_last_call(AIStatus.TOKEN_LIMIT_EXCEEDED)
    with caplog.at_level(logging.INFO):
        ai_usage.log_report_usage(logging.getLogger("t"), "job1", "client1", ledger.summary())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "step core_problem" in text and "step next_steps" in text
    assert "AI waste job job1" in text and "17000 token" in text


def test_attempt_number_and_effort_reach_the_worker_thread_that_calls_claude(monkeypatch):
    """Every provider call runs in _call_with_timeout's worker thread. The attempt
    number (for the log) and the 'low effort' retry setting were thread-locals the
    worker never saw: logs always said attempt 1, and the retry after a cut-off
    answer silently ran at the same effort and was cut off again."""
    seen = {}

    def inside_worker():
        seen["attempt"] = ai_usage.current_attempt()
        seen["effort"] = text_ai_client._claude_output_options("claude-sonnet-5", 100)["output_config"]["effort"]
        return "ok"

    ai_usage.set_attempt(3)
    text_ai_client._effort_override.value = "low"
    try:
        assert text_ai_client._call_with_timeout(inside_worker, 5) == "ok"
    finally:
        text_ai_client._effort_override.value = None
        ai_usage.set_attempt(1)
    assert seen == {"attempt": 3, "effort": "low"}
    # ...and the worker leaves nothing behind for the next task on the same thread.
    seen.clear()
    text_ai_client._call_with_timeout(inside_worker, 5)
    assert seen["effort"] == text_ai_client.CLAUDE_EFFORT
