"""Unified text-generation call that tries whichever AI provider has a
configured key — Groq first, then Gemini, then Claude as a last, paid
fallback — so callers (company overview extraction, AI summary,
competitor narrative, core problem, keyword clustering) work as long as
*any* key is set, without provider-specific branching at each call site.

Groq goes first deliberately (confirmed real-world tradeoff, not the
original default): Gemini's free-tier quota resets once every 24 hours, so
burning it first on every call exhausts it early in the day and it then
sits useless in reserve while Groq alone (with its own, faster-recovering
per-minute/hourly limits) idles unused until Gemini fails. Trying Groq
first spends the fast-recovering resource first and keeps Gemini's scarce
daily allowance in reserve for when Groq is genuinely, if temporarily,
tapped out."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

import httpx
from anthropic import Anthropic
from google import genai

from app.config import settings
from app.integrations.gemini_errors import friendly_gemini_error

RATE_LIMIT_RETRY_DELAY_SECONDS = 20

GEMINI_MODEL = "gemini-3.6-flash"
GROQ_MODEL = "openai/gpt-oss-120b"
CLAUDE_MODEL = "claude-sonnet-5"

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

# A single report generation makes 7-8 sequential AI calls (company
# overview, core problem, keyword clustering, up to 5 competitor
# narratives, next steps). Whenever Gemini's DAILY quota is exhausted
# (confirmed on a real report), every one of those calls falls through to
# Groq — with no spacing, that's enough back-to-back requests to burst past
# Groq's free-tier per-minute token limit, and the existing single 20s
# retry on 429 isn't always enough once several calls are already queued
# right behind each other. This tracks the last Groq call's timestamp
# process-wide and waits out the minimum interval before the next one, so
# Groq calls spread out across the run instead of bursting.
_groq_pacing_lock = threading.Lock()
_last_groq_call_at: float | None = None
_GROQ_MIN_INTERVAL_SECONDS = 4.0


class NoAIProviderConfigured(Exception):
    pass


def _try_gemini(prompt: str) -> str:
    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return (response.text or "").strip()


def _wait_for_groq_slot():
    """Blocks until at least _GROQ_MIN_INTERVAL_SECONDS have passed since
    the last Groq call anywhere in this process."""
    global _last_groq_call_at
    with _groq_pacing_lock:
        if _last_groq_call_at is not None:
            elapsed = time.monotonic() - _last_groq_call_at
            if elapsed < _GROQ_MIN_INTERVAL_SECONDS:
                time.sleep(_GROQ_MIN_INTERVAL_SECONDS - elapsed)
        _last_groq_call_at = time.monotonic()


# Some free-tier Groq orgs are capped as low as 8000 tokens-per-minute
# total (prompt + completion combined) — confirmed real: a 413 "Request
# too large" hit at prompt≈4900 + max_tokens=4096 defaulted by an unrelated
# caller. Callers pass max_tokens sized for the completion they actually
# need, with no idea what the prompt costs against this shared budget, so
# clamp here using a rough chars/4 token estimate rather than trusting the
# caller's number outright.
_GROQ_TPM_BUDGET = 7500  # stays under the observed 8000 cap with slack


def _try_groq(prompt: str, max_tokens: int) -> str:
    _wait_for_groq_slot()
    estimated_prompt_tokens = len(prompt) // 4
    safe_max_tokens = max(256, min(max_tokens, _GROQ_TPM_BUDGET - estimated_prompt_tokens))
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
    response = httpx.post(
        GROQ_API_URL,
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": safe_max_tokens,
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
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def generate_text(prompt: str, max_tokens: int = 4096) -> tuple[str, str]:
    """Returns (text, provider_used) — 'gemini', 'groq', or 'claude'. Tries
    each configured provider in that order, falling through to the next on
    any failure (not configured, empty response, request error). Raises
    NoAIProviderConfigured if no key is set at all, or if every configured
    provider's call failed (message includes each provider's error).
    max_tokens only affects the Groq/Claude paths — Gemini has no
    equivalent cap exposed here and just returns whatever it generates."""
    if not settings.gemini_api_key and not settings.groq_api_key and not settings.claude_api_key:
        raise NoAIProviderConfigured("No Gemini, Groq, or Claude API key configured — add one in Settings")

    errors = []

    if settings.groq_api_key:
        try:
            text = _try_groq(prompt, max_tokens)
            if text:
                return text, "groq"
            errors.append("Groq returned an empty response")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                retry_after = _groq_retry_after_seconds(e.response)
                # Groq's Retry-After tells us whether this 429 is a
                # per-minute limit (short, recovers inside our retry
                # window) or a daily/token-cap exhaustion (long, won't
                # recover in 20s) — same reasoning already applied to
                # Gemini's daily-quota case below. Confirmed real: with
                # both providers quota-exhausted, blindly retrying every
                # single Groq 429 after a 20s sleep (guaranteed to fail
                # again) was the main contributor to reports stalling for
                # minutes at the competitor-narrative AI call.
                if retry_after is not None and retry_after > RATE_LIMIT_RETRY_DELAY_SECONDS:
                    errors.append(f"Groq rate-limited, not retrying (Retry-After {retry_after:.0f}s): {str(e)[:200]}")
                else:
                    time.sleep(RATE_LIMIT_RETRY_DELAY_SECONDS)
                    try:
                        text = _try_groq(prompt, max_tokens)
                        if text:
                            return text, "groq"
                        errors.append("Groq returned an empty response")
                    except Exception as e2:
                        errors.append(f"Groq request failed: {str(e2)[:300]}")
            else:
                errors.append(f"Groq request failed: {str(e)[:300]}")
        except Exception as e:
            errors.append(f"Groq request failed: {str(e)[:300]}")

    if settings.gemini_api_key:
        try:
            text = _call_with_timeout(_try_gemini, GEMINI_TIMEOUT_SECONDS, prompt)
            if text:
                return text, "gemini"
            errors.append("Gemini returned an empty response")
        except Exception as e:
            error_text = str(e)
            is_daily_quota = "PerDay" in error_text or "free_tier" in error_text.lower()
            # A burst of concurrent calls (e.g. one per competitor) can all hit
            # Gemini's per-minute cap in the same instant — one short wait and
            # retry is often enough since the window is per-minute, not daily.
            # A daily-quota exhaustion won't clear in 20s, so don't bother.
            if not is_daily_quota and ("RESOURCE_EXHAUSTED" in error_text or "429" in error_text):
                time.sleep(RATE_LIMIT_RETRY_DELAY_SECONDS)
                try:
                    text = _call_with_timeout(_try_gemini, GEMINI_TIMEOUT_SECONDS, prompt)
                    if text:
                        return text, "gemini"
                    errors.append("Gemini returned an empty response")
                except Exception as e2:
                    errors.append(friendly_gemini_error(e2))
            else:
                errors.append(friendly_gemini_error(e))

    if settings.claude_api_key:
        try:
            text = _call_with_timeout(_try_claude, CLAUDE_TIMEOUT_SECONDS, prompt, max_tokens)
            if text:
                return text, "claude"
            errors.append("Claude returned an empty response")
        except Exception as e:
            errors.append(f"Claude request failed: {str(e)[:300]}")

    raise NoAIProviderConfigured(" / ".join(errors))
