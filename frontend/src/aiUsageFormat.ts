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
