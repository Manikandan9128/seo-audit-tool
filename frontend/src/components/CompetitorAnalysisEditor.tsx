export interface AnalysisIssue {
  summary: string;
  detail: string;
  recommendation: string;
  severity: "warn" | "opportunity" | "info";
}

export interface CompetitorAnalysis {
  has_data: boolean;
  issues: AnalysisIssue[];
  coverage?: Record<string, boolean>;
}

const SEVERITY_LABEL: Record<AnalysisIssue["severity"], string> = {
  warn: "Behind",
  opportunity: "Opportunity",
  info: "Missing data",
};

export default function CompetitorAnalysisEditor({
  analysis,
  onChange,
}: {
  analysis: CompetitorAnalysis;
  onChange: (next: CompetitorAnalysis) => void;
}) {
  function setIssue(i: number, field: keyof AnalysisIssue, value: string) {
    const issues = analysis.issues.map((issue, idx) => (idx === i ? { ...issue, [field]: value } : issue));
    onChange({ ...analysis, issues });
  }

  if (!analysis.issues.length) {
    return <p style={{ fontSize: 13, color: "var(--text-muted)" }}>No gaps found in the uploaded data.</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0 }}>
        Review and edit before generating — up to 8 of these go on the "Competitive Gaps &amp; Opportunities" slide.
      </p>
      {analysis.issues.map((issue, i) => (
        <div key={i} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
          <label style={{ fontSize: 11, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase" }}>
            {SEVERITY_LABEL[issue.severity]}
          </label>
          <input
            style={{ width: "100%", marginTop: 4, fontWeight: 600 }}
            value={issue.summary}
            onChange={(e) => setIssue(i, "summary", e.target.value)}
          />
          <textarea
            style={{ width: "100%", marginTop: 6, minHeight: 40, fontFamily: "inherit", fontSize: 13 }}
            value={issue.detail}
            onChange={(e) => setIssue(i, "detail", e.target.value)}
          />
          <div style={{ display: "flex", alignItems: "flex-start", gap: 6, marginTop: 6 }}>
            <strong style={{ fontSize: 13, marginTop: 4 }}>Do:</strong>
            <textarea
              style={{ flex: 1, minHeight: 40, fontFamily: "inherit", fontSize: 13 }}
              value={issue.recommendation}
              onChange={(e) => setIssue(i, "recommendation", e.target.value)}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
