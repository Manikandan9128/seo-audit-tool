"""Flags a generic-TLD / single-target-country mismatch (e.g. a .com site
whose target market is one specific country) and produces the client-facing
Domain Strategy finding: the local-SEO tradeoff of a ccTLD move, plus an open
question about expansion timeline — a crawl has no way to know the client's
growth plans, so this is deliberately framed as a question, not a verdict."""

COUNTRY_TLD = {
    "india": "in", "united states": "us", "usa": "us", "united kingdom": "uk",
    "australia": "au", "canada": "ca", "singapore": "sg", "germany": "de",
    "france": "fr", "new zealand": "nz", "united arab emirates": "ae", "uae": "ae",
    "japan": "jp", "south africa": "za", "ireland": "ie", "spain": "es",
    "italy": "it", "netherlands": "nl", "brazil": "br", "mexico": "mx",
    "malaysia": "my", "philippines": "ph", "indonesia": "id", "saudi arabia": "sa",
}
GENERIC_TLDS = {"com", "net", "org", "io", "co", "biz", "info"}
NON_TARGETED_LABELS = {"global", "worldwide", "international", ""}


def _extract_tld(website_url: str) -> str:
    host = website_url.replace("https://", "").replace("http://", "").rstrip("/").split("/")[0]
    return host.rsplit(".", 1)[-1].lower()


def check_domain_strategy(website_url: str, target_country: str | None) -> dict | None:
    """Returns None when the rule doesn't apply (no single target country, or
    the domain is already on the matching ccTLD / a non-generic TLD).
    Otherwise returns the finding content for the Domain Strategy slide."""
    if not website_url or not target_country:
        return None
    country_norm = target_country.strip().lower()
    if country_norm in NON_TARGETED_LABELS:
        return None

    matched_country = next(
        (name for name in COUNTRY_TLD if name in country_norm or country_norm in name),
        None,
    )
    if not matched_country:
        return None

    expected_tld = COUNTRY_TLD[matched_country]
    actual_tld = _extract_tld(website_url)
    if actual_tld == expected_tld or actual_tld not in GENERIC_TLDS:
        return None

    domain = website_url.replace("https://", "").replace("http://", "").rstrip("/")
    cc_domain = domain.rsplit(".", 1)[0] + "." + expected_tld

    return {
        "current_domain": domain,
        "suggested_cc_domain": cc_domain,
        "target_country": target_country,
        "finding": (
            f"{domain} is on a generic .{actual_tld} domain while the target market is {target_country}. "
            f"A country-code domain like {cc_domain} sends a direct local-SEO signal to Google and can "
            f"outrank a generic-TLD competitor on country-specific searches — but a domain migration resets "
            f"accumulated authority and link equity built on {domain} unless it is handled as a full "
            f"301-redirected move, and it locks the brand more tightly into one country."
        ),
        "open_question": (
            f"Is {domain} planning to expand beyond {target_country} in the next 12-18 months? If yes, "
            f"staying on the generic .{actual_tld} domain and doubling down on country-filtered content and "
            f"hreflang is the safer path. If {target_country} is the sole focus for the foreseeable future, "
            f"moving to {cc_domain} — with a full 301 migration — is worth the short-term authority dip for "
            f"the long-term local-SEO advantage."
        ),
    }
