"""Global AI token & failure control (2026-09-29 spec) — shared by every AI
call in the tool, whatever the provider or model.

One place for:
- configuration: per-module output budgets, warning and hard safety
  limits, prices (AI_CONFIG / PRICES);
- request states (AIStatus) and the exact client-facing wording for each;
- the failure classifier (classify_error): token limit vs timeout vs
  invalid JSON vs provider error are never conflated;
- the per-report usage ledger (UsageLedger): tokens, cost, provider,
  model, module and status for every call, readable while a report builds;
- JSON validation + repair (parse_json).

text_ai_client routes every provider call through this; audit modules never
duplicate any of it. Nothing here is provider-specific: adding a provider
(e.g. OpenAI) means adding its price row and reporting its usage numbers.
"""

import json
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Configuration — change limits and budgets here only.
# ---------------------------------------------------------------------------
AI_CONFIG = {
    # Per heavy step (one module's AI work) and per whole report, in tokens.
    # These are WARNING thresholds only (shown in the High Token Usage
    # popup so the user can see cost before proceeding) — 2026-09-29 user
    # decision: correctness matters more than cost or time, so nothing here
    # blocks a step or a report from running once the user has seen the
    # popup and clicked Proceed. See hard_limit_violation() and
    # check_report_hard_limit(), both now no-ops kept for their return
    # shape/tests. step_hard_limit_tokens/report_hard_limit_tokens still
    # drive the popup's "over the limit" styling, just not enforcement.
    "step_warning_tokens": 20_000,
    "step_hard_limit_tokens": 50_000,
    "report_warning_tokens": 150_000,
    "report_hard_limit_tokens": 400_000,
    # Retries after a failed request (the first attempt is not a retry).
    # Raised from 2 (2026-09-29 user decision, same reasoning as above):
    # a section missing from the deck is worse than a slower/costlier
    # report, so the SAME pinned provider (never a fallback substitute)
    # keeps getting retried, each retry shaped by the real failure reason
    # (shorter answer / stricter JSON / etc, see RETRY_SUFFIX), well past
    # what a quick transient blip would need. NOT raised all the way to
    # "unlimited": some provider paths (Groq/Gemini rate-limit handling)
    # sleep for real seconds between attempts, so the retry count is also a
    # real wall-clock multiplier, not just a request-count cap — confirmed
    # real, raising this to 20 made the test suite itself balloon from ~35s
    # to several minutes. 8 is a large jump from 2 (a genuinely stuck call
    # gets far more chances) without that pathological blow-up.
    "max_retries": 8,
    # A request that times out is retried at most this many times — kept
    # equal to max_retries so a run of timeouts isn't cut short earlier
    # than any other failure type would be.
    "max_timeout_retries": 8,
}

# Output-token budget per module for providers whose models reason before
# answering (thinking shares the output budget — see PROVIDER_REASONS). For
# other providers the caller's own, smaller max_tokens is kept, capped here.
MODULE_OUTPUT_BUDGETS = {
    "aeo_geo": 16_000,
    "ui_audit": 16_000,
    "core_problem": 16_000,
    "competitor_narratives": 16_000,
    "next_steps": 16_000,
    "branded": 16_000,
    "seo_issues": 12_000,
    "schema": 12_000,
    "company_overview": 12_000,
    "keywords": 12_000,
    "other": 12_000,
}

# Providers whose models spend output tokens on reasoning before the answer.
PROVIDER_REASONS = {"claude"}

# $ per million (input, output) tokens. Key: (provider, model) or
# (provider, None) for a provider-wide price. Missing = unknown price.
PRICES: dict[tuple[str, str | None], tuple[float, float]] = {
    ("claude", "claude-opus-5-5"): (4.0, 20.0),
    ("claude", "claude-sonnet-5"): (2.0, 10.0),
    ("claude", "claude-haiku-4-5-20251001"): (1.0, 5.0),
    ("openrouter", None): (0.0, 0.0),  # openrouter/free router
    ("groq", None): (0.0, 0.0),  # free tier
    ("gemini", None): (0.0, 0.0),  # free tier
    # ("openai", "<model>"): (in, out),  — add when OpenAI is wired in
}

PROVIDER_LABELS = {
    "groq": "Groq", "gemini": "Gemini", "claude": "Claude", "browser_use": "Browser Use",
    "openrouter": "OpenRouter", "openai": "OpenAI",
}


def price_for(provider: str, model: str | None) -> tuple[float, float] | None:
    return PRICES.get((provider, model)) or PRICES.get((provider, None))


def cost_usd(provider: str, model: str | None, input_tokens: int, output_tokens: int) -> float | None:
    price = price_for(provider, model)
    if price is None:
        return None
    return round(input_tokens / 1e6 * price[0] + output_tokens / 1e6 * price[1], 4)


def output_budget(module: str | None, provider: str, requested: int) -> int:
    budget = MODULE_OUTPUT_BUDGETS.get(module or "other", MODULE_OUTPUT_BUDGETS["other"])
    if provider in PROVIDER_REASONS:
        return max(requested, budget)
    return min(requested, budget)


# ---------------------------------------------------------------------------
# Request states and client-facing wording.
# ---------------------------------------------------------------------------
class AIStatus:
    QUEUED = "QUEUED"
    ESTIMATING = "ESTIMATING"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    TOKEN_LIMIT_EXCEEDED = "TOKEN_LIMIT_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    INVALID_JSON = "INVALID_JSON"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


FAILURE_WORDING = {
    AIStatus.TOKEN_LIMIT_EXCEEDED: (
        "Token limit exceeded",
        "The AI response reached the configured token limit before completing the analysis.",
    ),
    AIStatus.TIMEOUT: ("Request timed out", "The AI request exceeded the allowed processing time."),
    AIStatus.INVALID_JSON: (
        "AI returned incomplete JSON",
        "The AI completed the request, but the response could not be parsed as valid JSON.",
    ),
    AIStatus.PROVIDER_ERROR: ("Provider API error", "The AI provider returned an error."),
    AIStatus.CANCELLED: ("Cancelled by user", "Skipped at your request to save AI usage (not shown in the deck)."),
    AIStatus.FAILED: ("Failed", "The analysis could not be generated."),
}


class TokenLimitError(RuntimeError):
    """A provider stopped because it hit the output-token limit."""


class SafetyLimitError(RuntimeError):
    """The report reached its configured maximum token safety limit."""


_TOKEN_LIMIT_RE = re.compile(
    r"stop_reason=max_tokens|finish_reason=(?:length|max_tokens)|max_tokens_exceeded|token_limit|"
    r"output_truncated|context_length_exceeded|prompt too large|request too large|tokens? limit|"
    r"\btruncated content\b|maximum context length",
    re.IGNORECASE,
)
_TIMEOUT_RE = re.compile(r"timed out|timeout|did not finish within", re.IGNORECASE)
_INVALID_JSON_RE = re.compile(r"not return valid json|invalid json|not valid json|missing an? '?\w+'? array|could not be parsed",
                              re.IGNORECASE)
_PROVIDER_RE = re.compile(r"\b(4\d\d|5\d\d)\b|rate[- ]limit|quota|resource_exhausted|unavailable|overloaded|"
                          r"api key|authentication|permission|request failed", re.IGNORECASE)
_TIMEOUT_SECONDS_RE = re.compile(r"after (\d+)s|within (\d+)s")
_STATUS_TAG_RE = re.compile(r"\[(TOKEN_LIMIT_EXCEEDED|TIMEOUT|INVALID_JSON|PROVIDER_ERROR|FAILED|CANCELLED)\]")


def classify_error(text: str) -> str:
    """The real failure reason behind one provider error message. Order
    matters: a truncated answer that also failed to parse is a token-limit
    problem, not a JSON problem; neither is ever reported as a timeout."""
    tagged = _STATUS_TAG_RE.search(text or "")
    if tagged:
        return tagged.group(1)
    if _TOKEN_LIMIT_RE.search(text or ""):
        return AIStatus.TOKEN_LIMIT_EXCEEDED
    if _TIMEOUT_RE.search(text or ""):
        return AIStatus.TIMEOUT
    if _INVALID_JSON_RE.search(text or ""):
        return AIStatus.INVALID_JSON
    if _PROVIDER_RE.search(text or ""):
        return AIStatus.PROVIDER_ERROR
    return AIStatus.FAILED


def is_daily_limit(text: str) -> bool:
    """A provider cap that a retry within this report can't clear."""
    t = (text or "").lower()
    return "perday" in t or "daily" in t or "free-tier request cap" in t or "not retrying" in t or "budget looks spent" in t


def tag_error(text: str) -> str:
    """'[STATUS] message' — the status travels with the message through
    every caller's own error list, so the final wording can be exact."""
    if _STATUS_TAG_RE.match(text or ""):
        return text
    return f"[{classify_error(text)}] {text}"


def describe_failure(section: str, errors: list[str], provider: str | None) -> str:
    """'<Section> — <headline>. <explanation> (<Provider>: <detail>). No
    fallback provider was used because <Provider> was selected.' The status
    is the LAST real attempt's — the reason the step finally gave up."""
    real = [e for e in errors if e and not e.startswith("No fallback provider")]
    status = classify_error(real[-1]) if real else AIStatus.FAILED
    headline, explanation = FAILURE_WORDING.get(status, FAILURE_WORDING[AIStatus.FAILED])
    detail = _STATUS_TAG_RE.sub("", real[-1]).strip() if real else ""
    if status == AIStatus.TIMEOUT:
        m = _TIMEOUT_SECONDS_RE.search(detail)
        if m:
            headline = f"Request timed out after {m.group(1) or m.group(2)} seconds"
    if status == AIStatus.INVALID_JSON and any(classify_error(e) == AIStatus.TOKEN_LIMIT_EXCEEDED for e in real):
        explanation = "The AI response was truncated before valid JSON could be completed."
    label = PROVIDER_LABELS.get(provider or "", provider or "")
    detail_text = f" ({detail[:160]})" if detail else ""
    note = f" No fallback provider was used because {label} was selected." if label else ""
    return f"{section} — {headline}. {explanation}{detail_text}{note}"


def readable_issue(issue: str, provider: str | None) -> str:
    """Rewrites a content issue built from a raw error list ('Core Problem
    slide: [TOKEN_LIMIT_EXCEEDED] Claude …| …') into the exact client-facing
    form. Issues with no AI failure in them pass through unchanged."""
    if " — " in issue.split(":", 1)[0]:
        return issue  # already in final form
    if not (_STATUS_TAG_RE.search(issue) or _INVALID_JSON_RE.search(issue)):
        return issue
    section, _, rest = issue.partition(":")
    parts = [p.strip() for p in re.split(r"\s\|\s|\s/\s", rest) if p.strip()]
    return describe_failure(section.strip(), parts, provider)


# ---------------------------------------------------------------------------
# JSON validation + repair.
# ---------------------------------------------------------------------------
def _strip_fences(raw: str) -> str:
    return re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()


def _close_truncated(text: str) -> str | None:
    """Closes an answer cut off mid-JSON at its last complete value: drops
    the trailing partial element, then closes open strings/brackets."""
    stack, in_string, escape, last_safe = [], False, False, -1
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
            last_safe = i
        elif ch == "," and stack and stack[-1] == "]":
            # Only a comma between ARRAY elements is a safe cut: cutting
            # inside an object would keep a half-written item (e.g. an
            # issue with a title but no priority).
            last_safe = i - 1
    if not stack or last_safe < 0:
        return None
    head = text[: last_safe + 1].rstrip().rstrip(",")
    # Re-count what's still open after the cut.
    stack, in_string, escape = [], False, False
    for ch in head:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if in_string:
        return None
    return head + "".join(reversed(stack))


def parse_json(raw: str):
    """(data, repaired) for an AI answer that should be JSON, or
    (None, False) when it can't be recovered. Tries, in order: the answer
    as-is, the outermost {...}/[...] inside it, then closing a truncated
    answer at its last complete value."""
    text = _strip_fences(raw)
    try:
        return json.loads(text), False
    except (json.JSONDecodeError, ValueError):
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if starts:
        start = min(starts)
        end = max(text.rfind("}"), text.rfind("]"))
        if end > start:
            try:
                return json.loads(text[start: end + 1]), False
            except (json.JSONDecodeError, ValueError):
                pass
        closed = _close_truncated(text[start:])
        if closed:
            try:
                return json.loads(closed), True
            except (json.JSONDecodeError, ValueError):
                pass
    return None, False


# Appended to a prompt on a retry, by failure type (spec §10).
RETRY_SUFFIX = {
    AIStatus.TOKEN_LIMIT_EXCEEDED: (
        "\n\nIMPORTANT: your previous answer was cut off at the length limit. Answer again more concisely: "
        "return only the required JSON fields, keep every string short, and include fewer items if needed. "
        "Output only the JSON."
    ),
    AIStatus.INVALID_JSON: (
        "\n\nIMPORTANT: your previous answer was not valid JSON. Return ONLY one valid JSON value matching "
        "the requested structure — no markdown fences, no commentary, no trailing text."
    ),
}


# ---------------------------------------------------------------------------
# Per-report usage ledger.
# ---------------------------------------------------------------------------
@dataclass
class UsageLedger:
    provider: str | None = None
    model: str | None = None
    calls: list[dict] = field(default_factory=list)
    module_status: dict[str, str] = field(default_factory=dict)
    # Requests we gave up waiting for. The provider may still have finished
    # (and billed) them, so their tokens are NOT in `calls` - counted here
    # instead so the report can say how many calls may have been paid twice.
    timeouts: dict[str, int] = field(default_factory=dict)
    status: str = AIStatus.RUNNING
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_call(self, module: str, provider: str, model: str | None, input_tokens: int, output_tokens: int,
                    status: str = AIStatus.COMPLETED, estimated: bool = False, attempt: int = 1,
                    stop_reason: str | None = None, duration_s: float | None = None,
                    cache_read_tokens: int = 0, cache_creation_tokens: int = 0) -> None:
        with self._lock:
            self.calls.append({
                "module": module, "provider": provider, "model": model,
                "input_tokens": int(input_tokens or 0), "output_tokens": int(output_tokens or 0),
                "cost_usd": cost_usd(provider, model, input_tokens or 0, output_tokens or 0),
                "status": status, "estimated": estimated,
                "attempt": int(attempt or 1), "stop_reason": stop_reason,
                "duration_s": round(duration_s, 1) if duration_s is not None else None,
                "cache_read_tokens": int(cache_read_tokens or 0),
                "cache_creation_tokens": int(cache_creation_tokens or 0),
            })

    def note_timeout(self, module: str) -> None:
        with self._lock:
            self.timeouts[module] = self.timeouts.get(module, 0) + 1

    def mark_calls_since(self, start: int, status: str) -> None:
        """Marks the calls recorded since index `start` (one attempt's calls)
        as not-completed - the answer was received and paid for but the step
        rejected it. A call already marked (truncated, cached) keeps its mark."""
        with self._lock:
            for c in self.calls[start:]:
                if c["status"] == AIStatus.COMPLETED and not c.get("cached"):
                    c["status"] = status

    def record_cached_call(self, module: str, provider: str, model: str | None,
                           saved_input: int, saved_output: int) -> None:
        """An answer served from the cache: no tokens billed, the original
        call's size kept so the report can show what was saved."""
        with self._lock:
            self.calls.append({
                "module": module, "provider": provider, "model": model,
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                "status": AIStatus.COMPLETED, "estimated": False, "cached": True,
                "saved_input_tokens": int(saved_input or 0), "saved_output_tokens": int(saved_output or 0),
                "saved_cost_usd": cost_usd(provider, model, saved_input or 0, saved_output or 0),
            })

    def mark_last_call(self, status: str) -> None:
        with self._lock:
            if self.calls:
                self.calls[-1]["status"] = status

    def set_module_status(self, module: str, status: str) -> None:
        with self._lock:
            # A module that completed once stays completed even if a later,
            # separate call in the same module fails; a failure never
            # overwrites COMPLETED, CANCELLED always wins.
            current = self.module_status.get(module)
            if status == AIStatus.CANCELLED or current != AIStatus.COMPLETED:
                self.module_status[module] = status

    @property
    def total_tokens(self) -> int:
        with self._lock:
            return sum(c["input_tokens"] + c["output_tokens"] for c in self.calls)

    def summary(self) -> dict:
        with self._lock:
            calls = list(self.calls)
            modules = dict(self.module_status)
        known_costs = [c["cost_usd"] for c in calls if c["cost_usd"] is not None]
        by_module: dict[str, dict] = {}
        wasted = [c for c in calls if c["status"] != AIStatus.COMPLETED and not c.get("cached")]
        for c in calls:
            m = by_module.setdefault(c["module"], {"input_tokens": 0, "output_tokens": 0, "calls": 0})
            m["input_tokens"] += c["input_tokens"]
            m["output_tokens"] += c["output_tokens"]
            m["calls"] += 1
            m["max_attempt"] = max(m.get("max_attempt", 1), c.get("attempt", 1))
        for c in wasted:
            m = by_module[c["module"]]
            m["wasted_tokens"] = m.get("wasted_tokens", 0) + c["input_tokens"] + c["output_tokens"]
            m["wasted_calls"] = m.get("wasted_calls", 0) + 1
            m.setdefault("wasted_reasons", {})
            m["wasted_reasons"][c["status"]] = m["wasted_reasons"].get(c["status"], 0) + 1
        for name, count in self.timeouts.items():
            by_module.setdefault(name, {"input_tokens": 0, "output_tokens": 0, "calls": 0})["timeouts"] = count
        wasted_costs = [c["cost_usd"] for c in wasted if c["cost_usd"] is not None]
        for name, st in modules.items():
            by_module.setdefault(name, {"input_tokens": 0, "output_tokens": 0, "calls": 0})["status"] = st
        return {
            "provider": self.provider,
            "provider_label": PROVIDER_LABELS.get(self.provider or "", self.provider),
            "model": self.model,
            "status": self.status,
            "calls": len(calls),
            "input_tokens": sum(c["input_tokens"] for c in calls),
            "output_tokens": sum(c["output_tokens"] for c in calls),
            "total_tokens": sum(c["input_tokens"] + c["output_tokens"] for c in calls),
            "cost_usd": round(sum(known_costs), 4) if len(known_costs) == len(calls) else None,
            "estimated": any(c["estimated"] for c in calls),
            "wasted_calls": len(wasted),
            "wasted_tokens": sum(c["input_tokens"] + c["output_tokens"] for c in wasted),
            "wasted_cost_usd": round(sum(wasted_costs), 4) if len(wasted_costs) == len(wasted) else None,
            "timeouts": sum(self.timeouts.values()),
            "cached_calls": sum(1 for c in calls if c.get("cached")),
            "saved_tokens": sum(c.get("saved_input_tokens", 0) + c.get("saved_output_tokens", 0) for c in calls),
            "saved_cost_usd": round(sum(c.get("saved_cost_usd") or 0 for c in calls), 4),
            "modules": by_module,
            "warning_tokens": AI_CONFIG["report_warning_tokens"],
            "hard_limit_tokens": AI_CONFIG["report_hard_limit_tokens"],
        }


_context = threading.local()


def current_ledger() -> UsageLedger | None:
    return getattr(_context, "ledger", None)


def set_ledger(ledger: UsageLedger | None) -> None:
    _context.ledger = ledger


def current_module() -> str:
    return getattr(_context, "module", None) or "other"


def set_module(module: str | None) -> None:
    _context.module = module


@contextmanager
def ai_module(module: str):
    """Attributes every AI call inside the block to one audit module (for
    usage tracking, budgets and status)."""
    previous = getattr(_context, "module", None)
    _context.module = module
    try:
        yield
    finally:
        _context.module = previous


def set_attempt(n: int) -> None:
    """Which try (1 = first) of the current request is about to run - stamped
    on the ledger entry so a retried step shows up as attempt 2, 3..."""
    _context.attempt = n


def current_attempt() -> int:
    return getattr(_context, "attempt", 1)


def record_usage(provider: str, model: str | None, input_tokens: int, output_tokens: int,
                 estimated: bool = False, **detail) -> None:
    ledger = current_ledger()
    if ledger is not None:
        ledger.record_call(current_module(), provider, model, input_tokens, output_tokens, estimated=estimated,
                           attempt=current_attempt(), **detail)


def note_timeout() -> None:
    ledger = current_ledger()
    if ledger is not None:
        ledger.note_timeout(current_module())


def log_report_usage(logger, job_id, client_id, summary: dict) -> None:
    """One INFO line per step and one WARNING when tokens were paid for and
    thrown away, so a bad report can be traced from the server log alone."""
    for name, m in sorted(summary.get("modules", {}).items(), key=lambda kv: -(kv[1]["input_tokens"] + kv[1]["output_tokens"])):
        logger.info(
            "AI usage job %s (client %s) step %s: %d call(s), max attempt %d, %d in + %d out, wasted %d tokens %s, timeouts %d",
            job_id, client_id, name, m.get("calls", 0), m.get("max_attempt", 1), m.get("input_tokens", 0),
            m.get("output_tokens", 0), m.get("wasted_tokens", 0), m.get("wasted_reasons", {}), m.get("timeouts", 0),
        )
    if summary.get("wasted_tokens") or summary.get("timeouts"):
        logger.warning(
            "AI waste job %s (client %s): %d token(s) in %d call(s) paid for and discarded (cost %s), %d request(s) timed out",
            job_id, client_id, summary.get("wasted_tokens", 0), summary.get("wasted_calls", 0),
            summary.get("wasted_cost_usd"), summary.get("timeouts", 0),
        )
    if summary.get("cached_calls"):
        logger.info(
            "AI cache job %s (client %s): %d call(s) answered from saved answers, ~%d token(s) / $%s saved",
            job_id, client_id, summary["cached_calls"], summary.get("saved_tokens", 0), summary.get("saved_cost_usd"),
        )


def check_report_hard_limit() -> None:
    """2026-09-29 user decision: the High Token Usage popup (Preview/
    Download) is the ONE confirmation gate — once the user has clicked
    Proceed there, nothing mid-report aborts a run over token usage, no
    matter how far over report_hard_limit_tokens it runs. A no-op kept so
    _layered_attempts' call site and SafetyLimitError's tests/shape don't
    need touching; the ledger's own total_tokens keeps counting either way
    for the live usage display."""
    return


def context_snapshot() -> tuple:
    return getattr(_context, "ledger", None), getattr(_context, "module", None)


def restore_context(snapshot: tuple) -> None:
    _context.ledger, _context.module = snapshot
