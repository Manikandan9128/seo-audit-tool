import { useEffect, useState, type ReactNode } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import SiteAuditHistory from "../components/SiteAuditHistory";
import PageSpeedReport from "../components/PageSpeedReport";
import PageAuditTable from "../components/PageAuditTable";
import AnalyticsReport from "../components/AnalyticsReport";
import CompanyOverviewEditor from "../components/CompanyOverviewEditor";
import type { CompanyOverview } from "../components/CompanyOverviewEditor";
import SemrushImportCard from "../components/SemrushImportCard";
import SemrushAnalysis from "../components/SemrushAnalysis";
import ReportPreviewModal from "../components/ReportPreviewModal";
import type { ReportPreviewData } from "../components/ReportPreviewModal";
import type { CompetitorAnalysis } from "../components/CompetitorAnalysisEditor";

interface Client {
  id: string;
  name: string;
  website_url: string;
  ga4_property_id: string | null;
  gsc_site_url: string | null;
  google_connected: boolean;
}

interface GA4Property {
  name: string;
  display_name: string;
}

interface GSCSite {
  site_url: string;
  permission_level: string;
}

interface SemrushImportSummary {
  id: string;
  import_type: string;
  original_filename: string;
  row_count: number;
  created_at: string;
  is_own_site?: boolean;
  domain_label?: string | null;
}

export default function ClientDetailPage() {
  const { clientId } = useParams();
  const [searchParams] = useSearchParams();
  const missingScopes = searchParams.get("missing_scopes") === "1";
  const [client, setClient] = useState<Client | null>(null);
  const [properties, setProperties] = useState<GA4Property[]>([]);
  const [sites, setSites] = useState<GSCSite[]>([]);
  const [error, setError] = useState("");

  const [auditLoading, setAuditLoading] = useState(false);
  const [auditHistoryKey, setAuditHistoryKey] = useState(0);

  const [psiMobile, setPsiMobile] = useState<any>(null);
  const [psiDesktop, setPsiDesktop] = useState<any>(null);
  const [psiLoading, setPsiLoading] = useState(false);

  const [pageAuditResult, setPageAuditResult] = useState<any>(null);
  const [pageAuditLoading, setPageAuditLoading] = useState(false);
  const [pageAuditProgress, setPageAuditProgress] = useState<{ checked: number; total: number | null } | null>(null);

  const [analyticsResult, setAnalyticsResult] = useState<any>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsStart, setAnalyticsStart] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().slice(0, 10);
  });
  const [analyticsEnd, setAnalyticsEnd] = useState(() => new Date().toISOString().slice(0, 10));

  const [imports, setImports] = useState<SemrushImportSummary[]>([]);

  const [reportLoading, setReportLoading] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewData, setPreviewData] = useState<ReportPreviewData | null>(null);
  const [previewOverview, setPreviewOverview] = useState<CompanyOverview | null>(null);
  const [previewCompetitorAnalysis, setPreviewCompetitorAnalysis] = useState<CompetitorAnalysis | null>(null);

  const [overview, setOverview] = useState<CompanyOverview | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [overviewMsg, setOverviewMsg] = useState("");

  const [techStack, setTechStack] = useState<any>(null);
  const [techStackLoading, setTechStackLoading] = useState(false);
  const [techStackMsg, setTechStackMsg] = useState("");

  const [uxNotes, setUxNotes] = useState("");

  const SECTION_OPTIONS = [
    { key: "overview", label: "Company Overview" },
    { key: "site_audit", label: "Site Audit" },
    { key: "all_pages", label: "All Pages" },
    { key: "pagespeed", label: "PageSpeed Insights" },
    { key: "tech_stack", label: "Tech Stack & Hosting" },
    { key: "analytics", label: "Analytics (GA4 / Search Console)" },
  ] as const;
  type SectionKey = (typeof SECTION_OPTIONS)[number]["key"];

  const [selectedSections, setSelectedSections] = useState<SectionKey[]>(
    SECTION_OPTIONS.map((s) => s.key)
  );
  const [sectionDropdownOpen, setSectionDropdownOpen] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [hasGenerated, setHasGenerated] = useState(false);

  function toggleSection(key: SectionKey) {
    setSelectedSections((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  }

  async function generateSelectedReport() {
    setGenerating(true);
    setError("");
    setCollapsedSections((prev) => prev.filter((k) => !selectedSections.includes(k)));
    try {
      const tasks: Promise<any>[] = [];
      if (selectedSections.includes("overview")) tasks.push(loadOverview());
      if (selectedSections.includes("site_audit")) tasks.push(runSiteAudit());
      if (selectedSections.includes("all_pages")) tasks.push(runPageAudit());
      if (selectedSections.includes("pagespeed")) tasks.push(runPageSpeed());
      if (selectedSections.includes("tech_stack")) tasks.push(loadTechStack());
      if (selectedSections.includes("analytics") && client?.google_connected) {
        tasks.push(runAnalyticsReport());
      }
      await Promise.all(tasks);
      setHasGenerated(true);
    } finally {
      setGenerating(false);
    }
  }

  async function loadClient() {
    const res = await api.get(`/clients/${clientId}`);
    setClient(res.data);
  }

  async function loadImports() {
    const res = await api.get(`/clients/${clientId}/semrush-imports`);
    setImports(res.data);
  }

  useEffect(() => {
    loadClient();
    loadImports();
  }, [clientId]);

  async function connectGoogle() {
    const res = await api.get(`/clients/${clientId}/google/connect`);
    window.location.href = res.data.auth_url;
  }

  async function loadProperties() {
    setError("");
    try {
      const [propsRes, sitesRes] = await Promise.all([
        api.get(`/clients/${clientId}/ga4/properties`),
        api.get(`/clients/${clientId}/gsc/sites`),
      ]);
      setProperties(propsRes.data);
      setSites(sitesRes.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Failed to load Google properties");
    }
  }

  async function selectProperties(ga4PropertyId: string, gscSiteUrl: string) {
    await api.post(`/clients/${clientId}/select-properties`, {
      ga4_property_id: ga4PropertyId || null,
      gsc_site_url: gscSiteUrl || null,
    });
    loadClient();
  }

  async function runSiteAudit() {
    setAuditLoading(true);
    setError("");
    try {
      await api.post(`/clients/${clientId}/site-audit`);
      setAuditHistoryKey((k) => k + 1);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Site audit failed");
    } finally {
      setAuditLoading(false);
    }
  }

  async function runAnalyticsReport() {
    setAnalyticsLoading(true);
    setError("");
    try {
      const res = await api.get(`/clients/${clientId}/analytics-report`, {
        params: { start_date: analyticsStart, end_date: analyticsEnd },
      });
      setAnalyticsResult(res.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Analytics report failed");
    } finally {
      setAnalyticsLoading(false);
    }
  }

  async function runPageAudit() {
    setPageAuditLoading(true);
    setPageAuditResult(null);
    setPageAuditProgress(null);
    setError("");
    try {
      const startRes = await api.post(`/clients/${clientId}/site-audit-pages/start`, null, { params: { limit: 200000 } });
      const jobId = startRes.data.job_id;
      await pollPageAuditJob(jobId);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Page-by-page audit failed");
      setPageAuditLoading(false);
    }
  }

  async function pollPageAuditJob(jobId: string) {
    const res = await api.get(`/clients/${clientId}/site-audit-pages/${jobId}`);
    const job = res.data;
    setPageAuditProgress({ checked: job.pages_checked, total: job.pages_total });
    if (job.status === "done") {
      setPageAuditResult(job.result);
      setPageAuditLoading(false);
    } else if (job.status === "failed") {
      setError(job.error || "Page-by-page audit failed");
      setPageAuditLoading(false);
    } else {
      setTimeout(() => pollPageAuditJob(jobId), 1500);
    }
  }

  async function runPageSpeed() {
    setPsiLoading(true);
    setError("");
    try {
      const [mobileRes, desktopRes] = await Promise.all([
        api.post(`/clients/${clientId}/pagespeed`, null, { params: { strategy: "mobile" } }),
        api.post(`/clients/${clientId}/pagespeed`, null, { params: { strategy: "desktop" } }),
      ]);
      setPsiMobile(mobileRes.data);
      setPsiDesktop(desktopRes.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "PageSpeed Insights failed");
    } finally {
      setPsiLoading(false);
    }
  }

  async function loadOverview() {
    setOverviewLoading(true);
    setOverviewMsg("");
    try {
      const [overviewRes, catalogueRes] = await Promise.all([
        api.get(`/clients/${clientId}/company-overview`),
        api.get(`/clients/${clientId}/product-catalogue`).catch(() => ({ data: { products: [] } })),
      ]);
      const catalogueNames: string[] = (catalogueRes.data.products || []).map((p: any) => p.name);
      const data = overviewRes.data;
      setOverview({
        company_name: data.company_name ?? null,
        description: data.description ?? null,
        products: data.products?.length ? data.products : catalogueNames,
        solutions: data.solutions ?? [],
        industries: data.industries ?? [],
        kpis: data.kpis ?? [],
        registration_info: data.registration_info ?? null,
        contact: data.contact ?? null,
        products_by_category: data.products_by_category ?? {},
        target_country: data.target_country ?? null,
        primary_buyers: data.primary_buyers ?? [],
        daily_users: data.daily_users ?? [],
        beneficiaries: data.beneficiaries ?? [],
        target_market: data.target_market ?? null,
      });
    } catch (err: any) {
      setOverviewMsg(err?.response?.data?.detail || "Couldn't load company overview");
    } finally {
      setOverviewLoading(false);
    }
  }

  async function loadTechStack() {
    setTechStackLoading(true);
    setTechStackMsg("");
    try {
      const res = await api.get(`/clients/${clientId}/tech-stack`);
      setTechStack(res.data);
    } catch (err: any) {
      setTechStackMsg(err?.response?.data?.detail || "Couldn't detect tech stack");
    } finally {
      setTechStackLoading(false);
    }
  }

  async function openPreview() {
    setPreviewLoading(true);
    setError("");
    try {
      const body = {
        ...(overview ? { company_overview_override: overview } : {}),
        ...(uxNotes.trim() ? { ux_notes: uxNotes.trim() } : {}),
      };
      const res = await api.post(`/clients/${clientId}/report-preview`, Object.keys(body).length ? body : null);
      setPreviewData(res.data);
      setPreviewOverview(res.data.company_overview);
      setPreviewCompetitorAnalysis(res.data.competitor_analysis);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't load report preview");
    } finally {
      setPreviewLoading(false);
    }
  }

  async function downloadReportWithBody(body: any, closePreviewAfter: boolean) {
    setReportLoading(true);
    setError("");
    try {
      const res = await api.post(`/clients/${clientId}/generate-report`, body, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `${client?.name || "client"}-seo-audit.pptx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      if (closePreviewAfter) setPreviewData(null);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Report generation failed");
    } finally {
      setReportLoading(false);
    }
  }

  function downloadReportDirect() {
    const body = {
      ...(overview ? { company_overview_override: overview } : {}),
      ...(uxNotes.trim() ? { ux_notes: uxNotes.trim() } : {}),
    };
    downloadReportWithBody(Object.keys(body).length ? body : null, false);
  }

  function downloadReportFromPreview() {
    const body = {
      company_overview_override: previewOverview,
      competitor_analysis_override: previewCompetitorAnalysis,
      ...(uxNotes.trim() ? { ux_notes: uxNotes.trim() } : {}),
    };
    downloadReportWithBody(body, true);
  }

  const [collapsedSections, setCollapsedSections] = useState<SectionKey[]>([]);

  function toggleCollapsed(key: SectionKey) {
    setCollapsedSections((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  }

  function sectionStatus(key: SectionKey, loading: boolean, hasData: boolean): { label: string; cls: string } {
    if (!selectedSections.includes(key)) return { label: "Not included", cls: "muted" };
    if (loading) return { label: "Generating…", cls: "muted" };
    if (hasData) return { label: "Ready", cls: "success" };
    return { label: "Pending", cls: "muted" };
  }

  function SectionCard({
    sectionKey,
    title,
    description,
    loading,
    hasData,
    children,
  }: {
    sectionKey: SectionKey;
    title: string;
    description: string;
    loading: boolean;
    hasData: boolean;
    children?: ReactNode;
  }) {
    const status = sectionStatus(sectionKey, loading, hasData);
    const collapsed = collapsedSections.includes(sectionKey);
    return (
      <div
        className="card"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 4,
          borderTop: `3px solid ${hasData ? "var(--success)" : "var(--border-strong)"}`,
        }}
      >
        <div
          onClick={() => toggleCollapsed(sectionKey)}
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 8,
            cursor: "pointer",
            userSelect: "none",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              style={{
                display: "inline-block",
                transition: "transform 0.15s ease",
                transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)",
                color: "var(--text-muted)",
                fontSize: 12,
              }}
            >
              ▾
            </span>
            <h3 style={{ margin: 0, fontSize: 17 }}>{title}</h3>
          </div>
          <span className={`badge ${status.cls}`}>{status.label}</span>
        </div>
        {!collapsed && (
          <>
            <p style={{ color: "var(--text-muted)", fontSize: 13, margin: "2px 0 0" }}>{description}</p>
            {!loading && !hasData && (
              <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 10, fontStyle: "italic" }}>
                Not generated yet — check this section and click Generate Report.
              </p>
            )}
            {children}
          </>
        )}
      </div>
    );
  }

  if (!client) return <p>Loading...</p>;

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", display: "flex", flexDirection: "column", gap: 20 }}>
      <div
        className="card"
        style={{
          position: "sticky",
          top: 76,
          zIndex: 20,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          boxShadow: "0 4px 16px rgba(20, 20, 15, 0.06)",
        }}
      >
        <div>
          <p className="eyebrow" style={{ margin: "0 0 4px" }}>
            Client
          </p>
          <h2 style={{ margin: 0 }}>{client.name}</h2>
          <p style={{ color: "var(--text-muted)", margin: "4px 0 0", fontSize: 13.5 }}>{client.website_url}</p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "flex-start", position: "relative" }}>
          <div style={{ position: "relative" }}>
            <button className="secondary" onClick={() => setSectionDropdownOpen((o) => !o)}>
              Sections ({selectedSections.length}) ▾
            </button>
            {sectionDropdownOpen && (
              <>
                <div
                  onClick={() => setSectionDropdownOpen(false)}
                  style={{ position: "fixed", inset: 0, zIndex: 10 }}
                />
                <div
                  className="card"
                  style={{
                    position: "absolute",
                    top: "110%",
                    right: 0,
                    zIndex: 11,
                    width: 260,
                    padding: 12,
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                    boxShadow: "0 12px 32px rgba(20, 20, 15, 0.14)",
                  }}
                >
                  {SECTION_OPTIONS.map((opt) => (
                    <label key={opt.key} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                      <input
                        type="checkbox"
                        checked={selectedSections.includes(opt.key)}
                        onChange={() => toggleSection(opt.key)}
                      />
                      {opt.label}
                    </label>
                  ))}
                </div>
              </>
            )}
          </div>
          <button onClick={generateSelectedReport} disabled={generating || selectedSections.length === 0}>
            {generating ? "Generating..." : "Generate Report"}
          </button>
          {hasGenerated && (
            <>
              <button onClick={openPreview} disabled={previewLoading}>
                {previewLoading ? "Loading..." : "Preview Report"}
              </button>
              <button onClick={downloadReportDirect} disabled={reportLoading}>
                {reportLoading ? "Generating..." : "Download Report (PPTX)"}
              </button>
            </>
          )}
        </div>
      </div>

      {previewData && (
        <ReportPreviewModal
          data={previewData}
          companyOverview={previewOverview}
          onCompanyOverviewChange={setPreviewOverview}
          competitorAnalysis={previewCompetitorAnalysis}
          onCompetitorAnalysisChange={setPreviewCompetitorAnalysis}
          onDownload={downloadReportFromPreview}
          onClose={() => setPreviewData(null)}
          downloading={reportLoading}
        />
      )}

      {missingScopes && (
        <div className="card" style={{ borderColor: "#fcd34d", background: "#fffbeb", color: "#92400e" }}>
          Google was connected, but Analytics / Search Console permission wasn't granted. Click{" "}
          <strong>Connect Google</strong> again and check <strong>both</strong> boxes ("See and download your Google
          Analytics data" and "View Search Console data") on the consent screen.
        </div>
      )}

      {error && (
        <div className="card" style={{ borderColor: "#fca5a5", background: "#fef2f2", color: "#991b1b" }}>
          {error}
        </div>
      )}

      <div className="card">
        <h3 style={{ margin: 0, fontSize: 17 }}>Manual UX / QA Notes</h3>
        <p style={{ color: "var(--text-muted)", fontSize: 13, margin: "4px 0 10px" }}>
          Optional — paste notes from a manual walkthrough (broken checkout, dead CTAs, missing trust signals,
          etc.). Included in Preview/Download as UI-Level Fixes and Conversion Opportunities. Left blank, the
          report states plainly that a manual UX pass hasn't been done yet.
        </p>
        <textarea
          value={uxNotes}
          onChange={(e) => setUxNotes(e.target.value)}
          placeholder="e.g. Checkout page's 'Apply Coupon' button does nothing on click. No customer reviews shown on product pages. ..."
          rows={4}
          style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
        />
      </div>

      {(selectedSections.includes("overview") ||
        selectedSections.includes("site_audit") ||
        selectedSections.includes("pagespeed") ||
        selectedSections.includes("tech_stack")) && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Company Overview — review/edit before generating */}
          {selectedSections.includes("overview") && (
            <SectionCard
              sectionKey="overview"
              title="Company Overview"
              description='Crawls About/Products/legal pages + sitemap for the "About" and "Products & Services" report slides. Edit below, then Download Report uses your edits instead of re-crawling.'
              loading={overviewLoading}
              hasData={!!overview}
            >
              {overviewMsg && <p style={{ fontSize: 13, color: "#991b1b" }}>{overviewMsg}</p>}
              {overview && (
                <div style={{ marginTop: 16 }}>
                  <CompanyOverviewEditor overview={overview} onChange={setOverview} />
                </div>
              )}
            </SectionCard>
          )}

          {/* Site Audit */}
          {selectedSections.includes("site_audit") && (
            <SectionCard
              sectionKey="site_audit"
              title="Site Audit"
              description="Checks HTTPS, robots.txt, sitemap, titles, meta tags — no login required."
              loading={auditLoading}
              hasData={true}
            >
              <div style={{ marginTop: 12 }}>
                <SiteAuditHistory clientId={clientId!} refreshKey={auditHistoryKey} />
              </div>
            </SectionCard>
          )}

          {/* PageSpeed Insights */}
          {selectedSections.includes("pagespeed") && (
            <SectionCard
              sectionKey="pagespeed"
              title="PageSpeed Insights"
              description="Lighthouse scores and Core Web Vitals for mobile and desktop — no login required."
              loading={psiLoading}
              hasData={!!psiMobile}
            >
              {psiMobile && (
                <div style={{ marginTop: 16 }}>
                  <PageSpeedReport mobile={psiMobile} desktop={psiDesktop} />
                </div>
              )}
            </SectionCard>
          )}

          {/* Tech Stack & Hosting */}
          {selectedSections.includes("tech_stack") && (
            <SectionCard
              sectionKey="tech_stack"
              title="Tech Stack & Hosting"
              description="Response headers, DNS/PTR records, and HTML markers — CMS, framework, hosting, CDN, analytics. No login required."
              loading={techStackLoading}
              hasData={!!techStack}
            >
              {techStackMsg && <p style={{ fontSize: 13, color: "#991b1b" }}>{techStackMsg}</p>}
              {techStack && (
                <div style={{ marginTop: 16 }}>
                  <div className="metric-grid">
                    <div className="metric">
                      <div className="label">Hostname</div>
                      <div className="value" style={{ fontSize: 14 }}>{techStack.hostname || "—"}</div>
                    </div>
                    <div className="metric">
                      <div className="label">IP address</div>
                      <div className="value" style={{ fontSize: 14 }}>{techStack.ip || "—"}</div>
                    </div>
                    <div className="metric">
                      <div className="label">Reverse DNS</div>
                      <div className="value" style={{ fontSize: 13 }}>{techStack.reverse_dns || "—"}</div>
                    </div>
                    <div className="metric">
                      <div className="label">HTTPS</div>
                      <div className={`value ${techStack.https ? "good" : "bad"}`}>{techStack.https ? "Yes" : "No"}</div>
                    </div>
                  </div>
                  {techStack.detected?.length > 0 && (
                    <div className="card" style={{ marginTop: 16 }}>
                      <table>
                        <thead>
                          <tr><th>Technology</th><th>Category</th></tr>
                        </thead>
                        <tbody>
                          {techStack.detected.map((d: any) => (
                            <tr key={d.name}>
                              <td>{d.name}</td>
                              <td style={{ textTransform: "capitalize" }}>{d.category}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </SectionCard>
          )}
        </div>
      )}

      {/* Multi-page audit — full width, has a wide table */}
      {selectedSections.includes("all_pages") && (
        <SectionCard
          sectionKey="all_pages"
          title="All Pages"
          description="Crawls every page listed in the sitemap and checks title/meta description on each — not just the homepage."
          loading={pageAuditLoading}
          hasData={!!pageAuditResult}
        >
          {pageAuditLoading && pageAuditProgress && (
            <p style={{ fontSize: 13, marginTop: 8, color: "var(--text-muted)" }}>
              Checked {pageAuditProgress.checked}
              {pageAuditProgress.total ? ` / ${pageAuditProgress.total}` : ""} pages...
            </p>
          )}

          {pageAuditResult && (
            <div style={{ marginTop: 20 }}>
              <PageAuditTable result={pageAuditResult} />
            </div>
          )}
        </SectionCard>
      )}

      {/* Semrush uploads — one for our domain, one for competitors */}
      <SemrushImportCard
        clientId={clientId!}
        title="Our Website Data"
        description={`Semrush exports for ${client.website_url} — backlinks, keyword gap, or domain overview. Type is auto-detected; select multiple files to bulk-upload.`}
        isOwnSite={true}
        mcpHint={`Use the Semrush MCP tools to pull backlinks, keyword data, and domain overview data for our own site, ${client.website_url}.`}
        imports={imports}
        onChanged={loadImports}
      />
      <SemrushImportCard
        clientId={clientId!}
        title="Competitor Data"
        description="Semrush exports for competitor domains — backlinks, organic competitors, or keyword gap. Type is auto-detected; select multiple files to bulk-upload."
        isOwnSite={false}
        mcpHint={`Use the Semrush MCP tools to pull competitor data (backlinks, organic competitors, keyword gap) for competitors of ${client.website_url}.`}
        imports={imports}
        onChanged={loadImports}
      />

      <SemrushAnalysis clientId={clientId!} />

      {/* Google Analytics / Search Console */}
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Google Analytics &amp; Search Console</h3>
        {!client.google_connected ? (
          <button onClick={connectGoogle}>Connect Google</button>
        ) : (
          <div>
            <span className="badge success" style={{ marginBottom: 12, display: "inline-block" }}>
              Google connected
            </span>
            <div>
              <button className="secondary" onClick={loadProperties}>
                Load GA4 properties / GSC sites
              </button>
            </div>

            {properties.length > 0 && (
              <div style={{ marginTop: 14 }}>
                <label style={{ fontSize: 13, fontWeight: 600 }}>GA4 Property</label>
                <br />
                <select
                  value={client.ga4_property_id || ""}
                  onChange={(e) => selectProperties(e.target.value, client.gsc_site_url || "")}
                  style={{ marginTop: 4, width: "100%" }}
                >
                  <option value="">-- select --</option>
                  {properties.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.display_name}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {sites.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <label style={{ fontSize: 13, fontWeight: 600 }}>Search Console Site</label>
                <br />
                <select
                  value={client.gsc_site_url || ""}
                  onChange={(e) => selectProperties(client.ga4_property_id || "", e.target.value)}
                  style={{ marginTop: 4, width: "100%" }}
                >
                  <option value="">-- select --</option>
                  {sites.map((s) => (
                    <option key={s.site_url} value={s.site_url}>
                      {s.site_url}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {(client.ga4_property_id || client.gsc_site_url) && (
              <div style={{ marginTop: 16, display: "flex", alignItems: "flex-end", gap: 10, flexWrap: "wrap" }}>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, display: "block" }}>Start date</label>
                  <input
                    type="date"
                    value={analyticsStart}
                    max={analyticsEnd}
                    onChange={(e) => setAnalyticsStart(e.target.value)}
                  />
                </div>
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, display: "block" }}>End date</label>
                  <input
                    type="date"
                    value={analyticsEnd}
                    min={analyticsStart}
                    max={new Date().toISOString().slice(0, 10)}
                    onChange={(e) => setAnalyticsEnd(e.target.value)}
                  />
                </div>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  Included when "Analytics" is checked in Sections and Generate Report is run.
                </span>
              </div>
            )}

            {selectedSections.includes("analytics") && analyticsLoading && (
              <p style={{ fontSize: 13, marginTop: 8 }}>Loading...</p>
            )}

            {selectedSections.includes("analytics") && analyticsResult && (
              <div style={{ marginTop: 20 }}>
                <AnalyticsReport data={analyticsResult} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
