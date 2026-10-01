"""Unified text-generation call that tries whichever AI provider has a
configured key — Groq first, then Gemini, then Claude as a last, paid
fallback — so callers (company overview extraction, AI summary, competitor
narrative, core problem, keyword clustering) work as long as *any* key is
set, without provider-specific branching at each call site.

Groq goes first deliberately (confirmed real-world tradeoff, not the
original default): Gemini's free-tier quota resets once every 24 hours, so
burning it first on every call exhausts it early in the day and it then
sits useless in reserve while Groq alone (with its own, faster-recovering
per-minute/hourly limits) idles unused until Gemini fails. Trying Groq
first spends the fast-recovering resource first and keeps Gemini's scarce
daily allowance in reserve for when Groq is genuinely, if temporarily,
tapped out.

OpenRouter was tried as a third fallback between Groq and Gemini
(2026-09-22) but removed the same day: its free tier caps at just 50
requests/day, far below what a single report generation needs (15-20+ AI
calls), so it was already exhausted well before most reports finished and
never provided real headroom — confirmed live on a Bharatbenz report where
Groq, OpenRouter, AND Gemini were all simultaneously capped, and
OpenRouter's own error showed 0/50 remaining. Not worth the added
complexity for a budget too small to matter."""

import logging
import threading
from contextlib import contextmanager
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from io import BytesIO

import base64

import httpx

from app.integrations import ai_usage
from app.services import ai_response_cache
from app.integrations.ai_usage import AIStatus, TokenLimitError
from anthropic import Anthropic
from google import genai
from google.genai import types as genai_types

from app.config import settings
from app.integrations.gemini_errors import friendly_gemini_error

logger = logging.getLogger(__name__)

RATE_LIMIT_RETRY_DELAY_SECONDS = 20

GEMINI_MODEL = "gemini-3.6-flash"
GROQ_MODEL = "openai/gpt-oss-120b"
CLAUDE_MODEL = "claude-sonnet-5"

# Selectable Claude models (2026-09-25) — the paid API bills per token by
# model, and a cheaper/faster model can be worth trading some quality for
# on a report where cost or speed matters more. Keys are what the UI's
# dropdown offers; values are the real Anthropic model ids this session's
# own environment reports as current. Sonnet 5 stays CLAUDE_MODEL's
# default (unchanged behavior when nothing is selected).
CLAUDE_MODEL_CHOICES = {
    "claude-opus-5-5": "Opus 5.5 (most capable, highest cost)",
    "claude-sonnet-5": "Sonnet 5 (balanced — default)",
    "claude-haiku-4-5-20251001": "Haiku 4.5 (fastest, lowest cost)",
}

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

GEMINI_TIMEOUT_SECONDS = 45
# Sonnet 5 and Opus 5.5 think by default (adaptive thinking runs even with
# no `thinking` param; on Opus 5.5 it can't be turned off), and thinking
# tokens count against max_tokens — so Claude's output budget comes from
# ai_usage.MODULE_OUTPUT_BUDGETS (room for thinking plus the answer), and
# effort "medium" keeps thinking proportionate to these structured-JSON
# tasks ("low" on a retry after a token-limit failure).
CLAUDE_EFFORT = "medium"
CLAUDE_TIMEOUT_SECONDS = 180
CLAUDE_VISION_TIMEOUT_SECONDS = 240


def _call_with_timeout(fn, timeout_seconds: float, *args, **kwargs):
    """Runs fn in a worker thread and enforces a hard wall-clock timeout,
    regardless of whether the underlying SDK exposes (or honors) its own
    timeout — confirmed real: a Gemini call with no client-side timeout
    hung an entire report generation for 30+ minutes on one single AI call
    with nothing to stop it, even though a quota/rate-limit rejection
    normally comes back near-instantly (this was Google's servers being
    slow to respond, not a fast reject). Raises TimeoutError on expiry,
    which every caller's existing `except Exception` handling already
    treats the same as any other provider failure — reported as that
    provider's error instead of hanging the whole pipeline. The orphaned
    thread is abandoned (not killed — Python has no API for that) rather
    than waited on; it either eventually finishes harmlessly in the
    background or the process exits, whichever comes first."""
    pool = JobContextThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        raise TimeoutError(f"{fn.__name__} timed out after {timeout_seconds:.0f}s with no response") from None
    finally:
        pool.shutdown(wait=False)

# A single report generation makes several sequential AI calls (company
# overview, core problem, keyword clustering, competitor narratives, next
# steps) that can all land on Groq back-to-back. Groq's real constraint is
# a rolling 60s token budget (prompt + completion combined, see
# GROQ_TPM_BUDGET below) — not a fixed gap between calls — so this tracks
# every Groq call's (timestamp, tokens_used) in the trailing window and
# blocks a new call only long enough for enough of that window to age out
# and free the budget it needs, then lets it through immediately. A caller
# that needs more than one Groq call's worth of tokens (e.g. competitor
# narratives split into several budget-sized chunks) naturally spreads
# across the next minute's budget instead of bursting past it.
_groq_pacing_lock = threading.Lock()
_groq_usage_window: list[tuple[float, int]] = []
_GROQ_WINDOW_SECONDS = 60.0


def _reserve_groq_budget(needed_tokens: int) -> None:
    while True:
        with _groq_pacing_lock:
            now = time.monotonic()
            cutoff = now - _GROQ_WINDOW_SECONDS
            while _groq_usage_window and _groq_usage_window[0][0] < cutoff:
                _groq_usage_window.pop(0)
            used = sum(tokens for _, tokens in _groq_usage_window)
            # The "used == 0" case lets a single oversized-but-otherwise-
            # allowed call through once nothing else is in the window,
            # rather than looping forever — _try_groq's own too-small-to-
            # serve-at-all check runs before this and already refuses a
            # request that can never fit.
            if used == 0 or used + needed_tokens <= GROQ_TPM_BUDGET:
                _groq_usage_window.append((now, needed_tokens))
                return
            wait_seconds = _groq_usage_window[0][0] + _GROQ_WINDOW_SECONDS - now
        time.sleep(max(wait_seconds, 0.5))


# Gemini's free tier is request-count-limited (RPM + RPD), not token-
# limited like Groq's — its 250,000 TPM is never the binding constraint,
# but 10 RPM / 250 RPD (per gemini-3.x Flash's published free-tier table;
# Google's own docs refuse to state a static number, "check your account's
# AI Studio dashboard") absolutely is. This app tracked zero request-count
# pacing for Gemini before 2026-09-22 — confirmed real: a report's ~17
# sequential AI calls all fall through to Gemini within seconds once Groq
# is already dead for the day (Groq fails fast, a plain 429, not a slow
# timeout), which blows straight through 10 RPM before the existing
# one-retry-after-20s discipline in _attempt_gemini can absorb it, wasting
# real Gemini calls on rejections this process could have predicted and
# paced around instead. Same rolling-window shape as _reserve_groq_budget
# above, just counting requests in two windows (minute + day) instead of
# summing tokens in one.
_gemini_pacing_lock = threading.Lock()
_gemini_minute_window: list[float] = []
_gemini_day_window: list[float] = []
_GEMINI_MINUTE_WINDOW_SECONDS = 60.0
_GEMINI_DAY_WINDOW_SECONDS = 24 * 60 * 60.0
# Capped below the real 10 RPM / 250 RPD to leave headroom for clock/
# measurement slop between this process and Google's own window — same
# margin-below-observed-cap discipline GROQ_TPM_BUDGET already uses.
_GEMINI_RPM_BUDGET = 8
_GEMINI_RPD_BUDGET = 230


def _reserve_gemini_slot() -> bool:
    """True once a Gemini call may proceed (and reserves its slot, blocking
    with a sleep-and-recheck loop if the per-minute window is momentarily
    full — same pacing-not-failing discipline as _reserve_groq_budget).
    False means today's own request-count budget looks spent — the caller
    should skip Gemini entirely rather than wait, since a spent daily
    budget doesn't free up the way a per-minute one does.

    In-memory only, resets on process restart — same accepted limitation
    as Groq's own _groq_usage_window (this codebase's existing pattern for
    self-imposed pacing), not a hard guarantee synced with Google's actual
    account-side counters, just enough to stop this process's own bursts
    from wasting a real Gemini call on a rejection that was predictable."""
    while True:
        with _gemini_pacing_lock:
            now = time.monotonic()
            minute_cutoff = now - _GEMINI_MINUTE_WINDOW_SECONDS
            while _gemini_minute_window and _gemini_minute_window[0] < minute_cutoff:
                _gemini_minute_window.pop(0)
            day_cutoff = now - _GEMINI_DAY_WINDOW_SECONDS
            while _gemini_day_window and _gemini_day_window[0] < day_cutoff:
                _gemini_day_window.pop(0)

            if len(_gemini_day_window) >= _GEMINI_RPD_BUDGET:
                return False

            if len(_gemini_minute_window) < _GEMINI_RPM_BUDGET:
                _gemini_minute_window.append(now)
                _gemini_day_window.append(now)
                return True

            wait_seconds = _gemini_minute_window[0] + _GEMINI_MINUTE_WINDOW_SECONDS - now
        time.sleep(max(wait_seconds, 0.5))


class NoAIProviderConfigured(Exception):
    pass


def _gemini_result(response) -> str:
    """Records usage, flags a cut-off answer, returns the text."""
    meta = getattr(response, "usage_metadata", None)
    ai_usage.record_usage(
        "gemini", GEMINI_MODEL, getattr(meta, "prompt_token_count", 0) or 0,
        (getattr(meta, "candidates_token_count", 0) or 0) + (getattr(meta, "thoughts_token_count", 0) or 0),
    )
    candidates = getattr(response, "candidates", None) or []
    if candidates and "MAX_TOKENS" in str(getattr(candidates[0], "finish_reason", "")):
        ledger = ai_usage.current_ledger()
        if ledger:
            ledger.mark_last_call(AIStatus.TOKEN_LIMIT_EXCEEDED)
        raise TokenLimitError("finish_reason=MAX_TOKENS")
    return (response.text or "").strip()


def _try_gemini(prompt: str) -> str:
    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return _gemini_result(response)


# Some free-tier Groq orgs are capped as low as 8000 tokens-per-minute
# total (prompt + completion combined) — confirmed real: a 413 "Request
# too large" hit at prompt≈4900 + max_tokens=4096 defaulted by an unrelated
# caller. Callers pass max_tokens sized for the completion they actually
# need, with no idea what the prompt costs against this shared budget, so
# clamp here using a rough chars/4 token estimate rather than trusting the
# caller's number outright. Public (no leading underscore) so a caller that
# needs more output than fits one call — e.g. competitor narratives — can
# size its own chunks against the same number instead of guessing.
GROQ_TPM_BUDGET = 7500  # stays under the observed 8000 cap with slack


def _chat_completion_result(provider: str, model: str | None, data: dict) -> str:
    """Usage + truncation handling for OpenAI-compatible chat responses
    (Groq, OpenRouter, and any future OpenAI-style provider)."""
    usage = data.get("usage") or {}
    ai_usage.record_usage(provider, model or data.get("model"), usage.get("prompt_tokens") or 0,
                          usage.get("completion_tokens") or 0)
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        ledger = ai_usage.current_ledger()
        if ledger:
            ledger.mark_last_call(AIStatus.TOKEN_LIMIT_EXCEEDED)
        raise TokenLimitError("finish_reason=length")
    return ((choice.get("message") or {}).get("content") or "").strip()


def _try_groq(prompt: str, max_tokens: int) -> str:
    estimated_prompt_tokens = len(prompt) // 4
    safe_max_tokens = max(256, min(max_tokens, GROQ_TPM_BUDGET - estimated_prompt_tokens))
    if safe_max_tokens < max_tokens // 2:
        # Confirmed real: the competitor-narrative batch call (up to 16000
        # requested tokens for 4-5 competitors' full sections in one JSON
        # object) was silently clamped down to ~a quarter of that here,
        # producing a truncated, unparseable JSON response — reported back
        # as a normal 200 from Groq, not a failure, so generate_text() never
        # knew to fall through to Gemini. It just looked like every
        # competitor's narrative "failed" with no clue why. Below this
        # threshold, clamping can't meaningfully serve the request — raise
        # so the caller falls through to a provider that can actually
        # produce a complete response instead of a guaranteed-truncated one.
        raise RuntimeError(
            f"prompt too large for Groq's shared TPM budget to leave room for the requested "
            f"output ({safe_max_tokens} available vs {max_tokens} needed)"
        )
    _reserve_groq_budget(estimated_prompt_tokens + safe_max_tokens)
    response = httpx.post(
        GROQ_API_URL,
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": safe_max_tokens,
            # gpt-oss-120b is a reasoning model whose hidden chain-of-thought
            # tokens share this SAME max_tokens budget — confirmed live
            # 2026-09-12: with no reasoning_effort set (defaults to full
            # reasoning), the tightest-budget callers (SEO Issues insights
            # at 768, Core Problem at 1024) had reasoning consume the whole
            # budget, coming back as an empty content string or truncated/
            # unparseable JSON. "low" leaves enough headroom for tight
            # prompts to actually get a final answer.
            "reasoning_effort": "low",
        },
        timeout=60,
    )
    if response.status_code >= 400:
        # raise_for_status()'s default message is just the URL + status code
        # — Groq's actual reason (bad model name, malformed body, etc.) is
        # in the response body, and without it a 4xx is an unpinnable guess.
        raise httpx.HTTPStatusError(
            f"{response.status_code} {response.reason_phrase} for url '{response.url}': {response.text[:300]}",
            request=response.request,
            response=response,
        )
    return _chat_completion_result("groq", GROQ_MODEL, response.json())


def _groq_retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


_effort_override = threading.local()


def _claude_output_options(model: str, max_tokens: int) -> dict:
    """max_tokens plus effort for one Claude call. Haiku 4.5 doesn't think
    by default and rejects `effort`, so it only gets max_tokens."""
    options: dict = {"max_tokens": max_tokens}
    if not model.startswith("claude-haiku"):
        options["output_config"] = {"effort": getattr(_effort_override, "value", None) or CLAUDE_EFFORT}
    return options


def _claude_result(response, model: str, duration_s: float | None = None) -> str:
    """Records usage; a cut-off (stop_reason=max_tokens) or empty answer
    raises with the real stop_reason so it's never mistaken for a
    timeout or a refusal."""
    usage = response.usage
    ai_usage.record_usage(
        "claude", model, usage.input_tokens, usage.output_tokens,
        stop_reason=response.stop_reason, duration_s=duration_s,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )
    logger.info(
        "Claude call step=%s model=%s attempt=%d in=%d out=%d stop=%s %.1fs",
        ai_usage.current_module(), model, ai_usage.current_attempt(), usage.input_tokens, usage.output_tokens,
        response.stop_reason, duration_s or 0.0,
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if response.stop_reason == "max_tokens":
        ledger = ai_usage.current_ledger()
        if ledger:
            ledger.mark_last_call(AIStatus.TOKEN_LIMIT_EXCEEDED)
        raise TokenLimitError(f"{'truncated' if text else 'empty'} content, stop_reason=max_tokens")
    if not text:
        raise RuntimeError(f"empty content, stop_reason={response.stop_reason}")
    return text


def _try_claude(prompt: str, max_tokens: int) -> str:
    client = Anthropic(api_key=settings.claude_api_key)
    model = _current_claude_model()
    started = time.monotonic()
    response = client.messages.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        **_claude_output_options(model, max_tokens),
    )
    _record_claude_usage("text", response.usage.input_tokens, response.usage.output_tokens)
    return _claude_result(response, model, time.monotonic() - started)


# Lets a caller STRICTLY pin one provider for the lifetime of a single
# job's thread (e.g. "run this report with Claude, and only Claude") --
# without threading a preferred_provider parameter through the ~10 call
# sites between the report-generation route and generate_text(). 2026-09-
# 28: the selection is the ONLY provider used — no provider order, no
# fallback (see resolve_selected_provider). Every affected call happens
# synchronously within one dedicated thread per job/request (see
# _run_generate_report_job, report_preview), so a thread-local is exactly
# "one preference per in-flight job" with no cross-request leakage risk,
# as long as callers reset it when done (set_preferred_provider(None) in a
# finally block) so a thread reused by the app server's pool doesn't carry
# a stale preference into an unrelated later request.
_provider_preference = threading.local()

_VALID_PROVIDERS = {"groq", "gemini", "claude", "browser_use", "openrouter"}


def set_preferred_provider(name: str | None) -> None:
    if name is not None and name not in _VALID_PROVIDERS:
        raise ValueError(f"Unknown provider {name!r} — must be one of {sorted(_VALID_PROVIDERS)} or None")
    _provider_preference.value = name


# Heavy AI steps the user chose to skip for this report (2026-09-29): the
# high-usage popup lets them untick e.g. AEO/GEO or the UI/UX screenshot
# audit on a paid provider. Same thread-local-per-job lifecycle as the
# provider pin; the report build checks ai_step_skipped(key) before running
# each heavy step. Keys: see app.services.ai_usage_estimate.HEAVY_AI_STEPS.
_skipped_ai_steps = threading.local()


def set_skipped_ai_steps(steps) -> None:
    _skipped_ai_steps.value = frozenset(steps or ())


def ai_step_skipped(step: str) -> bool:
    return step in (getattr(_skipped_ai_steps, "value", None) or ())


@contextmanager
def selected_provider_scope(provider: str | None, claude_model: str | None = None, skip_steps=()):
    """Pins this thread's AI calls to the user's Report AI Provider (and the
    heavy steps they chose to skip) for the duration of one request, then
    clears it so a pooled server thread never carries it into an unrelated
    request."""
    set_preferred_provider(provider)
    set_claude_model(claude_model)
    set_skipped_ai_steps(skip_steps)
    previous_usage = ai_usage.context_snapshot()
    ai_usage.set_ledger(ai_usage.UsageLedger(
        provider=provider, model=(claude_model or CLAUDE_MODEL) if provider == "claude" else None,
    ))
    try:
        yield
    finally:
        set_preferred_provider(None)
        set_claude_model(None)
        set_skipped_ai_steps(())
        ai_usage.restore_context(previous_usage)


# Same thread-local-per-job pattern as _provider_preference above, one
# level down: which Claude model this job's Claude calls use, independent
# of the provider choice (only read when Claude is selected). Reset to
# None (uses CLAUDE_MODEL)
# in the same finally block that resets the provider preference.
_claude_model_preference = threading.local()


def set_claude_model(model: str | None) -> None:
    if model is not None and model not in CLAUDE_MODEL_CHOICES:
        raise ValueError(f"Unknown Claude model {model!r} — must be one of {sorted(CLAUDE_MODEL_CHOICES)} or None")
    _claude_model_preference.value = model


def _current_claude_model() -> str:
    return getattr(_claude_model_preference, "value", None) or CLAUDE_MODEL


# Real per-call token counts straight from the Claude API's own response
# (response.usage), not the max_tokens cap a call was allowed up to —
# added 2026-09-25 so a report's actual Claude spend can be logged, since
# nothing in this app tracked it before and the only real numbers lived
# in Anthropic's own Console, not visible to anyone building/debugging
# the report pipeline itself. Same thread-local lifecycle as
# _provider_preference above (one report-generation job = one thread =
# one call to reset_claude_token_usage() at the start, one read at the
# end) — Groq/Gemini calls aren't tracked here since the question this
# answers is specifically "what does the Claude API bill for this
# report," not total AI usage across every provider.
_claude_token_usage = threading.local()


def reset_claude_token_usage() -> None:
    """Call at the start of one report-generation job's thread so
    get_claude_token_usage() below reflects only that job's own Claude
    calls, not whatever a reused thread-pool thread racked up on an
    earlier, unrelated job."""
    _claude_token_usage.calls = []


def _record_claude_usage(label: str, input_tokens: int, output_tokens: int) -> None:
    calls = getattr(_claude_token_usage, "calls", None)
    if calls is None:
        return  # reset_claude_token_usage() was never called on this thread (e.g. a one-off script) — nothing to track into
    calls.append({"label": label, "input_tokens": input_tokens, "output_tokens": output_tokens})


def get_claude_token_usage() -> dict:
    """{"calls": n, "input_tokens": total, "output_tokens": total} for every
    Claude API call made on this thread since the last
    reset_claude_token_usage() — real numbers from the API's own usage
    field, not an estimate. All zero if reset was never called."""
    calls = getattr(_claude_token_usage, "calls", None) or []
    return {
        "calls": len(calls),
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] for c in calls),
    }


class JobContextThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor whose tasks inherit the submitting thread's job
    context: the strict provider pin, the Claude model choice, and the
    Claude token-usage list. All three are thread-locals, so a plain pool
    worker saw none of them — confirmed real (2026-09-28): every provider
    call runs inside _call_with_timeout's worker thread, so _try_claude
    read the default CLAUDE_MODEL instead of the model the user picked and
    its token usage was never recorded; and report-generation's own nested
    pools (run_site_audit -> summarize_company) called generate_text() with
    no pin at all, so they ignored the user's selected provider. The worker's previous
    values are restored after each task, since pool threads are reused."""

    def submit(self, fn, /, *args, **kwargs):
        context = (
            getattr(_provider_preference, "value", None),
            getattr(_claude_model_preference, "value", None),
            getattr(_claude_token_usage, "calls", None),
            getattr(_skipped_ai_steps, "value", None),
        )
        usage_context = ai_usage.context_snapshot()

        def run():
            previous = (
                getattr(_provider_preference, "value", None),
                getattr(_claude_model_preference, "value", None),
                getattr(_claude_token_usage, "calls", None),
                getattr(_skipped_ai_steps, "value", None),
            )
            previous_usage = ai_usage.context_snapshot()
            (_provider_preference.value, _claude_model_preference.value, _claude_token_usage.calls,
             _skipped_ai_steps.value) = context
            ai_usage.restore_context(usage_context)
            try:
                return fn(*args, **kwargs)
            finally:
                (_provider_preference.value, _claude_model_preference.value, _claude_token_usage.calls,
                 _skipped_ai_steps.value) = previous
                ai_usage.restore_context(previous_usage)

        return super().submit(run)


def _attempt_groq(prompt: str, max_tokens: int, errors: list[str]) -> str | None:
    if not settings.groq_api_key:
        return None
    try:
        text = _try_groq(prompt, max_tokens)
        if text:
            return text
        errors.append("Groq returned an empty response")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            retry_after = _groq_retry_after_seconds(e.response)
            # Groq's Retry-After tells us whether this 429 is a per-minute
            # limit (short, recovers inside our retry window) or a
            # daily/token-cap exhaustion (long, won't recover in 20s) —
            # same reasoning already applied to Gemini's daily-quota case
            # below. Confirmed real: with both providers quota-exhausted,
            # blindly retrying every single Groq 429 after a 20s sleep
            # (guaranteed to fail again) was the main contributor to
            # reports stalling for minutes at the competitor-narrative AI
            # call.
            if retry_after is not None and retry_after > RATE_LIMIT_RETRY_DELAY_SECONDS:
                errors.append(f"Groq rate-limited, not retrying (Retry-After {retry_after:.0f}s): {str(e)[:300]}")
            else:
                time.sleep(RATE_LIMIT_RETRY_DELAY_SECONDS)
                try:
                    text = _try_groq(prompt, max_tokens)
                    if text:
                        return text
                    errors.append("Groq returned an empty response")
                except Exception as e2:
                    errors.append(f"Groq request failed: {str(e2)[:300]}")
        else:
            errors.append(f"Groq request failed: {str(e)[:300]}")
    except Exception as e:
        errors.append(f"Groq request failed: {str(e)[:300]}")
    return None


def _attempt_gemini(prompt: str, max_tokens: int, errors: list[str]) -> str | None:
    if not settings.gemini_api_key:
        return None
    # Self-paced request-count budget (2026-09-22) — checked BEFORE the
    # call, not just reacted to after: skip outright if today's own
    # request-count budget already looks spent (no point burning a real
    # call on a rejection this process can already predict), otherwise
    # block briefly if the per-minute window is momentarily full instead
    # of firing straight into a 429. See _reserve_gemini_slot's docstring.
    if not _reserve_gemini_slot():
        errors.append("Gemini skipped — this process's own daily request-count budget looks spent (self-paced, not necessarily Google's real count)")
        return None
    try:
        text = _call_with_timeout(_try_gemini, GEMINI_TIMEOUT_SECONDS, prompt)
        if text:
            return text
        errors.append("Gemini returned an empty response")
    except Exception as e:
        error_text = str(e)
        is_daily_quota = "PerDay" in error_text or "free_tier" in error_text.lower()
        # A burst of concurrent calls (e.g. one per competitor) can all hit
        # Gemini's per-minute cap in the same instant — one short wait and
        # retry is often enough since the window is per-minute, not daily.
        # A daily-quota exhaustion won't clear in 20s, so don't bother.
        # _reserve_gemini_slot above already absorbs most of this before it
        # happens; this stays as a second line of defense for whatever it
        # doesn't catch (e.g. Google's real count already includes calls
        # from outside this process).
        if not is_daily_quota and ("RESOURCE_EXHAUSTED" in error_text or "429" in error_text):
            time.sleep(RATE_LIMIT_RETRY_DELAY_SECONDS)
            if not _reserve_gemini_slot():
                errors.append("Gemini skipped retry — daily request-count budget looks spent")
                return None
            try:
                text = _call_with_timeout(_try_gemini, GEMINI_TIMEOUT_SECONDS, prompt)
                if text:
                    return text
                errors.append("Gemini returned an empty response")
            except Exception as e2:
                errors.append(friendly_gemini_error(e2))
        else:
            errors.append(friendly_gemini_error(e))
    return None


def _attempt_claude(prompt: str, max_tokens: int, errors: list[str]) -> str | None:
    """One Claude request. Retries are decided centrally (_layered_attempts)
    from the real failure reason, never blindly here."""
    if not settings.claude_api_key:
        return None
    try:
        text = _call_with_timeout(_try_claude, CLAUDE_TIMEOUT_SECONDS, prompt, max_tokens)
        if text:
            return text
        errors.append("Claude returned an empty response")
    except Exception as e:
        if isinstance(e, TimeoutError):
            ai_usage.note_timeout()
        errors.append(f"Claude request failed: {str(e)[:300]}")
    return None


BROWSER_USE_API_URL = "https://api.browser-use.com/api/v4/runs"
BROWSER_USE_MODEL = "gpt-5.6-luna"
BROWSER_USE_TIMEOUT_SECONDS = 120
BROWSER_USE_POLL_INTERVAL_SECONDS = 3
# A vision-replacement run browses the site at two widths before answering.
BROWSER_USE_VISION_TIMEOUT_SECONDS = 360


def _try_browser_use(prompt: str, timeout_seconds: float = BROWSER_USE_TIMEOUT_SECONDS, start_url: str | None = None) -> str:
    """Runs the prompt as a Browser Use Cloud agent run and polls until it
    finishes. A real billed agent run, much slower than a chat completion.
    With start_url the agent opens that page (the UI-Level Fixes vision
    replacement); without it, a plain reasoning task."""
    headers = {"X-Browser-Use-API-Key": settings.browser_use_api_key}
    response = httpx.post(
        BROWSER_USE_API_URL,
        headers=headers,
        json={"task": prompt, "model": BROWSER_USE_MODEL, **({"startUrl": start_url} if start_url else {})},
        timeout=30,
    )
    response.raise_for_status()
    run_id = response.json()["id"]

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(BROWSER_USE_POLL_INTERVAL_SECONDS)
        status_response = httpx.get(f"{BROWSER_USE_API_URL}/{run_id}", headers=headers, timeout=30)
        status_response.raise_for_status()
        data = status_response.json()
        status = data.get("status")
        if status == "completed":
            result = (data.get("result") or "").strip()
            # Browser Use reports no token counts — estimated from text size.
            ai_usage.record_usage("browser_use", BROWSER_USE_MODEL, len(prompt) // 4, len(result) // 4, estimated=True)
            return result
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"Browser Use run {status}: {data.get('error') or 'no error detail'}")
    raise TimeoutError(f"Browser Use run did not finish within {timeout_seconds:.0f}s")


def _attempt_browser_use(prompt: str, max_tokens: int, errors: list[str]) -> str | None:
    if not settings.browser_use_api_key:
        return None
    try:
        text = _try_browser_use(prompt)
        if text:
            return text
        errors.append("Browser Use returned an empty response")
    except Exception as e:
        errors.append(f"Browser Use request failed: {str(e)[:300]}")
    return None


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
# OpenRouter's own auto-router, not a specific model — named free models
# churn (deprecated/404ing within hours, see 98e8bf3), the alias doesn't.
OPENROUTER_MODEL = "openrouter/free"
OPENROUTER_TIMEOUT_SECONDS = 60
OPENROUTER_VISION_TIMEOUT_SECONDS = 120


def _try_openrouter(prompt: str, max_tokens: int) -> str:
    response = httpx.post(
        OPENROUTER_API_URL,
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        json={
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        },
        timeout=OPENROUTER_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"{response.status_code} {response.reason_phrase} for url '{response.url}': {response.text[:300]}",
            request=response.request,
            response=response,
        )
    return _chat_completion_result("openrouter", None, response.json())


def _attempt_openrouter(prompt: str, max_tokens: int, errors: list[str]) -> str | None:
    if not settings.openrouter_api_key:
        return None
    try:
        text = _call_with_timeout(_try_openrouter, OPENROUTER_TIMEOUT_SECONDS, prompt, max_tokens)
        if text:
            return text
        errors.append("OpenRouter returned an empty response")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            # Free-tier cap is per-day (50/day with no credits bought) —
            # a retry sleep won't clear it, fall straight through instead.
            errors.append(f"OpenRouter rate-limited (likely today's free-tier request cap): {str(e)[:300]}")
        else:
            errors.append(f"OpenRouter request failed: {str(e)[:300]}")
    except Exception as e:
        errors.append(f"OpenRouter request failed: {str(e)[:300]}")
    return None


_PROVIDER_ATTEMPTS = {
    "groq": _attempt_groq, "gemini": _attempt_gemini, "claude": _attempt_claude,
    "browser_use": _attempt_browser_use, "openrouter": _attempt_openrouter,
}
PROVIDER_LABELS = {
    "groq": "Groq", "gemini": "Gemini", "claude": "Claude", "browser_use": "Browser Use", "openrouter": "OpenRouter",
}


def _provider_key(provider: str) -> str | None:
    return {
        "groq": settings.groq_api_key, "gemini": settings.gemini_api_key, "claude": settings.claude_api_key,
        "browser_use": settings.browser_use_api_key, "openrouter": settings.openrouter_api_key,
    }[provider]


def resolve_selected_provider() -> str:
    """The ONE provider every AI call in this request/job uses — the user's
    Report AI Provider selection (2026-09-28 spec). There is no provider
    order and no automatic choice: with nothing selected, or the selected
    provider's key missing, this raises instead of picking another one.
    Every caller already treats NoAIProviderConfigured as "this section's
    AI step failed", so the message reaches the job's issue list."""
    provider = getattr(_provider_preference, "value", None)
    if not provider:
        raise NoAIProviderConfigured(
            "No Report AI Provider selected — pick one in the Report AI Provider dropdown. "
            "No provider is chosen automatically."
        )
    if not _provider_key(provider):
        label = PROVIDER_LABELS[provider]
        raise NoAIProviderConfigured(
            f"{label} is selected but no {label} API key is configured — add it in Settings. "
            f"No fallback provider was used because {label} was selected."
        )
    return provider


def selected_provider_ready() -> bool:
    """Whether AI steps can run at all this request: a provider is
    selected and its key is configured. Replaces the old "any key
    configured" gates, which quietly assumed a provider order."""
    try:
        resolve_selected_provider()
    except NoAIProviderConfigured:
        return False
    return True


def failure_message(section: str, errors: list[str]) -> str:
    """User-facing error for an AI step that failed on the selected
    provider: the section, the REAL failure type (token limit / timeout /
    invalid JSON / provider error — never lumped together), and that no
    other provider was tried. See ai_usage.describe_failure."""
    provider = pinned_provider()
    if not provider:
        try:
            resolve_selected_provider()
        except NoAIProviderConfigured as e:
            return str(e)
    return ai_usage.describe_failure(section, errors, provider)


def _cache_key_for(provider: str, sent_prompt: str, extra: str) -> str | None:
    """Cache key for one paid-provider request, or None when this request
    shouldn't be cached (cache off, free provider, or a Claude call with no
    key to bill)."""
    if provider != "claude" or not ai_response_cache.enabled():
        return None
    model = _current_claude_model()
    effort = None if model.startswith("claude-haiku") else (getattr(_effort_override, "value", None) or CLAUDE_EFFORT)
    return ai_response_cache.make_key(provider, model, effort, ai_usage.current_module(), extra, sent_prompt)


def _usage_since(start: tuple[int, int]) -> tuple[int, int]:
    """(input, output) tokens the ledger recorded since `start` calls/tokens."""
    ledger = ai_usage.current_ledger()
    if ledger is None:
        return 0, 0
    with ledger._lock:
        calls = list(ledger.calls)
    new = calls[start[0]:]
    return sum(c["input_tokens"] for c in new), sum(c["output_tokens"] for c in new)


def _layered_attempts(provider: str, call, prompt: str, max_tokens: int, errors: list[str], max_attempts: int,
                      cache_extra: str = ""):
    """The one request layer every AI call goes through (2026-09-29 global
    token & failure spec). Per attempt: report hard-safety check, module
    output budget, provider call, usage recorded by the provider helper.
    Between attempts, the retry is shaped by the REAL failure reason:
    token limit -> retry asking for a shorter answer (Claude at low effort);
    invalid JSON -> retry with strict JSON instructions; timeout -> at most
    one retry; a daily/provider cap that won't clear -> stop. Never more
    than 1 + AI_CONFIG["max_retries"] attempts. Every error appended is
    tagged with its status so the final message states the exact reason.
    Yields text; a caller that accepts an answer stops iterating (that is
    what marks the module COMPLETED in the usage ledger)."""
    module = ai_usage.current_module()
    ledger = ai_usage.current_ledger()
    budget = ai_usage.output_budget(module, provider, max_tokens)
    last_status, suffix, timeouts, accepted = None, "", 0, False
    # ("cache"|"fresh", key, text, input_tokens, output_tokens) of the answer
    # most recently handed to the caller - what gets stored if it's accepted.
    last_given: tuple | None = None
    try:
        for _attempt in range(max_attempts):
            ai_usage.set_attempt(_attempt + 1)
            try:
                ai_usage.check_report_hard_limit()
            except ai_usage.SafetyLimitError as e:
                errors.append(f"[{AIStatus.FAILED}] {e}")
                last_status = AIStatus.FAILED
                break
            before = len(errors)
            _effort_override.value = "low" if last_status == AIStatus.TOKEN_LIMIT_EXCEEDED else None
            cache_key = None
            try:
                cache_key = _cache_key_for(provider, prompt + suffix, cache_extra)
                hit = ai_response_cache.get(cache_key) if cache_key else None
                if hit:
                    if ledger is not None:
                        ledger.record_cached_call(module, provider, _current_claude_model(),
                                                  hit["input_tokens"], hit["output_tokens"])
                    last_given = ("cache", cache_key, hit["response"], 0, 0)
                    yield hit["response"]
                    # Still iterating: the caller rejected the saved answer, so
                    # it is dropped and this attempt asks the provider afresh.
                    ai_response_cache.delete(cache_key)
                    last_given = None
                    if ledger is not None:
                        ledger.mark_last_call(AIStatus.INVALID_JSON)
                ledger_start = (len(ledger.calls) if ledger is not None else 0, 0)
                text = call(prompt + suffix, budget, errors)
            finally:
                _effort_override.value = None
            for i in range(before, len(errors)):
                errors[i] = ai_usage.tag_error(errors[i])
            if text:
                if cache_key:
                    used_in, used_out = _usage_since(ledger_start)
                    last_given = ("fresh", cache_key, text, used_in, used_out)
                yield text
                last_given = None
                # Still iterating: the caller rejected this answer, which was
                # paid for - record it as discarded so waste can be reported.
                if ledger is not None:
                    ledger.mark_calls_since(ledger_start[0], AIStatus.INVALID_JSON)
                if len(errors) > before:
                    errors[-1] = ai_usage.tag_error(errors[-1])
                    last_status = ai_usage.classify_error(errors[-1])
                else:
                    last_status = AIStatus.INVALID_JSON
            else:
                last_status = ai_usage.classify_error(errors[-1]) if len(errors) > before else AIStatus.FAILED
            if last_status == AIStatus.TIMEOUT:
                timeouts += 1
                if timeouts > ai_usage.AI_CONFIG["max_timeout_retries"]:
                    break
            if last_status == AIStatus.PROVIDER_ERROR and errors and ai_usage.is_daily_limit(errors[-1]):
                break
            suffix = ai_usage.RETRY_SUFFIX.get(last_status, "")
    except GeneratorExit:
        accepted = True
        if last_given and last_given[0] == "fresh":
            _kind, key, text, used_in, used_out = last_given
            ai_response_cache.put(key, module, provider, _current_claude_model(), text, used_in, used_out)
        raise
    finally:
        if ledger is not None:
            ledger.set_module_status(module, AIStatus.COMPLETED if accepted else (last_status or AIStatus.FAILED))


def _no_fallback_note(provider: str) -> str:
    label = PROVIDER_LABELS[provider]
    return f"No fallback provider was used because {label} was selected."


def pinned_provider() -> str | None:
    """The selected provider for this job's thread, or None if none set."""
    return getattr(_provider_preference, "value", None)


def groq_prompt_fits(prompt: str, max_tokens: int) -> bool:
    """Same budget check _try_groq applies before refusing a call — lets a
    caller with a big prompt shrink it BEFORE a Groq pin turns the refusal
    into a hard failure."""
    safe_max_tokens = max(256, min(max_tokens, GROQ_TPM_BUDGET - len(prompt) // 4))
    return safe_max_tokens >= max_tokens // 2


def iter_text_attempts(prompt: str, max_tokens: int, errors: list[str]):
    """Yields (text, provider) from the selected provider only (see
    resolve_selected_provider), through the shared request layer
    (_layered_attempts): up to 1 + max_retries answers, each retry shaped
    by why the previous one failed or was rejected. Never moves to another
    provider. When attempts are exhausted, `errors` ends with a "No
    fallback provider was used" note naming the selected provider."""
    provider = resolve_selected_provider()
    attempt = _PROVIDER_ATTEMPTS[provider]
    for text in _layered_attempts(
        provider, attempt, prompt, max_tokens, errors, 1 + ai_usage.AI_CONFIG["max_retries"],
    ):
        yield text, provider
    errors.append(_no_fallback_note(provider))


def generate_text(prompt: str, max_tokens: int = 4096) -> tuple[str, str]:
    """Returns (text, provider) from the selected provider only, via the
    shared request layer (retries shaped by the failure reason). Raises
    NoAIProviderConfigured with the tagged errors (plus a "No fallback
    provider was used" note) if every attempt fails."""
    errors: list[str] = []
    for text, provider in iter_text_attempts(prompt, max_tokens, errors):
        return text, provider
    # " | " joins these: a raw provider error can itself contain " / "
    # (Groq's org id in its JSON body), " | " essentially never appears.
    raise NoAIProviderConfigured(" | ".join(errors))


# Free-tier vision model — GROQ_MODEL (openai/gpt-oss-120b) is text-only,
# but Qwen 3.6 27B is natively multimodal and available on the same free
# Groq account/key already used for text calls, no new signup or paid key
# needed. Tried before Gemini/Claude for the same reason generate_text()
# tries Groq first: its per-minute budget recovers fast, so spending it
# first keeps Gemini's scarce once-daily allowance in reserve for when
# Groq is genuinely tapped out.
# Previously meta-llama/llama-4-scout-17b-16e-instruct, deprecated by Groq
# 2026-07-17 (404 model_not_found) — confirmed live 2026-09-12 forcing
# every vision call onto Gemini and exhausting its daily quota. Swapped to
# Groq's own documented replacement, then qwen3.6-27b ITSELF started 404ing
# 2026-09-17 with "does not exist or you do not have access to it" — Groq's
# own docs still list it as supported, so this reads as a per-account
# entitlement/waitlist gate, not a renamed or retired model, and isn't
# something a code fix alone can guarantee around. qwen3.8-27b is Groq's
# other currently-documented vision model (same docs page) — tried second,
# in case entitlement differs per model on this account, before falling all
# the way to Gemini's much scarcer daily quota.
GROQ_VISION_MODELS = ("qwen/qwen3.6-27b", "qwen/qwen3.8-27b")
GROQ_VISION_TIMEOUT_SECONDS = 45


def _try_groq_vision(prompt: str, images: list[tuple[bytes, str]], max_tokens: int) -> str:
    # +300 per image covers each image's own token cost, which the char/4
    # estimate below (sized for text-only prompts) can't see at all — a
    # single-image budget of +300 badly undercounts once a caller sends
    # several (the UI-Level Fixes rebuild's 4-screenshot analysis call).
    estimated_prompt_tokens = len(prompt) // 4 + 300 * len(images)
    safe_max_tokens = max(256, min(max_tokens, GROQ_TPM_BUDGET - estimated_prompt_tokens))
    if safe_max_tokens < max_tokens // 2:
        raise RuntimeError(
            f"prompt+image(s) too large for Groq's shared TPM budget to leave room for the requested "
            f"output ({safe_max_tokens} available vs {max_tokens} needed)"
        )
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"}}
        for image_bytes, mime_type in images
    ]
    model_errors: list[str] = []
    for model in GROQ_VISION_MODELS:
        _reserve_groq_budget(estimated_prompt_tokens + safe_max_tokens)
        response = httpx.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": safe_max_tokens,
                # Both Qwen vision models only accept "none" or "default" for
                # reasoning_effort (unlike gpt-oss-120b's low/medium/high) —
                # see the same reasoning-eats-max_tokens note on _try_groq.
                "reasoning_effort": "none",
            },
            timeout=60,
        )
        if response.status_code == 404:
            # Model not found/not entitled on this account — try the next
            # Groq vision model before giving up on Groq entirely.
            model_errors.append(f"{model}: {response.status_code} {response.text[:200]}")
            continue
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{response.status_code} {response.reason_phrase} for url '{response.url}': {response.text[:300]}",
                request=response.request,
                response=response,
            )
        return _chat_completion_result("groq", model, response.json())
    raise RuntimeError(f"no Groq vision model available on this account: {'; '.join(model_errors)}")


_VISION_MAX_DIM_PX = 7900  # Anthropic hard-rejects any image over 8000px on either side


def _cap_image_dimensions(image_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """A Playwright full_page=True screenshot of a long homepage can exceed
    8000px tall (e.g. UI-Level Fixes' 4-screenshot capture, ui_audit_capture.py)
    — Claude's vision API then rejects it outright with a 400 before this
    call's own paid, last-resort fallback ever gets a chance to run
    (confirmed real, Geopits regen 2026-09-26: 'image dimensions exceed max
    allowed size: 8000 pixels', after Groq timed out and Gemini's daily
    quota was exhausted — all three providers failed and the whole slide
    was dropped). Downscales in place, once, before any provider sees the
    image, so Groq/Gemini/Claude all get the same safe payload instead of
    branching per provider. Falls back to the original bytes on any decode
    failure so a corrupt capture still reaches the provider's own error
    handling rather than silently vanishing here."""
    try:
        from PIL import Image

        img = Image.open(BytesIO(image_bytes))
        if max(img.size) <= _VISION_MAX_DIM_PX:
            return image_bytes, mime_type
        ratio = _VISION_MAX_DIM_PX / max(img.size)
        new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        resized = img.resize(new_size, Image.LANCZOS)
        out = BytesIO()
        resized.save(out, format="PNG")
        return out.getvalue(), "image/png"
    except Exception:
        return image_bytes, mime_type


def _try_gemini_vision(prompt: str, images: list[tuple[bytes, str]]) -> str:
    client = genai.Client(api_key=settings.gemini_api_key)
    image_parts = [genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type) for image_bytes, mime_type in images]
    response = client.models.generate_content(model=GEMINI_MODEL, contents=[*image_parts, prompt])
    return _gemini_result(response)


def _try_claude_vision(prompt: str, images: list[tuple[bytes, str]], max_tokens: int) -> str:
    client = Anthropic(api_key=settings.claude_api_key)
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": base64.b64encode(image_bytes).decode()}}
        for image_bytes, mime_type in images
    ] + [{"type": "text", "text": prompt}]
    model = _current_claude_model()
    started = time.monotonic()
    response = client.messages.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        **_claude_output_options(model, max_tokens),
    )
    _record_claude_usage("vision", response.usage.input_tokens, response.usage.output_tokens)
    return _claude_result(response, model, time.monotonic() - started)


def _attempt_groq_vision(prompt: str, images: list[tuple[bytes, str]], max_tokens: int, errors: list[str]) -> str | None:
    if not settings.groq_api_key:
        return None
    try:
        text = _call_with_timeout(_try_groq_vision, GROQ_VISION_TIMEOUT_SECONDS, prompt, images, max_tokens)
        if text:
            return text
        errors.append("Groq returned an empty response")
    except httpx.HTTPStatusError as e:
        # Same per-minute-vs-daily distinction _attempt_groq already
        # makes for text calls — a burst of vision calls (UI-Level
        # Fixes + Onboarding Breakdown back to back) can trip Groq's
        # per-minute cap even when the account's daily budget is fine.
        if e.response.status_code == 429:
            retry_after = _groq_retry_after_seconds(e.response)
            if retry_after is not None and retry_after > RATE_LIMIT_RETRY_DELAY_SECONDS:
                errors.append(f"Groq vision rate-limited, not retrying (Retry-After {retry_after:.0f}s): {str(e)[:200]}")
            else:
                time.sleep(RATE_LIMIT_RETRY_DELAY_SECONDS)
                try:
                    text = _call_with_timeout(_try_groq_vision, GROQ_VISION_TIMEOUT_SECONDS, prompt, images, max_tokens)
                    if text:
                        return text
                    errors.append("Groq returned an empty response")
                except Exception as e2:
                    errors.append(f"Groq vision request failed: {str(e2)[:300]}")
        else:
            errors.append(f"Groq vision request failed: {str(e)[:300]}")
    except Exception as e:
        errors.append(f"Groq vision request failed: {str(e)[:300]}")
    return None


def _attempt_gemini_vision(prompt: str, images: list[tuple[bytes, str]], max_tokens: int, errors: list[str]) -> str | None:
    if not settings.gemini_api_key:
        return None
    # Vision calls share the SAME Gemini project/account as text calls
    # — same 10 RPM / 250 RPD budget, same _reserve_gemini_slot counters,
    # not a separate pool (see _attempt_gemini's own use of this).
    if not _reserve_gemini_slot():
        errors.append("Gemini vision skipped — this process's own daily request-count budget looks spent")
        return None
    try:
        text = _call_with_timeout(_try_gemini_vision, GEMINI_TIMEOUT_SECONDS, prompt, images)
        if text:
            return text
        errors.append("Gemini returned an empty response")
    except Exception as e:
        # Same retry _attempt_gemini already does for text calls — a
        # transient "servers are temporarily unavailable" or a
        # per-minute RESOURCE_EXHAUSTED both recover inside a short
        # wait; a daily-quota exhaustion won't, so don't bother there.
        error_text = str(e)
        is_daily_quota = "PerDay" in error_text or "free_tier" in error_text.lower()
        is_transient = "UNAVAILABLE" in error_text or "temporarily unavailable" in error_text.lower()
        if not is_daily_quota and (is_transient or "RESOURCE_EXHAUSTED" in error_text or "429" in error_text):
            time.sleep(RATE_LIMIT_RETRY_DELAY_SECONDS)
            if not _reserve_gemini_slot():
                errors.append("Gemini vision skipped retry — daily request-count budget looks spent")
            else:
                try:
                    text = _call_with_timeout(_try_gemini_vision, GEMINI_TIMEOUT_SECONDS, prompt, images)
                    if text:
                        return text
                    errors.append("Gemini returned an empty response")
                except Exception as e2:
                    errors.append(friendly_gemini_error(e2))
        else:
            errors.append(friendly_gemini_error(e))
    return None


def _attempt_claude_vision(prompt: str, images: list[tuple[bytes, str]], max_tokens: int, errors: list[str]) -> str | None:
    if not settings.claude_api_key:
        return None
    try:
        text = _call_with_timeout(_try_claude_vision, CLAUDE_VISION_TIMEOUT_SECONDS, prompt, images, max_tokens)
        if text:
            return text
        errors.append("Claude returned an empty response")
    except Exception as e:
        if isinstance(e, TimeoutError):
            ai_usage.note_timeout()
        errors.append(f"Claude vision request failed: {str(e)[:300]}")
    return None


def _try_openrouter_vision(prompt: str, images: list[tuple[bytes, str]], max_tokens: int) -> str:
    # openrouter/free routes to a free model that supports the request's
    # input modalities, so image parts steer it to a vision-capable one.
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"}}
        for image_bytes, mime_type in images
    ]
    response = httpx.post(
        OPENROUTER_API_URL,
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        json={"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens},
        timeout=OPENROUTER_VISION_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"{response.status_code} {response.reason_phrase} for url '{response.url}': {response.text[:300]}",
            request=response.request,
            response=response,
        )
    return _chat_completion_result("openrouter", None, response.json())


def _attempt_openrouter_vision(prompt: str, images: list[tuple[bytes, str]], max_tokens: int, errors: list[str]) -> str | None:
    if not settings.openrouter_api_key:
        return None
    try:
        text = _call_with_timeout(_try_openrouter_vision, OPENROUTER_VISION_TIMEOUT_SECONDS, prompt, images, max_tokens)
        if text:
            return text
        errors.append("OpenRouter vision returned an empty response")
    except Exception as e:
        errors.append(f"OpenRouter vision request failed: {str(e)[:300]}")
    return None


def _attempt_browser_use_vision(
    prompt: str, images: list[tuple[bytes, str]], max_tokens: int, errors: list[str], start_url: str | None = None,
) -> str | None:
    """Browser Use takes no image input, but it IS a browser agent — it
    opens the page itself instead of reading our screenshots (2026-09-28:
    a Browser Use pin used to fail the whole UI-Level Fixes pass)."""
    if not settings.browser_use_api_key:
        return None
    task = (
        "You cannot see the screenshots this task mentions. Open the website yourself instead — look at the "
        "first screen and scroll the full homepage at desktop width, then again at a mobile-width viewport — "
        "and base every finding on what you actually see there.\n\n" + prompt
    )
    try:
        text = _try_browser_use(task, BROWSER_USE_VISION_TIMEOUT_SECONDS, start_url)
        if text:
            return text
        errors.append("Browser Use returned an empty response")
    except Exception as e:
        errors.append(f"Browser Use request failed: {str(e)[:300]}")
    return None


_VISION_PROVIDER_ATTEMPTS = {
    "groq": _attempt_groq_vision,
    "gemini": _attempt_gemini_vision,
    "claude": _attempt_claude_vision,
    "openrouter": _attempt_openrouter_vision,
    "browser_use": _attempt_browser_use_vision,
}


def _images_fingerprint(images: list[tuple[bytes, str]]) -> str:
    import hashlib

    h = hashlib.sha256()
    for data, mime in images:
        h.update(mime.encode())
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def iter_text_with_images_attempts(
    prompt: str, images: list[tuple[bytes, str]], max_tokens: int, errors: list[str], start_url: str | None = None,
):
    """Vision counterpart to iter_text_attempts — screenshots go to the
    selected provider only, never another one. Every provider in the Report
    AI Provider dropdown has a vision path; Browser Use takes no images, so
    its agent opens `start_url` itself instead."""
    provider = resolve_selected_provider()
    images = [_cap_image_dimensions(b, m) for b, m in images]
    # Two tries on the selected provider — there is no other provider to
    # move to, and one bad-JSON response shouldn't end the whole pass.
    # Browser Use gets one: each try is a billed agent run lasting minutes.
    if provider == "browser_use":
        def call(p, mt, errs):
            return _attempt_browser_use_vision(p, images, mt, errs, start_url)
    else:
        def call(p, mt, errs):
            return _VISION_PROVIDER_ATTEMPTS[provider](p, images, mt, errs)
    attempts = 1 if provider == "browser_use" else 1 + ai_usage.AI_CONFIG["max_retries"]
    for text in _layered_attempts(provider, call, prompt, max_tokens, errors, attempts,
                                  cache_extra="images:" + _images_fingerprint(images)):
        yield text, provider
    errors.append(_no_fallback_note(provider))


def generate_text_with_images(prompt: str, images: list[tuple[bytes, str]], max_tokens: int = 2048) -> tuple[str, str]:
    """Same as generate_text(), for a prompt grounded in screenshots.
    `images` is a list of (image_bytes, mime_type) pairs sent together in
    one call, to the selected provider only. For a caller that parses the
    answer itself, iterate iter_text_with_images_attempts() instead."""
    errors: list[str] = []
    for text, provider in iter_text_with_images_attempts(prompt, images, max_tokens, errors):
        return text, provider
    raise NoAIProviderConfigured(" | ".join(errors))


def generate_text_with_image(prompt: str, image_bytes: bytes, mime_type: str = "image/png", max_tokens: int = 2048) -> tuple[str, str]:
    """Single-image convenience wrapper over generate_text_with_images —
    every existing caller of this (Onboarding Breakdown, single-screenshot
    UI-Level Fixes) keeps working unchanged."""
    return generate_text_with_images(prompt, [(image_bytes, mime_type)], max_tokens)
