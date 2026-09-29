import { useState } from "react";

import { formatCost, formatTokens } from "../aiUsageFormat";

// High Token Usage confirmation (2026-09-29 global AI token spec). Shown
// before Preview/Download on a paid provider when the estimate is over the
// warning threshold. Lists each heavy AI step with estimated input/output
// tokens and cost; steps over the warning say why; steps over the hard
// safety limit can't run. Unticked steps are skipped (sent as
// skip_ai_steps) and left out of the deck. Limits come from the server
// (ai_usage.AI_CONFIG) — nothing is hard-coded here.

export type AiUsageStep = {
  key: string;
  label: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number | null;
  over_warning: boolean;
  over_hard_limit: boolean;
  reason: string | null;
};

export type AiUsageEstimate = {
  provider_label: string;
  model: string | null;
  per_run_billing: boolean;
  needs_confirmation: boolean;
  steps: AiUsageStep[];
  other: { label: string; input_tokens: number; output_tokens: number; total_tokens: number; cost_usd: number | null };
  limits: {
    step_warning_tokens: number;
    step_hard_limit_tokens: number;
    report_warning_tokens: number;
    report_hard_limit_tokens: number;
  };
};

const HARD_LIMIT_MESSAGE =
  "This analysis exceeds the configured maximum token limit. Reduce the analysis scope or increase the maximum limit before continuing.";

export default function AiUsageModal({
  estimate,
  onCancel,
  onProceed,
}: {
  estimate: AiUsageEstimate;
  onCancel: () => void;
  onProceed: (skipped: string[]) => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(estimate.steps.filter((s) => !s.over_hard_limit).map((s) => s.key)),
  );
  const chosen = estimate.steps.filter((s) => selected.has(s.key));
  const sum = (key: "input_tokens" | "output_tokens" | "total_tokens") =>
    chosen.reduce((n, s) => n + s[key], 0) + estimate.other[key];
  const totalIn = sum("input_tokens");
  const totalOut = sum("output_tokens");
  const total = sum("total_tokens");
  const costs = [...chosen.map((s) => s.cost_usd), estimate.other.cost_usd];
  const totalCost = costs.every((c) => c !== null) ? costs.reduce((n, c) => n + (c as number), 0) : null;
  const overReportHard = total > estimate.limits.report_hard_limit_tokens;
  const overReportWarning = total > estimate.limits.report_warning_tokens;

  function toggle(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 110, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}
      onClick={onCancel}
    >
      <div className="card" style={{ maxWidth: 580, width: "100%", display: "flex", flexDirection: "column", gap: 12 }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ margin: 0, fontSize: 18 }}>⚠ High Token Usage</h3>
        <p style={{ margin: 0, fontSize: 13 }}>
          This analysis may use a high number of tokens and could increase API usage/cost. Untick any step you don't
          need — it will be skipped and left out of the report.
        </p>
        <div className="muted" style={{ fontSize: 12 }}>
          Provider: <strong>{estimate.provider_label}</strong>
          {estimate.model && (
            <>
              {" "}· Model: <strong>{estimate.model}</strong>
            </>
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {estimate.steps.map((step) => (
            <div key={step.key} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14, opacity: step.over_hard_limit ? 0.6 : 1 }}>
                <input
                  type="checkbox"
                  checked={selected.has(step.key)}
                  disabled={step.over_hard_limit}
                  onChange={() => toggle(step.key)}
                />
                <span style={{ flex: 1 }}>{step.label}</span>
                <span className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                  {formatTokens(step.input_tokens)} in · {formatTokens(step.output_tokens)} out ·{" "}
                  {formatCost(step.cost_usd, estimate.per_run_billing)}
                </span>
              </label>
              {step.over_hard_limit ? (
                <span style={{ fontSize: 12, color: "#991b1b", marginLeft: 24 }}>
                  Over the maximum limit ({formatTokens(estimate.limits.step_hard_limit_tokens)} tokens) — can't run.
                </span>
              ) : (
                step.reason && (
                  <span style={{ fontSize: 12, color: "#92400e", marginLeft: 24 }}>{step.reason}</span>
                )
              )}
            </div>
          ))}
          <div className="muted" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
            <span style={{ width: 13 }} />
            <span style={{ flex: 1 }}>{estimate.other.label}</span>
            <span style={{ fontSize: 12, whiteSpace: "nowrap" }}>
              {formatTokens(estimate.other.input_tokens)} in · {formatTokens(estimate.other.output_tokens)} out ·{" "}
              {formatCost(estimate.other.cost_usd, estimate.per_run_billing)}
            </span>
          </div>
        </div>

        <div style={{ fontSize: 13, display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 12px" }}>
          <span className="muted">Estimated input tokens</span>
          <span>{formatTokens(totalIn)}</span>
          <span className="muted">Estimated output tokens</span>
          <span>{formatTokens(totalOut)}</span>
          <span className="muted">Estimated total tokens</span>
          <strong style={{ color: overReportWarning ? "#92400e" : undefined }}>{formatTokens(total)}</strong>
          <span className="muted">Estimated cost</span>
          <strong>{formatCost(totalCost, estimate.per_run_billing)}</strong>
        </div>
        {overReportHard && (
          <div style={{ fontSize: 13, color: "#991b1b", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 6, padding: "8px 10px" }}>
            {HARD_LIMIT_MESSAGE}
          </div>
        )}
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
          Estimates only — actual usage depends on the site's data. Warning at{" "}
          {formatTokens(estimate.limits.report_warning_tokens)} tokens per report; hard limit{" "}
          {formatTokens(estimate.limits.report_hard_limit_tokens)}.
        </p>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="btn btn-secondary" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            disabled={overReportHard}
            onClick={() => onProceed(estimate.steps.filter((s) => !selected.has(s.key)).map((s) => s.key))}
          >
            Proceed
          </button>
        </div>
      </div>
    </div>
  );
}
