interface PageResult {
  url: string;
  reachable: boolean;
  status_code: number | null;
  meta: {
    title: string | null;
    meta_description: string | null;
    h1_count: number;
  } | null;
  issues: string[];
}

interface MultiPageAudit {
  base_url: string;
  sitemap_url: string | null;
  pages_checked: number;
  pages_with_issues: number;
  pages: PageResult[];
}

export default function PageAuditTable({ result }: { result: MultiPageAudit }) {
  return (
    <div>
      <div className="metric-grid" style={{ marginBottom: 16 }}>
        <div className="metric">
          <div className="label">Pages checked</div>
          <div className="value">{result.pages_checked}</div>
        </div>
        <div className="metric">
          <div className="label">Pages with issues</div>
          <div className={`value ${result.pages_with_issues > 0 ? "bad" : "good"}`}>{result.pages_with_issues}</div>
        </div>
        <div className="metric">
          <div className="label">Clean pages</div>
          <div className="value good">{result.pages_checked - result.pages_with_issues}</div>
        </div>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Page</th>
              <th>Title</th>
              <th>Meta description</th>
              <th>Issues</th>
            </tr>
          </thead>
          <tbody>
            {result.pages.map((page) => {
              const path = page.url.replace(result.base_url, "") || "/";
              return (
                <tr key={page.url}>
                  <td>
                    <a href={page.url} target="_blank" rel="noopener noreferrer" title={page.url}>
                      {path}
                    </a>
                  </td>
                  <td>
                    <span className={`status dot ${page.meta?.title ? "ok" : "fail"}`}>
                      {page.meta?.title ? "Present" : "Missing"}
                    </span>
                  </td>
                  <td>
                    <span className={`status dot ${page.meta?.meta_description ? "ok" : "fail"}`}>
                      {page.meta?.meta_description ? "Present" : "Missing"}
                    </span>
                  </td>
                  <td>
                    {page.issues.length === 0 ? (
                      <span style={{ color: "var(--success)", fontSize: 12.5 }}>None</span>
                    ) : (
                      <span style={{ color: "var(--danger)", fontSize: 12.5 }}>{page.issues.join(", ")}</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
