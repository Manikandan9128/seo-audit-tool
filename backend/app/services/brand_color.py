import re
from collections import Counter

_CSS_VAR_CANDIDATES = [
    "--color-primary",
    "--primary-color",
    "--brand-color",
    "--color-brand",
    "--accent-color",
    "--color-accent",
    "--primary",
    "--brand",
    "--accent",
]

_HEX_RE = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")


def _normalize_hex(hex_value: str) -> str:
    hex_value = hex_value.lstrip("#")
    if len(hex_value) == 3:
        hex_value = "".join(ch * 2 for ch in hex_value)
    return hex_value.upper()


def _is_grayscale(hex6: str, tolerance: int = 30) -> bool:
    """True for plain neutrals (grays) AND near-black/near-white UI colors
    (borders, dividers, text) — both are ubiquitous in compiled site CSS and
    render as an invisible/washed-out accent if picked as the report's theme
    color. tolerance=30 catches near-grays like D0D5DD (a Tailwind-style
    border gray, RGB spread only 13) that the old tolerance=12 let through."""
    r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    if max(r, g, b) - min(r, g, b) <= tolerance:
        return True
    lightness = (max(r, g, b) + min(r, g, b)) / 2 / 255
    return lightness < 0.15 or lightness > 0.92


def extract_brand_color(html_source: str) -> str | None:
    """Best-effort brand accent color from a homepage's HTML: theme-color
    meta tag, then common CSS custom-property names, then the most frequent
    non-grayscale hex color found on the page. Returns a 6-char hex string
    (no '#') or None."""
    meta_match = re.search(
        r'<meta[^>]+name=["\']theme-color["\'][^>]+content=["\'](#[0-9a-fA-F]{3,6})["\']',
        html_source,
        re.IGNORECASE,
    )
    if meta_match:
        return _normalize_hex(meta_match.group(1))

    for var_name in _CSS_VAR_CANDIDATES:
        var_match = re.search(rf"{re.escape(var_name)}\s*:\s*(#[0-9a-fA-F]{{3,6}})", html_source, re.IGNORECASE)
        if var_match:
            return _normalize_hex(var_match.group(1))

    hexes = [_normalize_hex(m.group(0)) for m in _HEX_RE.finditer(html_source)]
    candidates = [h for h in hexes if not _is_grayscale(h)]
    if candidates:
        return Counter(candidates).most_common(1)[0][0]
    return None
