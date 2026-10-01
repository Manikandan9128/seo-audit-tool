// Token / cost display helpers shared by the usage popup and the live
// token counter.

export function formatTokens(n: number) {
  return n.toLocaleString();
}

export function formatCost(cost: number | null | undefined, perRun: boolean) {
  if (cost === null || cost === undefined) return perRun ? "billed per run" : "unknown";
  if (cost === 0) return "free tier";
  return cost < 0.01 ? "<$0.01" : `$${cost.toFixed(2)}`;
}

// 201,370 -> "201k", 1,234,567 -> "1.2M"; small counts stay exact.
export function compactTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(n);
}

type UsageForLine = {
  provider_label?: string | null;
  model?: string | null;
  status: string;
  total_tokens: number;
  cost_usd: number | null;
  estimated?: boolean;
  calls?: number;
  cached_calls?: number;
  saved_tokens?: number;
  saved_cost_usd?: number;
  wasted_tokens?: number;
  wasted_cost_usd?: number | null;
};

// "Claude · 201k tokens · $1.11" - the one short line shown next to the buttons.
// Status is only spelled out when it is not the normal finished state.
export function usageSummaryText(u: UsageForLine) {
  const parts = [
    u.provider_label || "AI",
    `${compactTokens(u.total_tokens)} tokens`,
    `${formatCost(u.cost_usd, u.cost_usd === null)}${u.estimated ? " approx." : ""}`,
  ];
  if ((u.saved_cost_usd ?? 0) >= 0.01) parts.push(`saved ${formatCost(u.saved_cost_usd, false)}`);
  if (u.status === "RUNNING") parts.push("running");
  else if (u.status !== "COMPLETED") parts.push(u.status.toLowerCase());
  return parts.join(" · ");
}

// Everything the short line leaves out, shown on hover.
export function usageTooltip(u: UsageForLine) {
  const lines = [
    `${u.provider_label || "AI"}${u.model ? ` (${u.model})` : ""}`,
    `Tokens: ${formatTokens(u.total_tokens)} across ${u.calls ?? 0} call${u.calls === 1 ? "" : "s"}`,
    `Estimated cost: ${formatCost(u.cost_usd, u.cost_usd === null)}${u.estimated ? " (approx.)" : ""}`,
    `Status: ${u.status === "COMPLETED" ? "Completed" : u.status === "RUNNING" ? "Running" : u.status}`,
  ];
  if (u.cached_calls) {
    lines.push(`Answered from saved answers: ${u.cached_calls} call${u.cached_calls === 1 ? "" : "s"}, ~${formatTokens(u.saved_tokens ?? 0)} tokens (${formatCost(u.saved_cost_usd, false)}) saved`);
  }
  if (u.wasted_tokens) {
    lines.push(`Paid for but discarded: ${formatTokens(u.wasted_tokens)} tokens (${formatCost(u.wasted_cost_usd ?? null, false)})`);
  }
  return lines.join("\n");
}
