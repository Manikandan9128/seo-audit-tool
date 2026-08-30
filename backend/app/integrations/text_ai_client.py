"""Unified text-generation call that tries whichever AI provider has a
configured key — Gemini first (existing default), then Groq (free-tier,
much higher RPM — covers Gemini's per-minute burst limit), then Claude as
a last, paid fallback — so callers (company overview extraction, AI
summary, competitor narrative, core problem, keyword clustering) work as
long as *any* key is set, without provider-specific branching at each
call site."""

import httpx
from anthropic import Anthropic
from google import genai

from app.config import settings
from app.integrations.gemini_errors import friendly_gemini_error

GEMINI_MODEL = "gemini-3.6-flash"
GROQ_MODEL = "llama-3.3-70b-versatile"
CLAUDE_MODEL = "claude-sonnet-5"

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


class NoAIProviderConfigured(Exception):
    pass


def _try_gemini(prompt: str) -> str:
    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return (response.text or "").strip()


def _try_groq(prompt: str, max_tokens: int) -> str:
    response = httpx.post(
        GROQ_API_URL,
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


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

    if settings.gemini_api_key:
        try:
            text = _try_gemini(prompt)
            if text:
                return text, "gemini"
            errors.append("Gemini returned an empty response")
        except Exception as e:
            errors.append(friendly_gemini_error(e))

    if settings.groq_api_key:
        try:
            text = _try_groq(prompt, max_tokens)
            if text:
                return text, "groq"
            errors.append("Groq returned an empty response")
        except Exception as e:
            errors.append(f"Groq request failed: {str(e)[:300]}")

    if settings.claude_api_key:
        try:
            text = _try_claude(prompt, max_tokens)
            if text:
                return text, "claude"
            errors.append("Claude returned an empty response")
        except Exception as e:
            errors.append(f"Claude request failed: {str(e)[:300]}")

    raise NoAIProviderConfigured(" / ".join(errors))
