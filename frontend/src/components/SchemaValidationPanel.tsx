interface PageResult {
  meta: {
    schema_types_found?: string[];
    schema_field_issues?: string[];
  } | null;
}

const FIELD_ISSUE_RE = /^(.+?) schema missing required field: (.+)$/;

function aggregate(pages: PageResult[]) {
  let totalPages = 0;
  let pagesWithSchema = 0;
  const typeCounts = new Map<string, number>();
  const missingCounts = new Map<string, number>(); // "Type|field" -> count

  for (const page of pages) {
    if (!page.meta) continue;
    totalPages++;
    const types = page.meta.schema_types_found || [];
    if (types.length > 0) pagesWithSchema++;
    for (const t of types) typeCounts.set(t, (typeCounts.get(t) || 0) + 1);
    for (const issue of page.meta.schema_field_issues || []) {
      const m = issue.match(FIELD_ISSUE_RE);
      if (m) {
        const key = `${m[1]}|${m[2]}`;
        missingCounts.set(key, (missingCounts.get(key) || 0) + 1);
      }
    }
  }

  const typeCoverage = [...typeCounts.entries()]
    .map(([type, count]) => ({ type, count, pct: totalPages ? Math.round((100 * count) / totalPages) : 0 }))
    .sort((a, b) => b.count - a.count);

  const missingProperties = [...missingCounts.entries()]
    .map(([key, count]) => {
      const [type, field] = key.split("|");
      return { type, field, count };
    })
    .sort((a, b) => b.count - a.count);

  return { totalPages, pagesWithSchema, typeCoverage, missingProperties };
}

export default function SchemaValidationPanel({ pages }: { pages: PageResult[] }) {
  const { totalPages, pagesWithSchema, typeCoverage, missingProperties } = aggregate(pages);

  if (totalPages === 0) return null;

  return (
    <div style={{ marginTop: 24 }}>
      <h4 style={{ marginBottom: 4 }}>Schema Validator — whole site</h4>
      <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 12 }}>
        {pagesWithSchema.toLocaleString()} of {totalPages.toLocaleString()} crawled pages have some structured data.
        Required-property gaps below, per Google's structured-data requirements.
      </p>

      {typeCoverage.length > 0 && (
        <table style={{ width: "100%", marginBottom: 16 }}>
          <thead>
            <tr><th>Schema Type</th><th>Pages With It</th><th>Coverage</th></tr>
          </thead>
          <tbody>
            {typeCoverage.map((t) => (
              <tr key={t.type}>
                <td>{t.type}</td>
                <td>{t.count.toLocaleString()} / {totalPages.toLocaleString()}</td>
                <td>{t.pct}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {missingProperties.length > 0 ? (
        <>
          <p style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Missing required properties</p>
          <ul className="issue-list">
            {missingProperties.map((m, i) => (
              <li key={i}>
                <strong>{m.type}</strong> missing <code>{m.field}</code> on {m.count.toLocaleString()} of {totalPages.toLocaleString()} page(s)
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p style={{ fontSize: 13, color: "var(--success)" }}>No required-property gaps found in the schema types detected.</p>
      )}
    </div>
  );
}
