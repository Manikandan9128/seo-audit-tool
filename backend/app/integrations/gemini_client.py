from app.integrations.text_ai_client import generate_text, selected_provider_ready


def summarize_company(website_url: str, page_text: str) -> str | None:
    """Summarizes what a company does from its homepage text, using the
    selected Report AI Provider only. Returns None if no provider is
    selected/configured or the call fails."""
    if not selected_provider_ready() or not page_text.strip():
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
