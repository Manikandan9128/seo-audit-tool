import { useState } from "react";
import { api } from "../api/client";

interface SiteAuditResult {
  url: string;
  https: boolean;
  reachable: boolean;
  status_code: number | null;
  load_time_ms: number;
  page_size_bytes: number | null;
  robots_txt: { present: boolean; sitemap_urls: string[] };
  sitemap: { present: boolean; url_count: number };
  meta: {
    title: string | null;
    title_length: number;
    meta_description: string | null;
    h1_count: number;
    viewport_present: boolean;
    canonical_present: boolean;
    structured_data_present: boolean;
    schema_types_found: string[];
    schema_types_missing: string[];
    og_tags_present: boolean;
  } | null;
  issues: string[];
  company_summary: string | null;
}

function Status({ ok, okLabel, failLabel }: { ok: boolean; okLabel: string; failLabel: string }) {
  return <span className={`status dot ${ok ? "ok" : "fail"}`}>{ok ? okLabel : failLabel}</span>;
}

interface RichResultItem {
  type: string | null;
  items: { name: string | null; issues: { severity: string | null; message: string | null }[] }[];
}

interface RichResultsData {
  verdict: string;
  detected_items: RichResultItem[];
  inspection_url: string | null;
}

function RichResultsValidator({ clientId, url }: { clientId: string; url: string }) {
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [data, setData] = useState<RichResultsData | null>(null);
  const [error, setError] = useState("");

  async function run() {
    setState("loading");
    setError("");
    try {
      const res = await api.get(`/clients/${clientId}/gsc/rich-results`, { params: { url } });
      setData(res.data);
      setState("done");
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Validation failed");
      setState("error");
    }
  }

  return (
    <div style={{ marginTop: 8 }}>
      <button className="secondary" onClick={run} disabled={state === "loading"}>
        {state === "loading" ? "Validating with Google…" : "Validate with Google Rich Results"}
      </button>
      {state === "error" && <p style={{ color: "var(--danger)", fontSize: 13, marginTop: 6 }}>{error}</p>}
      {state === "done" && data && (
        <div style={{ marginTop: 10 }}>
          <p style={{ fontSize: 13, fontWeight: 600 }}>
            Verdict: <span className={data.verdict === "PASS" ? "status dot ok" : "status dot fail"}>{data.verdict}</span>
          </p>
          {data.detected_items.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)" }}>No eligible rich result types detected on this page.</p>
          ) : (
            <ul className="issue-list">
              {data.detected_items.map((item, i) => (
                <li key={i}>
                  <span style={{ fontWeight: 600 }}>{item.type}</span>
                  {item.items.flatMap((sub) => sub.issues).length > 0 ? (
                    <ul>
                      {item.items.flatMap((sub, j) =>
                        sub.issues.map((issue, k) => (
                          <li key={`${j}-${k}`}>
                            {issue.severity}: {issue.message}
                          </li>
                        ))
                      )}
                    </ul>
                  ) : (
                    <span style={{ color: "var(--success)" }}> — valid, no issues</span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {data.inspection_url && (
            <p style={{ fontSize: 12, marginTop: 6 }}>
              <a href={data.inspection_url} target="_blank" rel="noreferrer">View in Search Console</a>
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function SiteAuditReport({
  result,
  clientId,
  gscConnected,
}: {
  result: SiteAuditResult;
  clientId?: string;
  gscConnected?: boolean;
}) {
  const pageSizeKb = result.page_size_bytes ? Math.round(result.page_size_bytes / 1024) : null;
  const meta = result.meta;

  return (
    <div className="report">
      <nav className="toc">
        <p className="toc-label">On this page</p>
        <ol>
          <li><a href="#sa-overview">Overview</a></li>
          <li><a href="#sa-crawlability">Crawlability</a></li>
          <li><a href="#sa-onpage">On-page signals</a></li>
          <li><a href="#sa-issues">Issues found</a></li>
        </ol>
      </nav>

      <main>
        <p className="eyebrow">Site Audit Report</p>

        {result.company_summary && (
          <section className="report-section">
            <h3>About this company</h3>
            <div className="card" style={{ padding: 16 }}>
              <p style={{ margin: 0, fontSize: 14, lineHeight: 1.6 }}>{result.company_summary}</p>
            </div>
          </section>
        )}

        <section id="sa-overview" className="report-section">
          <h3>Overview</h3>
          <p className="section-intro">Core reachability and delivery checks for the homepage.</p>
          <div className="metric-grid">
            <div className="metric">
              <div className="label">HTTPS</div>
              <div className={`value ${result.https ? "good" : "bad"}`}>{result.https ? "Yes" : "No"}</div>
            </div>
            <div className="metric">
              <div className="label">Status code</div>
              <div className={`value ${result.reachable ? "good" : "bad"}`}>{result.status_code ?? "—"}</div>
            </div>
            <div className="metric">
              <div className="label">Load time</div>
              <div className="value">{result.load_time_ms} ms</div>
            </div>
            <div className="metric">
              <div className="label">Page size</div>
              <div className="value">{pageSizeKb !== null ? `${pageSizeKb} KB` : "—"}</div>
            </div>
          </div>
        </section>

        <section id="sa-crawlability" className="report-section">
          <h3>Crawlability</h3>
          <p className="section-intro">Whether search engines can find and read the site's structure.</p>
          <div className="card">
            <table>
              <thead>
                <tr><th>Check</th><th>Result</th><th>Detail</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>robots.txt</td>
                  <td><Status ok={result.robots_txt.present} okLabel="Present" failLabel="Missing" /></td>
                  <td className="mono">/robots.txt</td>
                </tr>
                <tr>
                  <td>XML sitemap</td>
                  <td><Status ok={result.sitemap.present} okLabel="Present" failLabel="Missing" /></td>
                  <td className="mono">{result.sitemap.present ? `${result.sitemap.url_count} URLs listed` : "—"}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section id="sa-onpage" className="report-section">
          <h3>On-page signals</h3>
          <p className="section-intro">What's in the page head and body that search engines and social previews read directly.</p>
          <div className="card">
            {meta ? (
              <table>
                <thead>
                  <tr><th>Element</th><th>Result</th><th>Value</th></tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Title tag</td>
                    <td><Status ok={!!meta.title} okLabel="Present" failLabel="Missing" /></td>
                    <td>{meta.title ? `“${meta.title}” · ${meta.title_length} chars` : "—"}</td>
                  </tr>
                  <tr>
                    <td>Meta description</td>
                    <td><Status ok={!!meta.meta_description} okLabel="Present" failLabel="Missing" /></td>
                    <td>{meta.meta_description || "—"}</td>
                  </tr>
                  <tr>
                    <td>H1 tag</td>
                    <td><Status ok={meta.h1_count === 1} okLabel={`${meta.h1_count} found`} failLabel={`${meta.h1_count} found`} /></td>
                    <td>{meta.h1_count === 1 ? "Exactly one, as expected" : "Should be exactly one"}</td>
                  </tr>
                  <tr>
                    <td>Mobile viewport tag</td>
                    <td><Status ok={meta.viewport_present} okLabel="Present" failLabel="Missing" /></td>
                    <td>Renders correctly on mobile</td>
                  </tr>
                  <tr>
                    <td>Canonical tag</td>
                    <td><Status ok={meta.canonical_present} okLabel="Present" failLabel="Missing" /></td>
                    <td>Prevents duplicate-content signals</td>
                  </tr>
                  <tr>
                    <td>Open Graph tags</td>
                    <td><Status ok={meta.og_tags_present} okLabel="Present" failLabel="Missing" /></td>
                    <td>Social share previews</td>
                  </tr>
                  <tr>
                    <td>Structured data (JSON-LD)</td>
                    <td><Status ok={meta.structured_data_present} okLabel="Present" failLabel="Missing" /></td>
                    <td>
                      {meta.structured_data_present
                        ? meta.schema_types_found.length > 0
                          ? meta.schema_types_found.join(", ")
                          : "schema.org markup (type unreadable)"
                        : "schema.org markup"}
                    </td>
                  </tr>
                </tbody>
              </table>
            ) : (
              <p style={{ color: "var(--text-muted)", fontSize: 13.5 }}>Homepage could not be fetched — no on-page data available.</p>
            )}
            {meta && meta.schema_types_missing.length > 0 && (
              <p style={{ fontSize: 12.5, color: "var(--text-muted)", marginTop: 8 }}>
                Missing recommended schema: {meta.schema_types_missing.join(", ")}
              </p>
            )}
            {meta && meta.structured_data_present && clientId && gscConnected && (
              <RichResultsValidator clientId={clientId} url={result.url} />
            )}
            {meta && meta.structured_data_present && clientId && !gscConnected && (
              <p style={{ fontSize: 12.5, color: "var(--text-muted)", marginTop: 8 }}>
                Connect Google Search Console for this client to validate structured data against Google's rich result requirements.
              </p>
            )}
          </div>
        </section>

        <section id="sa-issues" className="report-section">
          <h3>Issues found</h3>
          <p className="section-intro">Everything flagged across the checks above, in one place.</p>
          {result.issues.length > 0 ? (
            <ul className="issue-list">
              {result.issues.map((issue, i) => (
                <li key={i}>
                  <span className="idx">{String(i + 1).padStart(2, "0")}</span>
                  <span>{issue}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p style={{ color: "var(--success)", fontSize: 13.5 }}>No issues found in this pass.</p>
          )}
        </section>
      </main>
    </div>
  );
}
