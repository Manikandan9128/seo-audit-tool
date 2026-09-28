// Report AI Provider selection (2026-09-28): the ONE provider every AI step
// of a report uses — Generate, Preview and Download alike. There is no
// provider order and no automatic default: nothing selected means nothing
// is sent, and the backend refuses to generate instead of choosing one.
// Stored in localStorage so a reload keeps the choice; every API request
// carries it as X-AI-Provider / X-Claude-Model (see api/client.ts), so
// every AI call on the backend is pinned to it.

export const PROVIDER_LABELS: Record<string, string> = {
  groq: "Groq",
  gemini: "Gemini",
  claude: "Claude",
  browser_use: "Browser Use",
  openrouter: "OpenRouter",
};

const PROVIDER_KEY = "ai_provider";
const CLAUDE_MODEL_KEY = "ai_claude_model";

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* storage unavailable — selection lasts for this page only */
  }
}

export function readSelectedProvider(): string {
  const stored = readStored(PROVIDER_KEY) ?? "";
  return stored in PROVIDER_LABELS ? stored : "";
}

export function writeSelectedProvider(provider: string) {
  writeStored(PROVIDER_KEY, provider);
}

export function readClaudeModel(fallback: string): string {
  return readStored(CLAUDE_MODEL_KEY) || fallback;
}

export function writeClaudeModel(model: string) {
  writeStored(CLAUDE_MODEL_KEY, model);
}

// Headers for one request — Claude model only when Claude is selected.
export function selectionHeaders(): Record<string, string> {
  const provider = readSelectedProvider();
  if (!provider) return {};
  const model = provider === "claude" ? readStored(CLAUDE_MODEL_KEY) : null;
  return { "X-AI-Provider": provider, ...(model ? { "X-Claude-Model": model } : {}) };
}
