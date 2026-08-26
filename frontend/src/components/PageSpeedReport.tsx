interface PageSpeedResult {
  strategy: string;
  scores: {
    performance: number | null;
    seo: number | null;
    accessibility: number | null;
    best_practices: number | null;
  };
  core_web_vitals: {
    largest_contentful_paint: string | null;
    cumulative_layout_shift: string | null;
    interaction_to_next_paint: string | null;
    first_contentful_paint: string | null;
  };
}

function scoreClass(score: number | null) {
  if (score === null) return "";
  if (score >= 90) return "good";
  if (score >= 50) return "";
  return "bad";
}

function ScoreCard({ label, score }: { label: string; score: number | null }) {
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className={`value ${scoreClass(score)}`}>{score ?? "—"}</div>
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
              <ScoreCard label="Performance" score={result.scores.performance} />
              <ScoreCard label="SEO" score={result.scores.seo} />
              <ScoreCard label="Accessibility" score={result.scores.accessibility} />
              <ScoreCard label="Best practices" score={result.scores.best_practices} />
            </div>
            <table>
              <thead>
                <tr><th>Core Web Vital</th><th>Value</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>Largest Contentful Paint (LCP)</td>
                  <td className="mono">{result.core_web_vitals.largest_contentful_paint ?? "—"}</td>
                </tr>
                <tr>
                  <td>Cumulative Layout Shift (CLS)</td>
                  <td className="mono">{result.core_web_vitals.cumulative_layout_shift ?? "—"}</td>
                </tr>
                <tr>
                  <td>Interaction to Next Paint (INP)</td>
                  <td className="mono">{result.core_web_vitals.interaction_to_next_paint ?? "—"}</td>
                </tr>
                <tr>
                  <td>First Contentful Paint (FCP)</td>
                  <td className="mono">{result.core_web_vitals.first_contentful_paint ?? "—"}</td>
                </tr>
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}
