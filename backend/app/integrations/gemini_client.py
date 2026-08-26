from app.config import settings
from app.integrations.text_ai_client import generate_text


def summarize_company(website_url: str, page_text: str) -> str | None:
    """Summarizes what a company does from its homepage text. Tries Gemini
    first, falls back to Claude. Returns None if no API key is configured or
    both providers fail."""
    if (not settings.gemini_api_key and not settings.claude_api_key) or not page_text.strip():
        return None

    prompt = (
        f"Here is the visible text scraped from the homepage of {website_url}. "
        "In 3-4 sentences, summarize what this company does, who it serves, "
        "and what it's known for. Write plainly, no markdown, no preamble.\n\n"
        f"{page_text[:8000]}"
    )
    try:
        text, _provider = generate_text(prompt)
        return text
    except Exception:
        return None
