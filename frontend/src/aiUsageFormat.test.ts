import { describe, expect, it } from "vitest";

import { formatCost, formatTokens } from "./aiUsageFormat";

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
