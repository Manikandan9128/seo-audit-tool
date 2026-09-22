import ScriptTreemap from "./ScriptTreemap";

interface TreemapNode {
  name: string;
  resource_bytes: number;
  unused_bytes: number;
  children: TreemapNode[];
}

interface PageSpeedResult {
  strategy: string;
  scores: {
    performance: number | null;
    seo: number | null;
    accessibility: number | null;
    best_practices: number | null;
  };
  raw_scores?: {
    performance: number | null;
    seo: number | null;
    accessibility: number | null;
    best_practices: number | null;
  };
  metric_table?: {
    id: string;
    label: string;
    value: number;
    display_value: string | null;
    good_threshold: number;
    status: string | null;
  }[];
  script_treemap?: TreemapNode[];
}

function scoreClass(score: number | null) {
  if (score === null) return "";
  if (score >= 90) return "good";
  if (score >= 50) return "";
  return "bad";
}

function ScoreCard({ label, score, raw }: { label: string; score: number | null; raw?: number | null }) {
  return (
    <div className="metric" title={raw != null ? `Exact Lighthouse score: ${raw}` : undefined}>
      <div className="label">{label}</div>
      <div className={`value ${scoreClass(score)}`}>{score ?? "—"}</div>
      {raw != null && <div style={{ fontSize: 11, opacity: 0.6 }}>{raw.toFixed(3)}</div>}
    </div>
  );
}

export default function PageSpeedReport({
  mobile,
  desktop,
}: {
  mobile: PageSpeedResult | null;
  desktop: PageSpeedResult | null;
}) {
  const active = mobile;
  if (!active) return null;

  return (
    <div>
      {["mobile", "desktop"].map((strategy) => {
        const result = strategy === "mobile" ? mobile : desktop;
        if (!result) return null;
        return (
          <div key={strategy} style={{ marginBottom: 24 }}>
            <p className="eyebrow" style={{ marginBottom: 8 }}>{strategy}</p>
            <div className="metric-grid" style={{ marginBottom: 12 }}>
              <ScoreCard label="Performance" score={result.scores.performance} raw={result.raw_scores?.performance} />
              <ScoreCard label="SEO" score={result.scores.seo} raw={result.raw_scores?.seo} />
              <ScoreCard label="Accessibility" score={result.scores.accessibility} raw={result.raw_scores?.accessibility} />
              <ScoreCard label="Best practices" score={result.scores.best_practices} raw={result.raw_scores?.best_practices} />
            </div>
            <table>
              <thead>
                <tr><th>Metric</th><th>Value</th><th>Status</th></tr>
              </thead>
              <tbody>
                {(result.metric_table ?? []).map((row) => (
                  <tr key={row.id}>
                    <td>{row.label}</td>
                    <td className="mono">{row.display_value ?? "—"}</td>
                    <td>{row.status ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {result.script_treemap && result.script_treemap.length > 0 && (
              <ScriptTreemap nodes={result.script_treemap} />
            )}
          </div>
        );
      })}
    </div>
  );
}
