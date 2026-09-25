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

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

import base64

import httpx
from anthropic import Anthropic
from google import genai
from google.genai import types as genai_types

from app.config import settings
from app.integrations.gemini_errors import friendly_gemini_error

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
CLAUDE_TIMEOUT_SECONDS = 60


def _call_with_timeout(fn, timeout_seconds: float, *args, **kwargs):
    """Runs fn in a worker thread and enforces a hard wall-clock timeout,
    regardless of whether the underlying SDK exposes (or honors) its own
    timeout — confirmed real: a Gemini call with no client-side timeout
    hung an entire report generation for 30+ minutes on one single AI call
    with nothing to stop it, even though a quota/rate-limit rejection
    normally comes back near-instantly (this was Google's servers being
    slow to respond, not a fast reject). Raises TimeoutError on expiry,
    which every caller's existing `except Exception` handling already
    treats the same as any other provider failure — falls through to the
    next provider instead of hanging the whole pipeline. The orphaned
    thread is abandoned (not killed — Python has no API for that) rather
    than waited on; it either eventually finishes harmlessly in the
    background or the process exits, whichever comes first."""
    pool = ThreadPoolExecutor(max_workers=1)
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
            # serve-at-all check runs before this and already routes a
            # request that can never fit to the next provider instead.
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


def _try_gemini(prompt: str) -> str:
    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return (response.text or "").strip()


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
            f"output ({safe_max_tokens} available vs {max_tokens} needed) — skipping to next provider"
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
    data = response.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


def _groq_retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _try_claude(prompt: str, max_tokens: int) -> str:
    client = Anthropic(api_key=settings.claude_api_key)
    response = client.messages.create(
        model=_current_claude_model(),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    _record_claude_usage("text", response.usage.input_tokens, response.usage.output_tokens)
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        # A 200 with no text block (safety stop, or the model stopping
        # before writing anything) isn't a transport failure, so it never
        # hit the except branch below — confirmed real on a Geopits Core
        # Problem slide (2026-09-24): Claude answered, wrote nothing, and
        # the old code just logged "empty response" with no way to tell
        # a genuine one-off from a real refusal. stop_reason is the one
        # field that tells them apart, so surface it instead of guessing.
        raise RuntimeError(f"empty content, stop_reason={response.stop_reason}")
    return text


# Lets a caller pin one provider first for the lifetime of a single job's
# thread (e.g. "run this report with Claude") without threading a
# preferred_provider parameter through the ~10 call sites between the
# report-generation route and generate_text() — every one of those calls
# happens synchronously within one dedicated thread per job/request (see
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


# Same thread-local-per-job pattern as _provider_preference above, one
# level down: which Claude model this job's Claude calls use, independent
# of whether Claude was even picked as the preferred provider (it still
# applies on the fallback path). Reset to None (falls back to CLAUDE_MODEL)
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
    if not settings.claude_api_key:
        return None
    try:
        text = _call_with_timeout(_try_claude, CLAUDE_TIMEOUT_SECONDS, prompt, max_tokens)
        if text:
            return text
    except Exception as e:
        # One immediate retry before giving up — an empty/refused response
        # is a per-request roll, not a persistent outage like Groq/Gemini's
        # quota errors, so a second attempt on the same borderline prompt
        # often just succeeds (same reasoning as Groq/Gemini's retry above).
        try:
            text = _call_with_timeout(_try_claude, CLAUDE_TIMEOUT_SECONDS, prompt, max_tokens)
            if text:
                return text
            errors.append("Claude returned an empty response")
        except Exception as e2:
            errors.append(f"Claude request failed (retried once): {str(e2)[:300]}")
    return None


BROWSER_USE_API_URL = "https://api.browser-use.com/api/v4/runs"
BROWSER_USE_MODEL = "gpt-5.6-luna"
BROWSER_USE_TIMEOUT_SECONDS = 120
BROWSER_USE_POLL_INTERVAL_SECONDS = 3


def _try_browser_use(prompt: str) -> str:
    """Runs the prompt as a Browser Use Cloud agent run (no startUrl — a
    plain reasoning task, not browsing a specific site) and polls until it
    finishes. Much slower and pricier than a chat completion (a real
    billed agent run, not a token-based call) — not in
    _DEFAULT_PROVIDER_ORDER, only ever tried when explicitly preferred."""
    headers = {"X-Browser-Use-API-Key": settings.browser_use_api_key}
    response = httpx.post(
        BROWSER_USE_API_URL,
        headers=headers,
        json={"task": prompt, "model": BROWSER_USE_MODEL},
        timeout=30,
    )
    response.raise_for_status()
    run_id = response.json()["id"]

    deadline = time.monotonic() + BROWSER_USE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(BROWSER_USE_POLL_INTERVAL_SECONDS)
        status_response = httpx.get(f"{BROWSER_USE_API_URL}/{run_id}", headers=headers, timeout=30)
        status_response.raise_for_status()
        data = status_response.json()
        status = data.get("status")
        if status == "completed":
            return (data.get("result") or "").strip()
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"Browser Use run {status}: {data.get('error') or 'no error detail'}")
    raise TimeoutError(f"Browser Use run did not finish within {BROWSER_USE_TIMEOUT_SECONDS}s")


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
    data = response.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


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
# browser_use deliberately excluded from the default order — real billed
# agent run, seconds-to-minutes latency, not a fit to try on every report
# by default. Only reached via set_preferred_provider("browser_use").
# openrouter likewise excluded: its free tier (50 requests/day) was already
# tried in the default chain and removed (4ceb14d) for running out mid-
# report — re-added 2026-09-23 as an explicit pick only.
_DEFAULT_PROVIDER_ORDER = ["groq", "gemini", "claude"]


def _provider_order() -> list[str]:
    preferred = getattr(_provider_preference, "value", None)
    return [preferred] + [p for p in _DEFAULT_PROVIDER_ORDER if p != preferred] if preferred else _DEFAULT_PROVIDER_ORDER


def iter_text_attempts(prompt: str, max_tokens: int, errors: list[str]):
    """Same provider order/fallback as generate_text, but yields (text,
    provider) for EVERY configured provider that returned a non-empty raw
    response, instead of stopping at the first one (generate_text() calls
    this and returns just the first yield, unchanged behavior for every
    existing caller).

    Exists for a caller whose OWN parse of that text can turn out
    semantically empty even though the provider responded successfully —
    confirmed real (2026-09-20, Lumber + BharatBenz reports both hit this
    on the Structured Data & Schema Validator's Key Insights): Groq
    (first in the default order) returned syntactically valid JSON with an
    empty `{"insights": []}`, which generate_text() correctly counts as
    success (non-empty raw text) — but that's an inadequate result for the
    caller's actual need, and generate_text() has no way to know that,
    since it doesn't parse the caller's business-logic JSON shape at all.
    A caller who wants "try the next provider if MY parse of this came back
    empty" iterates this generator instead of calling generate_text() once.

    `errors` is appended to by the same _attempt_* functions generate_text()
    uses — pass the same list in both to see every attempt's failure
    reason if every yield turns out inadequate too. Raises
    NoAIProviderConfigured (on first iteration) only when no provider key
    is configured at all; yields nothing if every configured provider's
    raw call itself failed or returned empty (same as generate_text()
    raising NoAIProviderConfigured with `errors` joined)."""
    if not (settings.gemini_api_key or settings.groq_api_key or settings.claude_api_key or settings.browser_use_api_key or settings.openrouter_api_key):
        raise NoAIProviderConfigured("No Groq, Gemini, Claude, Browser Use, or OpenRouter API key configured — add one in Settings")
    for provider in _provider_order():
        text = _PROVIDER_ATTEMPTS[provider](prompt, max_tokens, errors)
        if text:
            yield text, provider


def generate_text(prompt: str, max_tokens: int = 4096) -> tuple[str, str]:
    """Returns (text, provider_used) — 'groq', 'gemini', or 'claude'. Tries
    each configured provider in order, falling through to the next on any
    failure (not configured, empty response, request error). Default order
    is Groq, Gemini, Claude — see set_preferred_provider() to move one
    provider to the front of that order for the current thread (e.g. one
    report-generation job). Raises NoAIProviderConfigured if no key is set
    at all, or if every configured provider's call failed (message
    includes each provider's error). max_tokens only affects the
    Groq/Claude paths — Gemini has no equivalent cap exposed here and just
    returns whatever it generates."""
    if not (settings.gemini_api_key or settings.groq_api_key or settings.claude_api_key or settings.browser_use_api_key or settings.openrouter_api_key):
        raise NoAIProviderConfigured("No Groq, Gemini, Claude, Browser Use, or OpenRouter API key configured — add one in Settings")

    preferred = getattr(_provider_preference, "value", None)
    order = _DEFAULT_PROVIDER_ORDER
    if preferred:
        order = [preferred] + [p for p in _DEFAULT_PROVIDER_ORDER if p != preferred]

    errors: list[str] = []
    for provider in order:
        text = _PROVIDER_ATTEMPTS[provider](prompt, max_tokens, errors)
        if text:
            return text, provider

    # " / " used to join these (confirmed real, 2026-09-19): a raw provider
    # error can itself legitimately contain " / " (Groq's own org id in its
    # JSON body, e.g. ".../ organization `org_...`"), and that string is
    # truncated separately per-provider above — a truncation cut can land
    # right next to that separator, making one provider's cut-off message
    # visually run straight into the NEXT provider's message with no
    # readable boundary (looked, on a real report, like Gemini's quota text
    # was somehow embedded INSIDE Groq's own error body). Each message
    # already names its own provider ("Groq ...", "Gemini ...", "Claude
    # ...") — " | " is a character that essentially never appears inside
    # real provider error text, so it can't be confused with content, only
    # ever read as this join's own separator.
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
            f"output ({safe_max_tokens} available vs {max_tokens} needed) — skipping to next provider"
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
        data = response.json()
        return (data["choices"][0]["message"]["content"] or "").strip()
    raise RuntimeError(f"no Groq vision model available on this account: {'; '.join(model_errors)}")


def _try_gemini_vision(prompt: str, images: list[tuple[bytes, str]]) -> str:
    client = genai.Client(api_key=settings.gemini_api_key)
    image_parts = [genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type) for image_bytes, mime_type in images]
    response = client.models.generate_content(model=GEMINI_MODEL, contents=[*image_parts, prompt])
    return (response.text or "").strip()


def _try_claude_vision(prompt: str, images: list[tuple[bytes, str]], max_tokens: int) -> str:
    client = Anthropic(api_key=settings.claude_api_key)
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": base64.b64encode(image_bytes).decode()}}
        for image_bytes, mime_type in images
    ] + [{"type": "text", "text": prompt}]
    response = client.messages.create(
        model=_current_claude_model(),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
    )
    _record_claude_usage("vision", response.usage.input_tokens, response.usage.output_tokens)
    return "".join(block.text for block in response.content if block.type == "text").strip()


def generate_text_with_images(prompt: str, images: list[tuple[bytes, str]], max_tokens: int = 2048) -> tuple[str, str]:
    """Same fallback shape as generate_text(), but for a prompt grounded in
    one or more real screenshots (e.g. the client's own homepage at
    several viewports) instead of text alone. `images` is a list of
    (image_bytes, mime_type) pairs, sent together in a single vision call
    — not one call per image. GROQ_MODEL itself is text-only, but the same
    free Groq key also reaches Groq's vision models (see GROQ_VISION_
    MODELS/_try_groq_vision above), tried first for the same fast-
    recovery-budget reason generate_text() tries Groq first — Gemini,
    then Claude follow as before. Raises NoAIProviderConfigured if no key
    is set or every call fails."""
    if not (settings.groq_api_key or settings.gemini_api_key or settings.claude_api_key):
        raise NoAIProviderConfigured("No Groq, Gemini, or Claude API key configured — vision calls need one of these")

    errors: list[str] = []
    if settings.groq_api_key:
        try:
            text = _call_with_timeout(_try_groq_vision, GROQ_VISION_TIMEOUT_SECONDS, prompt, images, max_tokens)
            if text:
                return text, "groq"
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
                            return text, "groq"
                        errors.append("Groq returned an empty response")
                    except Exception as e2:
                        errors.append(f"Groq vision request failed: {str(e2)[:300]}")
            else:
                errors.append(f"Groq vision request failed: {str(e)[:300]}")
        except Exception as e:
            errors.append(f"Groq vision request failed: {str(e)[:300]}")
    if settings.gemini_api_key:
        # Vision calls share the SAME Gemini project/account as text calls
        # — same 10 RPM / 250 RPD budget, same _reserve_gemini_slot counters,
        # not a separate pool (see _attempt_gemini's own use of this).
        if not _reserve_gemini_slot():
            errors.append("Gemini vision skipped — this process's own daily request-count budget looks spent")
        else:
            try:
                text = _call_with_timeout(_try_gemini_vision, GEMINI_TIMEOUT_SECONDS, prompt, images)
                if text:
                    return text, "gemini"
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
                                return text, "gemini"
                            errors.append("Gemini returned an empty response")
                        except Exception as e2:
                            errors.append(friendly_gemini_error(e2))
                else:
                    errors.append(friendly_gemini_error(e))
    if settings.claude_api_key:
        try:
            text = _call_with_timeout(_try_claude_vision, CLAUDE_TIMEOUT_SECONDS, prompt, images, max_tokens)
            if text:
                return text, "claude"
            errors.append("Claude returned an empty response")
        except Exception as e:
            errors.append(f"Claude vision request failed: {str(e)[:300]}")

    raise NoAIProviderConfigured(" / ".join(errors))


def generate_text_with_image(prompt: str, image_bytes: bytes, mime_type: str = "image/png", max_tokens: int = 2048) -> tuple[str, str]:
    """Single-image convenience wrapper over generate_text_with_images —
    every existing caller of this (Onboarding Breakdown, single-screenshot
    UI-Level Fixes) keeps working unchanged."""
    return generate_text_with_images(prompt, [(image_bytes, mime_type)], max_tokens)
