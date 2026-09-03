interface PageResult {
  url?: string;
  meta: {
    schema_types_found?: string[];
    schema_field_issues?: string[];
  } | null;
}

const FIELD_ISSUE_RE = /^(.+?) schema missing required field: (.+)$/;

// Same contextual shape rules as the backend (technical_seo_service.py
// aggregate_schema_validation / pptx_builder._SCHEMA_TYPE_REQUIRED_SHAPES):
// a type is only flagged "missing" when a page shaped for it actually
// exists, so a site with no /product/ URLs doesn't get a false "no Product
// schema" warning.
const SCHEMA_TYPE_SHAPE_RE: Record<string, RegExp> = {
  "Article-type": /\/(?:blog|news|articles?|posts?)\//i,
  Product: /\/(?:products?|shop|store|items?)\//i,
  LocalBusiness: /\/(?:locations?|store-locator|near-me|branch(?:es)?)\//i,
  JobPosting: /\/(?:careers?|jobs?)\//i,
  Event: /\/events?\//i,
  FAQPage: /faq|frequently[\s-]asked[\s-]questions/i,
};
// Article/BlogPosting/NewsArticle all satisfy the same blog-shaped-page
// finding, so they collapse into one "Article-type" row instead of three.
const SCHEMA_TYPE_GROUP_MEMBERS: Record<string, string[]> = {
  "Article-type": ["Article", "BlogPosting", "NewsArticle"],
};
const SITE_WIDE_SCHEMA_TYPES = ["Organization", "WebSite", "BreadcrumbList"];

function aggregate(pages: PageResult[]) {
  let totalPages = 0;
  let pagesWithSchema = 0;
  const typeCounts = new Map<string, number>();
  const missingCounts = new Map<string, number>(); // "Type|field" -> count
  const shapePageCounts = new Map<string, number>();
  const shapeTypeFoundCounts = new Map<string, number>();

  for (const page of pages) {
    if (!page.meta) continue;
    totalPages++;
    const types = page.meta.schema_types_found || [];
    const typeSet = new Set(types);
    if (types.length > 0) pagesWithSchema++;
    for (const t of types) typeCounts.set(t, (typeCounts.get(t) || 0) + 1);
    for (const issue of page.meta.schema_field_issues || []) {
      const m = issue.match(FIELD_ISSUE_RE);
      if (m) {
        const key = `${m[1]}|${m[2]}`;
        missingCounts.set(key, (missingCounts.get(key) || 0) + 1);
      }
    }

    const url = page.url || "";
    for (const [label, shapeRe] of Object.entries(SCHEMA_TYPE_SHAPE_RE)) {
      if (shapeRe.test(url)) {
        shapePageCounts.set(label, (shapePageCounts.get(label) || 0) + 1);
        const members = SCHEMA_TYPE_GROUP_MEMBERS[label] || [label];
        if (members.some((m) => typeSet.has(m))) {
          shapeTypeFoundCounts.set(label, (shapeTypeFoundCounts.get(label) || 0) + 1);
        }
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

  const missingTypes: { type: string; reason: string }[] = [];
  for (const t of Object.keys(SCHEMA_TYPE_SHAPE_RE)) {
    const relevant = shapePageCounts.get(t) || 0;
    const found = shapeTypeFoundCounts.get(t) || 0;
    const members = SCHEMA_TYPE_GROUP_MEMBERS[t] || [t];
    const foundAnywhere = members.some((m) => (typeCounts.get(m) || 0) > 0);
    if (relevant > 0 && found === 0 && !foundAnywhere) {
      missingTypes.push({ type: t, reason: `${relevant} relevant page(s) found, 0 have ${t} schema` });
    }
  }
  for (const t of SITE_WIDE_SCHEMA_TYPES) {
    if (totalPages > 0 && !(typeCounts.get(t) || 0)) {
      missingTypes.push({ type: t, reason: `not found on any of the ${totalPages} crawled pages` });
    }
  }

  return { totalPages, pagesWithSchema, typeCoverage, missingProperties, missingTypes };
}

export default function SchemaValidationPanel({ pages }: { pages: PageResult[] }) {
  const { totalPages, pagesWithSchema, typeCoverage, missingProperties, missingTypes } = aggregate(pages);

  if (totalPages === 0) return null;

  return (
    <div style={{ marginTop: 24 }}>
      <h4 style={{ marginBottom: 4 }}>Schema Validator — whole site</h4>
      <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 12 }}>
        {pagesWithSchema.toLocaleString()} of {totalPages.toLocaleString()} crawled pages have some structured data.
        Required-property gaps below, per Google's structured-data requirements.
      </p>

      {missingTypes.length > 0 && (
        <>
          <p style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Missing schema types</p>
          <ul className="issue-list" style={{ marginBottom: 16 }}>
            {missingTypes.map((m) => (
              <li key={m.type}>
                <strong>{m.type}</strong> — {m.reason}
              </li>
            ))}
          </ul>
        </>
      )}

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
