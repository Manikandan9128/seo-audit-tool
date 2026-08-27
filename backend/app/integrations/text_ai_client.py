"""Unified text-generation call that tries whichever AI provider has a
configured key — Gemini first (existing default), then Claude as a fallback
— so callers (company overview extraction, AI summary) work as long as
*either* key is set, without provider-specific branching at each call site."""

from anthropic import Anthropic
from google import genai

from app.config import settings
from app.integrations.gemini_errors import friendly_gemini_error

GEMINI_MODEL = "gemini-3.6-flash"
CLAUDE_MODEL = "claude-sonnet-5"


class NoAIProviderConfigured(Exception):
    pass


def _try_gemini(prompt: str) -> str:
    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return (response.text or "").strip()


def _try_claude(prompt: str, max_tokens: int) -> str:
    client = Anthropic(api_key=settings.claude_api_key)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def generate_text(prompt: str, max_tokens: int = 4096) -> tuple[str, str]:
    """Returns (text, provider_used) — 'gemini' or 'claude'. Tries Gemini
    first when configured, falls back to Claude if Gemini isn't configured
    or its call fails. Raises NoAIProviderConfigured if neither key is set,
    or if both are set but both calls failed (message includes both errors).
    max_tokens only affects the Claude path — Gemini has no equivalent cap
    exposed here and just returns whatever it generates."""
    if not settings.gemini_api_key and not settings.claude_api_key:
        raise NoAIProviderConfigured("No Gemini or Claude API key configured — add one in Settings")

    errors = []

    if settings.gemini_api_key:
        try:
            text = _try_gemini(prompt)
            if text:
                return text, "gemini"
            errors.append("Gemini returned an empty response")
        except Exception as e:
            errors.append(friendly_gemini_error(e))

    if settings.claude_api_key:
        try:
            text = _try_claude(prompt, max_tokens)
            if text:
                return text, "claude"
            errors.append("Claude returned an empty response")
        except Exception as e:
            errors.append(f"Claude request failed: {str(e)[:300]}")

    raise NoAIProviderConfigured(" / ".join(errors))
