"""Unified text-generation call that tries whichever AI provider has a
configured key — xAI (Grok) first, then Gemini, then Claude as a last, paid
fallback — so callers (company overview extraction, AI summary,
competitor narrative, core problem, keyword clustering) work as long as
*any* key is set, without provider-specific branching at each call site.

xAI goes first deliberately (confirmed real-world tradeoff, not the
original default): Gemini's free-tier quota resets once every 24 hours, so
burning it first on every call exhausts it early in the day and it then
sits useless in reserve while xAI alone (with its own, faster-recovering
per-minute/hourly limits) idles unused until Gemini fails. Trying xAI
first spends the fast-recovering resource first and keeps Gemini's scarce
daily allowance in reserve for when xAI is genuinely, if temporarily,
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
XAI_MODEL = "grok-4"
CLAUDE_MODEL = "claude-sonnet-5"

XAI_API_URL = "https://api.x.ai/v1/chat/completions"

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
# xAI — with no spacing, that's enough back-to-back requests to burst past
# xAI's per-minute rate limit, and the existing single 20s retry on 429
# isn't always enough once several calls are already queued right behind
# each other. This tracks the last xAI call's timestamp process-wide and
# waits out the minimum interval before the next one, so xAI calls spread
# out across the run instead of bursting.
_xai_pacing_lock = threading.Lock()
_last_xai_call_at: float | None = None
_XAI_MIN_INTERVAL_SECONDS = 4.0


class NoAIProviderConfigured(Exception):
    pass


def _try_gemini(prompt: str) -> str:
    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return (response.text or "").strip()


def _wait_for_xai_slot():
    """Blocks until at least _XAI_MIN_INTERVAL_SECONDS have passed since
    the last xAI call anywhere in this process."""
    global _last_xai_call_at
    with _xai_pacing_lock:
        if _last_xai_call_at is not None:
            elapsed = time.monotonic() - _last_xai_call_at
            if elapsed < _XAI_MIN_INTERVAL_SECONDS:
                time.sleep(_XAI_MIN_INTERVAL_SECONDS - elapsed)
        _last_xai_call_at = time.monotonic()


def _try_xai(prompt: str, max_tokens: int) -> str:
    _wait_for_xai_slot()
    response = httpx.post(
        XAI_API_URL,
        headers={"Authorization": f"Bearer {settings.xai_api_key}"},
        json={
            "model": XAI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


def _xai_retry_after_seconds(response: httpx.Response) -> float | None:
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
    """Returns (text, provider_used) — 'gemini', 'xai', or 'claude'. Tries
    each configured provider in that order, falling through to the next on
    any failure (not configured, empty response, request error). Raises
    NoAIProviderConfigured if no key is set at all, or if every configured
    provider's call failed (message includes each provider's error).
    max_tokens only affects the xAI/Claude paths — Gemini has no
    equivalent cap exposed here and just returns whatever it generates."""
    if not settings.gemini_api_key and not settings.xai_api_key and not settings.claude_api_key:
        raise NoAIProviderConfigured("No Gemini, xAI, or Claude API key configured — add one in Settings")

    errors = []

    if settings.xai_api_key:
        try:
            text = _try_xai(prompt, max_tokens)
            if text:
                return text, "xai"
            errors.append("xAI returned an empty response")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                retry_after = _xai_retry_after_seconds(e.response)
                # xAI's Retry-After tells us whether this 429 is a
                # per-minute limit (short, recovers inside our retry
                # window) or a daily/token-cap exhaustion (long, won't
                # recover in 20s) — same reasoning already applied to
                # Gemini's daily-quota case below. Confirmed real: with
                # both providers quota-exhausted, blindly retrying every
                # single xAI 429 after a 20s sleep (guaranteed to fail
                # again) was the main contributor to reports stalling for
                # minutes at the competitor-narrative AI call.
                if retry_after is not None and retry_after > RATE_LIMIT_RETRY_DELAY_SECONDS:
                    errors.append(f"xAI rate-limited, not retrying (Retry-After {retry_after:.0f}s): {str(e)[:200]}")
                else:
                    time.sleep(RATE_LIMIT_RETRY_DELAY_SECONDS)
                    try:
                        text = _try_xai(prompt, max_tokens)
                        if text:
                            return text, "xai"
                        errors.append("xAI returned an empty response")
                    except Exception as e2:
                        errors.append(f"xAI request failed: {str(e2)[:300]}")
            else:
                errors.append(f"xAI request failed: {str(e)[:300]}")
        except Exception as e:
            errors.append(f"xAI request failed: {str(e)[:300]}")

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
