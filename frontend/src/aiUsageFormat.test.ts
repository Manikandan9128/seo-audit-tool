import { describe, expect, it } from "vitest";

import { compactTokens, formatCost, formatTokens, usageSummaryText, usageTooltip } from "./aiUsageFormat";

describe("AI usage formatting", () => {
  it("shows exact token counts", () => {
    expect(formatTokens(12450)).toBe((12450).toLocaleString());
  });

  it("labels free, per-run and tiny costs honestly", () => {
    expect(formatCost(0, false)).toBe("free tier");
    expect(formatCost(null, true)).toBe("billed per run");
    expect(formatCost(0.004, false)).toBe("<$0.01");
    expect(formatCost(0.456, false)).toBe("$0.46");
  });
});

describe("short AI usage line", () => {
  it("compacts big token counts", () => {
    expect(compactTokens(201370)).toBe("201k");
    expect(compactTokens(1234567)).toBe("1.2M");
    expect(compactTokens(4520)).toBe("4.5k");
    expect(compactTokens(812)).toBe("812");
  });

  it("is one short line for a finished report", () => {
    const u = { provider_label: "Claude", model: "claude-sonnet-5", status: "COMPLETED", total_tokens: 201370, cost_usd: 1.11, calls: 25 };
    expect(usageSummaryText(u)).toBe("Claude · 201k tokens · $1.11");
  });

  it("says running/failed, approx. and cache savings only when they apply", () => {
    const base = { provider_label: "Claude", status: "RUNNING", total_tokens: 90000, cost_usd: 0.5, estimated: true };
    expect(usageSummaryText(base)).toBe("Claude · 90k tokens · $0.50 approx. · running");
    expect(usageSummaryText({ ...base, status: "COMPLETED", estimated: false, saved_cost_usd: 0.4 })).toBe(
      "Claude · 90k tokens · $0.50 · saved $0.40",
    );
  });

  it("keeps the model, exact tokens and waste in the hover text", () => {
    const text = usageTooltip({
      provider_label: "Claude", model: "claude-sonnet-5", status: "COMPLETED", total_tokens: 201370, cost_usd: 1.11,
      calls: 25, wasted_tokens: 8000, wasted_cost_usd: 0.05,
    });
    expect(text).toContain("claude-sonnet-5");
    expect(text).toContain((201370).toLocaleString());
    expect(text).toContain("discarded");
  });
});
