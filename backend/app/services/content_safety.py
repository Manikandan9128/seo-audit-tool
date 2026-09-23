"""Hard rule (user, 2026-09-23): 18+ / adult content must NEVER appear
anywhere in a client report — not on a slide, not in the linked Google
Sheet, not in the preview JSON, whatever its search volume, and whether
or not any AI call succeeded.

Deterministic word/pattern matching only — no AI, so it can never fail
open. Applied in three layers (see site_audit.py / pptx_builder.py):
  1. every keyword source as it is loaded (Semrush uploads, GSC queries),
     before any AI prompt or slide sees it;
  2. the whole report data again right before the Sheet and PPTX are built,
     catching anything an AI call wrote;
  3. the finished PPTX itself, as a last safety net.

Stored upload data is never modified (standing no-data-deletion rule):
layer 1 filters in-memory copies only.

Deliberately NOT blocked, to avoid wiping legitimate business keywords:
"sexual harassment" (HR/payroll clients), "escort vehicle" (trucking),
"xxl"/"xxxl" clothing sizes, "unisex", "Essex".
"""

import copy
import re

# Whole-word terms (after leetspeak normalisation).
_ADULT_WORDS = {
    "porn", "porno", "porns", "pornography", "pornographic", "xxx", "xnxx", "xvideos", "xvideo",
    "xhamster", "pornhub", "redtube", "youporn", "brazzers", "onlyfans", "hentai", "nsfw",
    "nude", "nudes", "nudity", "naked", "sex", "sexy", "milf", "camgirl", "camgirls", "chudai",
    "boobs", "tits", "pussy", "fuck", "fucking", "blowjob", "handjob", "cumshot", "erotic", "erotica",
    "hardcore", "bdsm", "fetish", "stripchat", "chaturbate", "livejasmin", "spankbang", "rule34",
    "eighteenplus", "bf",
}
# "bf" is Indian search slang for adult video ("school bus bf", seen in a
# real BharatBenz keyword sheet) — blocked unless it is the tyre brand.
_BF_SAFE_CONTEXT = {"goodrich", "tyre", "tyres", "tire", "tires"}
# "sex" alone is blocked unless the query is clearly about a non-adult
# topic (education/HR/biology/statistics).
_SEX_SAFE_CONTEXT = {
    "education", "educator", "ratio", "discrimination", "determination", "equality", "harassment",
    "offender", "offenders", "offence", "offense", "chromosome", "chromosomes", "hormone", "hormones",
    "differences", "difference", "workplace", "policy", "biological", "assigned", "trafficking",
}
# Multi-word phrases.
_ADULT_PHRASES = [
    "call girl", "call girls", "escort service", "escort services", "adult video", "adult videos",
    "adult movie", "adult movies", "adult site", "adult sites", "adult content", "blue film", "blue films",
    "bf video", "bf videos", "bus flash", "18 video", "18 videos", "18 movie", "desi mms", "mms video", "sex tape",
]
# Concatenated forms with no word boundary ("xxxvideo", "pornvideos").
_ADULT_SUBSTRINGS = ["porn", "xnxx", "xvideo", "xhamster", "hentai", "onlyfans", "brazzers", "sexvideo", "xxxvideo"]
# "xxx" inside a longer token is adult unless it's a clothing size (xxl, xxxl, 3xl...).
_SIZE_TOKEN = re.compile(r"^x{2,}l+$")

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "@": "a", "$": "s", "7": "t"})

# Row fields that hold a keyword / search query / page topic. A row whose
# value in any of these is adult is dropped whole.
_KEYWORD_FIELDS = (
    "keyword", "query", "top_query", "primary_keyword", "search_term", "term", "keywords_text",
)


def _normalise(text: str) -> str:
    t = (text or "").lower()
    # "18+" / "18 plus" markers before stripping punctuation.
    t = re.sub(r"(?<![0-9])18\s*(\+|plus)", " eighteenplus ", t)
    t = re.sub(r"[^a-z0-9@$]+", " ", t)
    # Leetspeak only inside tokens that mix letters and digits ("p0rn",
    # "s3x") — never pure numbers or ordinary words.
    tokens = []
    for tok in t.split():
        if re.search(r"[a-z]", tok) and re.search(r"[0-9@$]", tok):
            tok = tok.translate(_LEET)
        tokens.append(re.sub(r"[^a-z0-9]", "", tok))
    return " ".join(t for t in tokens if t)


# One cheap scan that every adult term below would also hit (including
# its leetspeak forms, folded by one C-level translate). Ordinary strings —
# the vast majority of report data — fail it and skip the detailed check.
_PREFILTER_FOLD = str.maketrans({"0": "o", "3": "e", "5": "s", "$": "s", "@": "a", "1": "i", "4": "a", "7": "t"})
_PREFILTER_NEEDLES = (
    "porn", "pron", "sex", "xxx", "xnxx", "xvid", "xham", "hentai", "onlyfan", "brazzer", "nsfw", "nud", "naked",
    "milf", "camgirl", "chudai", "boob", "tits", "pussy", "fuck", "blowjob", "handjob", "cumshot", "erotic",
    "hardcore", "bdsm", "fetish", "stripchat", "chaturbate", "jasmin", "spankbang", "ruie34", "rule34", "redtube",
    "i8+", "i8 +", "i8 plus", "i8plus", "i8 video", "i8 movie", "call girl", "escort", "adult", "blue film",
    "bf", "flash", "mms",
)


def _prefilter(text: str) -> bool:
    folded = text.lower().translate(_PREFILTER_FOLD)
    return any(n in folded for n in _PREFILTER_NEEDLES)


def is_adult(text) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    if not _prefilter(text):
        return False
    norm = _normalise(text)
    if not norm:
        return False
    tokens = norm.split()
    token_set = set(tokens)
    if "sex" in token_set and token_set & _SEX_SAFE_CONTEXT and not (token_set & (_ADULT_WORDS - {"sex"})):
        token_set = token_set - {"sex"}
        tokens = [t for t in tokens if t != "sex"]
    if "bf" in token_set and token_set & _BF_SAFE_CONTEXT:
        tokens = [t for t in tokens if t != "bf"]
    if any(t in _ADULT_WORDS for t in tokens):
        return True
    padded = f" {norm} "
    if any(f" {p} " in padded for p in _ADULT_PHRASES):
        return True
    for t in tokens:
        if _SIZE_TOKEN.match(t):
            continue
        if "xxx" in t or any(s in t for s in _ADULT_SUBSTRINGS):
            return True
    return False


def _row_is_adult(row: dict) -> bool:
    return any(is_adult(row.get(f)) for f in _KEYWORD_FIELDS if isinstance(row.get(f), str))


_KEYWORD_FIELD_SET = frozenset(_KEYWORD_FIELDS)


def _scrub_row(row: dict) -> dict | None:
    """One pass over a list-item dict: None (drop the whole row) when a
    keyword/query field is adult, else a scrubbed copy. Each string is
    checked once."""
    out = {}
    for k, v in row.items():
        if isinstance(k, str) and is_adult(k):
            continue
        if isinstance(v, str):
            if is_adult(v):
                if k in _KEYWORD_FIELD_SET:
                    return None
                v = _clean_string(v)
            out[k] = v
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = scrub(v)
    return out


def _clean_string(text: str) -> str:
    """Drops every sentence containing adult content from free text (AI
    narratives, insight lines); an all-adult string becomes empty."""
    if not is_adult(text):
        return text
    parts = re.split(r"(?<=[.!?])\s+|\n", text)
    kept = [p for p in parts if not is_adult(p)]
    return " ".join(kept).strip()


def scrub(obj):
    """Returns a copy of `obj` (dict/list/str/scalars, any nesting) with
    adult content removed: list items that are adult rows/strings are
    dropped, dict keys that are adult (e.g. keyword-keyed maps) are
    dropped, other strings lose their adult sentences. Never mutates the
    input."""
    if isinstance(obj, str):
        return _clean_string(obj)
    if isinstance(obj, list):
        out = []
        for item in obj:
            if isinstance(item, dict):
                row = _scrub_row(item)
                if row is not None:
                    out.append(row)
            elif isinstance(item, str):
                if not is_adult(item):
                    out.append(item)
            else:
                out.append(scrub(item))
        return out
    if isinstance(obj, tuple):
        return tuple(scrub(list(obj)))
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and is_adult(k):
                continue
            out[k] = scrub(v)
        return out
    return obj


class SafeImport:
    """Read-only view of a SemrushImport with adult rows removed from its
    parsed_data. Delegates every other attribute to the real ORM object,
    whose stored data is never touched."""

    def __init__(self, imp):
        self._imp = imp
        pd = getattr(imp, "parsed_data", None)
        if isinstance(pd, dict):
            safe = copy.copy(pd)
            if isinstance(pd.get("rows"), list):
                safe["rows"] = scrub(pd["rows"])
            self.parsed_data = safe
        else:
            self.parsed_data = pd

    def __getattr__(self, name):
        return getattr(self._imp, name)


def safe_imports(imports: list) -> list:
    return [SafeImport(i) for i in imports or []]


def redact_presentation(prs) -> int:
    """Layer 3 — last safety net over the finished deck: removes any table
    row (never the header) and clears any text run containing adult
    content. Returns how many items were removed."""
    removed = 0
    for slide in prs.slides:
        for shape in slide.shapes:
            if getattr(shape, "has_table", False) and shape.has_table:
                tbl = shape.table
                for row in list(tbl.rows)[1:]:
                    if any(is_adult(cell.text_frame.text) for cell in row.cells):
                        tbl._tbl.remove(row._tr)
                        removed += 1
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    if is_adult("".join(r.text for r in para.runs)):
                        for run in para.runs:
                            run.text = _clean_string(run.text) if not is_adult(run.text) else ""
                        removed += 1
    return removed
