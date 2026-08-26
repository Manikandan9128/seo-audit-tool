"""Turns raw Gemini SDK exceptions (which dump the full JSON error body) into
a short, actionable message for the UI — the raw text is still logged by
whatever caught the exception, this just controls what the user sees."""


def friendly_gemini_error(e: Exception) -> str:
    text = str(e)
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        if "PerDay" in text or "free_tier" in text.lower():
            return (
                "Gemini free-tier daily quota exceeded for this Google Cloud project (resets at "
                "midnight Pacific time). A different API key on the same project won't help — "
                "either wait for the reset, generate a key under a different project, or enable "
                "billing on the project to remove the daily cap."
            )
        return "Gemini rate limit hit — too many requests in a short window. Wait a bit and try again."
    if "PERMISSION_DENIED" in text or "403" in text or "API_KEY_INVALID" in text or "401" in text:
        return "Gemini rejected this API key — check it was copied correctly and hasn't been revoked."
    if "UNAVAILABLE" in text or "503" in text:
        return "Gemini's servers are temporarily unavailable. Try again shortly."
    return f"Gemini request failed: {text[:300]}"
