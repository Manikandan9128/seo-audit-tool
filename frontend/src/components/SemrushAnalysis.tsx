import { useState } from "react";
import { api } from "../api/client";

interface Issue {
  summary: string;
  detail: string;
  recommendation: string;
  severity: "warn" | "opportunity" | "info";
}

interface Coverage {
  own_domain_overview: boolean;
  own_backlinks: boolean;
  competitor_backlinks: boolean;
  organic_competitors: boolean;
  keyword_gap: boolean;
  own_site_audit_pages: boolean;
}

const SEVERITY_STYLE: Record<Issue["severity"], { color: string; label: string }> = {
  warn: { color: "#991b1b", label: "Behind" },
  opportunity: { color: "#166534", label: "Opportunity" },
  info: { color: "#92400e", label: "Missing data" },
};

// What each missing export type would unlock, shown so the user knows exactly
// which Semrush report to pull next instead of wondering why a comparison is empty.
const COVERAGE_HINTS: { key: keyof Coverage; label: string }[] = [
  { key: "own_domain_overview", label: "Domain Overview for your site — unlocks organic traffic/keyword-count comparisons" },
  { key: "own_backlinks", label: "Backlinks export for your site — unlocks referring-domain gap comparisons" },
  { key: "competitor_backlinks", label: "Backlinks export for a competitor — unlocks referring-domain gap comparisons" },
  { key: "organic_competitors", label: "Organic Competitors export — unlocks traffic/keyword-count comparisons per competitor" },
  { key: "keyword_gap", label: "Keyword Gap export — unlocks keyword opportunity list" },
  { key: "own_site_audit_pages", label: "Semrush Site Audit \"crawled pages\" export for your site — unlocks technical/on-page issue checks" },
];

export default function SemrushAnalysis({ clientId }: { clientId: string }) {
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [hasData, setHasData] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [aiSummary, setAiSummary] = useState<{ summary: string; priorities: string[] } | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState("");

  async function runAnalysis() {
    setLoading(true);
    setError("");
    try {
      const res = await api.get(`/clients/${clientId}/semrush-analysis`);
      setIssues(res.data.issues);
      setHasData(res.data.has_data);
      setCoverage(res.data.coverage ?? null);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't run analysis");
    } finally {
      setLoading(false);
    }
  }

  async function runAiSummary() {
    setAiLoading(true);
    setAiError("");
    setAiSummary(null);
    try {
      const res = await api.get(`/clients/${clientId}/semrush-ai-summary`);
      if (res.data.error) {
        setAiError(res.data.error);
      } else {
        setAiSummary(res.data);
      }
    } catch (err: any) {
      setAiError(err?.response?.data?.detail || "Couldn't generate AI summary");
    } finally {
      setAiLoading(false);
    }
  }

  const missingHints = coverage ? COVERAGE_HINTS.filter((h) => !coverage[h.key]) : [];

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0, fontSize: 18 }}>Competitor Analysis</h3>
        <button onClick={runAnalysis} disabled={loading}>
          {loading ? "Analyzing..." : "Run Competitor Analysis"}
        </button>
      </div>
      <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 6 }}>
        Compares your uploaded Semrush data against competitors' — gaps found here are also included in the generated
        PPTX report.
      </p>
      {error && <p style={{ fontSize: 13, color: "#991b1b" }}>{error}</p>}
      {issues !== null && !hasData && (
        <p style={{ fontSize: 13, color: "var(--text-muted)" }}>
          No Semrush data uploaded yet — upload "Our Website Data" and "Competitor Data" above, then run this again.
        </p>
      )}
      {issues !== null && hasData && issues.length === 0 && (
        <p style={{ fontSize: 13, color: "var(--success)" }}>No gaps found in the uploaded data.</p>
      )}
      {issues !== null && hasData && missingHints.length > 0 && (
        <div style={{ marginTop: 12, padding: 10, background: "#f9f6f0", borderRadius: 6 }}>
          <p style={{ margin: 0, fontSize: 12, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase" }}>
            Upload these to unlock more comparisons
          </p>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {missingHints.map((h) => (
              <li key={h.key} style={{ fontSize: 13, color: "var(--text-muted)" }}>
                {h.label}
              </li>
            ))}
          </ul>
        </div>
      )}
      {issues !== null && hasData && issues.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 12 }}>
          {issues.map((issue, i) => {
            const style = SEVERITY_STYLE[issue.severity];
            return (
              <div key={i} style={{ borderLeft: `3px solid ${style.color}`, paddingLeft: 12 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                  <span style={{ fontSize: 11, fontWeight: 700, color: style.color, textTransform: "uppercase" }}>
                    {style.label}
                  </span>
                  <span style={{ fontSize: 14, fontWeight: 600 }}>{issue.summary}</span>
                </div>
                <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--text-muted)" }}>{issue.detail}</p>
                <p style={{ margin: "4px 0 0", fontSize: 13 }}>
                  <strong>Do:</strong> {issue.recommendation}
                </p>
              </div>
            );
          })}
        </div>
      )}
      {issues !== null && hasData && issues.length > 0 && (
        <div style={{ marginTop: 16, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h4 style={{ margin: 0, fontSize: 15 }}>AI Summary</h4>
            <button onClick={runAiSummary} disabled={aiLoading}>
              {aiLoading ? "Thinking..." : "Generate AI Summary"}
            </button>
          </div>
          <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 6 }}>
            Asks Gemini to turn the findings above into a plain-English summary and prioritized action list —
            grounded only in the data already found, nothing invented.
          </p>
          {aiError && <p style={{ fontSize: 13, color: "#991b1b" }}>{aiError}</p>}
          {aiSummary && (
            <div style={{ marginTop: 8 }}>
              <p style={{ fontSize: 14, lineHeight: 1.5 }}>{aiSummary.summary}</p>
              {aiSummary.priorities?.length > 0 && (
                <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                  {aiSummary.priorities.map((p, i) => (
                    <li key={i} style={{ fontSize: 14, marginBottom: 4 }}>
                      {p}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
