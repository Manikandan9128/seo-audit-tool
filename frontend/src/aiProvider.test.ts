import { beforeEach, describe, expect, it, vi } from "vitest";

import { readSelectedProvider, selectionHeaders, writeClaudeModel, writeSelectedProvider } from "./aiProvider";

// Minimal localStorage stand-in — a "reload" is just reading it again with
// fresh module state, which is exactly what the page does on mount.
function installStorage() {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
}

describe("Report AI Provider selection", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    installStorage();
  });

  it("survives a reload unchanged", () => {
    writeSelectedProvider("claude");
    expect(readSelectedProvider()).toBe("claude");
  });

  it("is empty with nothing chosen — never defaults to a configured provider", () => {
    expect(readSelectedProvider()).toBe("");
    expect(selectionHeaders()).toEqual({});
  });

  it("ignores an unknown stored value instead of substituting another provider", () => {
    localStorage.setItem("ai_provider", "gpt-something");
    expect(readSelectedProvider()).toBe("");
  });

  it("sends the selected provider (and Claude model only for Claude) on every request", () => {
    writeSelectedProvider("claude");
    writeClaudeModel("claude-opus-5-5");
    expect(selectionHeaders()).toEqual({ "X-AI-Provider": "claude", "X-Claude-Model": "claude-opus-5-5" });

    writeSelectedProvider("gemini");
    expect(selectionHeaders()).toEqual({ "X-AI-Provider": "gemini" });
  });

  it("works without storage (private mode) instead of crashing", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
      removeItem: () => {
        throw new Error("blocked");
      },
    });
    expect(() => writeSelectedProvider("groq")).not.toThrow();
    expect(readSelectedProvider()).toBe("");
  });
});
