interface TrafficRow {
  date: string;
  sessions: string;
  total_users: string;
  page_views: string;
  engagement_rate: string;
  engagement_duration: string;
  active_users: string;
  bounce_rate: string;
}
interface TopPageRow {
  path: string;
  page_views: string;
  engagement_duration: string;
  active_users: string;
}
interface TrafficSourceRow {
  channel: string;
  sessions: string;
  users: string;
}
interface SearchQueryRow {
  query: string;
  clicks: number;
  impressions: number;
  ctr: number;
  position: number;
}
interface PagePerformanceRow {
  path: string;
  page_views: number;
  engagement_duration: string;
  bounce_rate: string;
  pct_of_total: number;
}
interface PagePerformance {
  total_pages: number;
  total_page_views: number;
  truncated: boolean;
  top_pages: PagePerformanceRow[];
  bottom_pages: PagePerformanceRow[];
}

export interface AnalyticsReportData {
  date_range: { start: string; end: string };
  traffic_overview: { rows: TrafficRow[] } | null;
  top_pages: { rows: TopPageRow[] } | null;
  traffic_sources: { rows: TrafficSourceRow[] } | null;
  search_queries: { rows: SearchQueryRow[] } | null;
  page_performance: PagePerformance | null;
}

function sum(rows: TrafficRow[], key: keyof TrafficRow) {
  return rows.reduce((acc, r) => acc + Number(r[key]), 0);
}

function fmtDuration(seconds: number) {
  if (!isFinite(seconds) || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

function fmtDate(yyyymmdd: string) {
  if (yyyymmdd.length !== 8) return yyyymmdd;
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

// Each Section* component renders exactly one PPTX slide's worth of content —
// the caller (ReportPreviewModal) wraps each in its own <Slide>, mirroring
// add_traffic_overview_slide / _table_slide / add_page_performance_slide in
// pptx_builder.py one-for-one so the preview matches the deck slide count.

export function SectionDateRange({ data }: { data: AnalyticsReportData }) {
  const traffic = data.traffic_overview?.rows ?? [];
  return (
    <p className="section-intro" style={{ marginBottom: 24 }}>
      {fmtDate(traffic[0]?.date ?? "")} to {fmtDate(traffic[traffic.length - 1]?.date ?? "")}
    </p>
  );
}

export function SectionTrafficOverview({ data }: { data: AnalyticsReportData }) {
  const traffic = data.traffic_overview?.rows ?? [];
  const totalSessions = sum(traffic, "sessions");
  const totalUsers = sum(traffic, "total_users");
  const totalPageViews = sum(traffic, "page_views");
  const avgEngagement =
    traffic.length > 0 ? (traffic.reduce((a, r) => a + Number(r.engagement_rate), 0) / traffic.length) * 100 : null;
  const avgBounce =
    traffic.length > 0 ? (traffic.reduce((a, r) => a + Number(r.bounce_rate), 0) / traffic.length) * 100 : null;

  return (
    <section className="report-section">
      <p className="section-intro">Sessions, users, and pageviews over the selected period.</p>
      <div className="metric-grid">
        <div className="metric">
          <div className="label">Sessions</div>
          <div className="value">{totalSessions.toLocaleString()}</div>
        </div>
        <div className="metric">
          <div className="label">Users</div>
          <div className="value">{totalUsers.toLocaleString()}</div>
        </div>
        <div className="metric">
          <div className="label">Page views</div>
          <div className="value">{totalPageViews.toLocaleString()}</div>
        </div>
        <div className="metric">
          <div className="label">Avg. engagement rate</div>
          <div className="value">{avgEngagement !== null ? `${avgEngagement.toFixed(1)}%` : "—"}</div>
        </div>
        <div className="metric">
          <div className="label">Bounce rate</div>
          <div className="value">{avgBounce !== null ? `${avgBounce.toFixed(1)}%` : "—"}</div>
        </div>
      </div>
    </section>
  );
}

export function SectionTopPages({ data }: { data: AnalyticsReportData }) {
  if (!data.top_pages) return null;
  return (
    <section className="report-section">
      <p className="section-intro">Most-viewed pages in this period, by pageviews.</p>
      <div className="card">
        <table>
          <thead>
            <tr><th>Page</th><th>Pageviews</th><th>Users</th><th>Avg. engagement / view</th></tr>
          </thead>
          <tbody>
            {data.top_pages.rows.map((p) => (
              <tr key={p.path}>
                <td>{p.path}</td>
                <td className="mono">{Number(p.page_views).toLocaleString()}</td>
                <td className="mono">{Number(p.active_users).toLocaleString()}</td>
                <td className="mono">
                  {fmtDuration(Number(p.engagement_duration) / Math.max(Number(p.page_views), 1))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function SectionPagePerformance({ data }: { data: AnalyticsReportData }) {
  if (!data.page_performance) return null;
  return (
    <section className="report-section">
      <p className="section-intro">
        {data.page_performance.total_pages} pages contributed {data.page_performance.total_page_views.toLocaleString()}{" "}
        pageviews this period{data.page_performance.truncated ? " (based on the top pages GA4 returned)" : ""}.
      </p>
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px" }}>
          <h4 style={{ color: "var(--success)", fontSize: 14, marginBottom: 8 }}>Top performing</h4>
          <div className="card">
            <table>
              <thead>
                <tr><th>Page</th><th>Views</th><th>% of total</th></tr>
              </thead>
              <tbody>
                {data.page_performance.top_pages.map((p) => (
                  <tr key={p.path}>
                    <td>{p.path}</td>
                    <td className="mono">{p.page_views.toLocaleString()}</td>
                    <td className="mono">{p.pct_of_total.toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div style={{ flex: "1 1 320px" }}>
          <h4 style={{ color: "#991b1b", fontSize: 14, marginBottom: 8 }}>Poor performing</h4>
          <div className="card">
            <table>
              <thead>
                <tr><th>Page</th><th>Views</th><th>% of total</th></tr>
              </thead>
              <tbody>
                {data.page_performance.bottom_pages.map((p) => (
                  <tr key={p.path}>
                    <td>{p.path}</td>
                    <td className="mono">{p.page_views.toLocaleString()}</td>
                    <td className="mono">{p.pct_of_total.toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}

export function SectionTrafficSources({ data }: { data: AnalyticsReportData }) {
  if (!data.traffic_sources) return null;
  const totalSessions = data.traffic_sources.rows.reduce((a, s) => a + Number(s.sessions), 0);
  return (
    <section className="report-section">
      <p className="section-intro">Where sessions came from, by channel.</p>
      <div className="card">
        <table>
          <thead>
            <tr><th>Channel</th><th>Sessions</th><th>Users</th><th>% of total</th></tr>
          </thead>
          <tbody>
            {data.traffic_sources.rows.map((s) => (
              <tr key={s.channel}>
                <td>{s.channel}</td>
                <td className="mono">{Number(s.sessions).toLocaleString()}</td>
                <td className="mono">{Number(s.users).toLocaleString()}</td>
                <td className="mono">
                  {totalSessions > 0 ? `${((Number(s.sessions) / totalSessions) * 100).toFixed(1)}%` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// Full scrolling report with a table-of-contents nav — used on the client
// detail page's inline "Run GA4 / Search Console Report" result. The report
// preview modal uses the individual Section* pieces above instead, one per
// slide, to mirror the generated PPTX exactly.
export default function AnalyticsReport({ data }: { data: AnalyticsReportData }) {
  return (
    <div className="report">
      <nav className="toc">
        <p className="toc-label">On this page</p>
        <ol>
          {data.traffic_overview && <li><a href="#ga-overview">Traffic overview</a></li>}
          {data.top_pages && <li><a href="#ga-top-pages">Top pages</a></li>}
          {data.page_performance && <li><a href="#ga-page-performance">Top vs. poor performing pages</a></li>}
          {data.traffic_sources && <li><a href="#ga-sources">Traffic sources</a></li>}
          {data.search_queries && <li><a href="#ga-queries">Search queries</a></li>}
        </ol>
      </nav>

      <main>
        <p className="eyebrow">Google Analytics &amp; Search Console Report</p>
        <SectionDateRange data={data} />

        {data.traffic_overview && (
          <div id="ga-overview">
            <h3>Traffic overview</h3>
            <SectionTrafficOverview data={data} />
          </div>
        )}
        {data.top_pages && (
          <div id="ga-top-pages">
            <h3>Top pages</h3>
            <SectionTopPages data={data} />
          </div>
        )}
        {data.page_performance && (
          <div id="ga-page-performance">
            <h3>Top vs. poor performing pages</h3>
            <SectionPagePerformance data={data} />
          </div>
        )}
        {data.traffic_sources && (
          <div id="ga-sources">
            <h3>Traffic sources</h3>
            <SectionTrafficSources data={data} />
          </div>
        )}
        {data.search_queries && (
          <div id="ga-queries">
            <h3>Search queries</h3>
            <SectionSearchQueries data={data} />
          </div>
        )}
      </main>
    </div>
  );
}

export function SectionSearchQueries({ data }: { data: AnalyticsReportData }) {
  if (!data.search_queries) return null;
  return (
    <section className="report-section">
      <p className="section-intro">Top Google Search Console queries by clicks, this period.</p>
      <div className="card">
        <table>
          <thead>
            <tr><th>Query</th><th>Clicks</th><th>Impressions</th><th>CTR</th><th>Avg. position</th></tr>
          </thead>
          <tbody>
            {data.search_queries.rows
              .slice()
              .sort((a, b) => b.clicks - a.clicks)
              .slice(0, 15)
              .map((q) => (
                <tr key={q.query}>
                  <td>{q.query}</td>
                  <td className="mono">{q.clicks}</td>
                  <td className="mono">{q.impressions}</td>
                  <td className="mono">{(q.ctr * 100).toFixed(1)}%</td>
                  <td className="mono">{q.position.toFixed(1)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
