"""Global AI token & failure control (2026-09-29): one request layer for every
provider — real failure reasons, controlled retries, JSON repair, usage
ledger and the report safety limit."""

from unittest.mock import patch

import pytest

import app.integrations.text_ai_client as text_ai_client
from app.integrations import ai_usage
from app.integrations.ai_usage import AIStatus


@pytest.fixture(autouse=True)
def _reset():
    yield
    text_ai_client.set_preferred_provider(None)
    ai_usage.set_ledger(None)
    ai_usage.set_module(None)


# --- failure classification --------------------------------------------------

@pytest.mark.parametrize("text, status", [
    ("Claude request failed: empty content, stop_reason=max_tokens", AIStatus.TOKEN_LIMIT_EXCEEDED),
    ("Groq request failed: finish_reason=length", AIStatus.TOKEN_LIMIT_EXCEEDED),
    ("Gemini request failed: finish_reason=MAX_TOKENS", AIStatus.TOKEN_LIMIT_EXCEEDED),
    ("OpenRouter request failed: 400 context_length_exceeded", AIStatus.TOKEN_LIMIT_EXCEEDED),
    ("Claude request failed: _try_claude timed out after 180s with no response", AIStatus.TIMEOUT),
    ("claude did not return valid JSON", AIStatus.INVALID_JSON),
    ("OpenRouter rate-limited (likely today's free-tier request cap): 429", AIStatus.PROVIDER_ERROR),
    ("Claude request failed: 529 overloaded", AIStatus.PROVIDER_ERROR),
    ("Claude returned an empty response", AIStatus.FAILED),
])
def test_each_failure_is_classified_by_its_real_reason(text, status):
    assert ai_usage.classify_error(text) == status


def test_token_limit_is_never_reported_as_a_timeout():
    msg = ai_usage.describe_failure(
        "AEO/GEO", ["[TOKEN_LIMIT_EXCEEDED] Claude request failed: empty content, stop_reason=max_tokens"], "claude",
    )
    assert msg.startswith("AEO/GEO — Token limit exceeded. The AI response reached the configured token limit")
    assert "timed out" not in msg.lower()
    assert msg.endswith("No fallback provider was used because Claude was selected.")


def test_timeout_wording_names_the_seconds():
    msg = ai_usage.describe_failure("Core", ["[TIMEOUT] Claude request failed: _try_claude timed out after 120s"], "claude")
    assert msg.startswith("Core — Request timed out after 120 seconds.")


def test_truncated_json_is_explained_as_truncation():
    msg = ai_usage.describe_failure("Branded vs Non-Branded", [
        "[TOKEN_LIMIT_EXCEEDED] Claude request failed: truncated content, stop_reason=max_tokens",
        "[INVALID_JSON] claude did not return valid JSON",
    ], "claude")
    assert msg.startswith("Branded vs Non-Branded — AI returned incomplete JSON.")
    assert "truncated before valid JSON could be completed" in msg


def test_raw_issue_strings_are_rewritten_readably():
    issue = ("Core Problem slide: [TOKEN_LIMIT_EXCEEDED] Claude request failed: empty content, stop_reason=max_tokens"
             " | No fallback provider was used because Claude was selected.")
    assert ai_usage.readable_issue(issue, "claude").startswith("Core Problem slide — Token limit exceeded.")
    assert ai_usage.readable_issue("Understanding Current Scenario: two snapshots", "claude") == (
        "Understanding Current Scenario: two snapshots"
    )


# --- JSON validation + repair ------------------------------------------------

def test_parse_json_handles_fences_and_surrounding_prose():
    assert ai_usage.parse_json('```json\n{"a": 1}\n```') == ({"a": 1}, False)
    assert ai_usage.parse_json('Here you go: {"a": [1, 2]} hope it helps') == ({"a": [1, 2]}, False)


def test_parse_json_repairs_a_truncated_answer_at_its_last_complete_item():
    data, repaired = ai_usage.parse_json('{"insights": ["one", "two", "thr')
    assert repaired and data == {"insights": ["one", "two"]}
    data, repaired = ai_usage.parse_json('{"issues": [{"t": "A", "p": "High"}, {"t": "B", "p')
    assert repaired and data == {"issues": [{"t": "A", "p": "High"}]}


def test_parse_json_gives_up_on_unrecoverable_text():
    assert ai_usage.parse_json("no json here") == (None, False)


# --- request layer -----------------------------------------------------------

def _run(provider, side_effect, consumer_rejects=0):
    """Iterates the layer like a JSON-parsing caller: rejects the first
    `consumer_rejects` answers (as invalid JSON), accepts the next."""
    text_ai_client.set_preferred_provider(provider)
    prompts, efforts, errors = [], [], []

    def attempt(prompt, max_tokens, errs):
        prompts.append(prompt)
        efforts.append(getattr(text_ai_client._effort_override, "value", None))
        result = side_effect.pop(0)
        if isinstance(result, Exception):
            errs.append(f"{provider.title()} request failed: {result}")
            return None
        return result

    accepted = None
    with patch("app.integrations.text_ai_client.settings") as mock_settings, \
         patch.dict(text_ai_client._PROVIDER_ATTEMPTS, {provider: attempt}):
        setattr(mock_settings, f"{provider}_api_key", "key")
        rejected = 0
        for text, _p in text_ai_client.iter_text_attempts("PROMPT", 4096, errors):
            if rejected < consumer_rejects:
                rejected += 1
                errors.append(f"{provider} did not return valid JSON")
                continue
            accepted = text
            break
    return accepted, prompts, efforts, errors


def test_token_limit_retry_asks_for_a_shorter_answer_at_low_effort():
    accepted, prompts, efforts, _ = _run("claude", [RuntimeError("stop_reason=max_tokens"), '{"ok": 1}'])
    assert accepted == '{"ok": 1}'
    assert prompts[1].startswith("PROMPT") and "cut off at the length limit" in prompts[1]
    assert efforts == [None, "low"]


def test_invalid_json_retry_uses_strict_json_instructions():
    accepted, prompts, _, _ = _run("claude", ["not json", '{"ok": 1}'], consumer_rejects=1)
    assert accepted == '{"ok": 1}'
    assert "not valid JSON" in prompts[1]


def test_a_request_that_keeps_timing_out_stops_after_max_timeout_retries():
    max_timeout_retries = ai_usage.AI_CONFIG["max_timeout_retries"]
    accepted, prompts, _, errors = _run("claude", [RuntimeError("timed out after 180s")] * (max_timeout_retries + 5))
    assert accepted is None and len(prompts) == max_timeout_retries + 1  # first try + max_timeout_retries retries
    assert errors[-1] == "No fallback provider was used because Claude was selected."


def test_never_more_than_the_configured_retries():
    max_retries = ai_usage.AI_CONFIG["max_retries"]
    _, prompts, _, _ = _run("claude", [RuntimeError("500 server error")] * (max_retries + 5))
    assert len(prompts) == max_retries + 1


def test_report_hard_limit_no_longer_stops_requests():
    # 2026-09-29 user decision: the High Token Usage popup's own Proceed
    # button is the one confirmation gate — once a report has passed it (or
    # the provider is free-tier and the popup never showed), nothing
    # mid-report aborts a run for being over token usage, however far past
    # report_hard_limit_tokens it runs. check_report_hard_limit() is a
    # deliberate no-op now; this asserts that, not just that it doesn't
    # raise, so a future re-add of enforcement can't slip back in silently.
    ledger = ai_usage.UsageLedger(provider="claude", model="claude-sonnet-5")
    ledger.record_call("core_problem", "claude", "claude-sonnet-5", 390_000, 20_000)
    assert ledger.total_tokens > ai_usage.AI_CONFIG["report_hard_limit_tokens"]
    ai_usage.set_ledger(ledger)
    accepted, prompts, _, errors = _run("claude", ['{"ok": 1}'])
    assert accepted == '{"ok": 1}' and len(prompts) == 1
    assert errors == []


# --- ledger ------------------------------------------------------------------

def test_ledger_tracks_tokens_cost_module_and_status():
    ledger = ai_usage.UsageLedger(provider="claude", model="claude-sonnet-5")
    ai_usage.set_ledger(ledger)
    with ai_usage.ai_module("aeo_geo"):
        _run("claude", ['{"ok": 1}'])
        ai_usage.record_usage("claude", "claude-sonnet-5", 10_000, 2_000)
    summary = ledger.summary()
    assert summary["total_tokens"] == 12_000
    assert summary["cost_usd"] == round(10_000 / 1e6 * 2 + 2_000 / 1e6 * 10, 4)
    assert summary["modules"]["aeo_geo"]["status"] == AIStatus.COMPLETED


def test_failed_module_keeps_its_real_status():
    ledger = ai_usage.UsageLedger(provider="claude")
    ai_usage.set_ledger(ledger)
    max_retries = ai_usage.AI_CONFIG["max_retries"]
    with ai_usage.ai_module("core_problem"):
        _run("claude", [RuntimeError("stop_reason=max_tokens")] * (max_retries + 5))
    assert ledger.summary()["modules"]["core_problem"]["status"] == AIStatus.TOKEN_LIMIT_EXCEEDED


def test_module_budget_gives_reasoning_providers_room_and_caps_others():
    assert ai_usage.output_budget("aeo_geo", "claude", 4096) == 16_000
    assert ai_usage.output_budget("aeo_geo", "groq", 4096) == 4096
    assert ai_usage.output_budget("seo_issues", "groq", 20_000) == 12_000
