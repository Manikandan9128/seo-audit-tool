import { useEffect, useState, type ReactNode } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import SiteAuditHistory from "../components/SiteAuditHistory";
import PageSpeedReport from "../components/PageSpeedReport";
import PageAuditTable from "../components/PageAuditTable";
import SchemaValidationPanel from "../components/SchemaValidationPanel";
import AnalyticsReport from "../components/AnalyticsReport";
import CompanyOverviewEditor from "../components/CompanyOverviewEditor";
import type { CompanyOverview } from "../components/CompanyOverviewEditor";
import SemrushImportCard from "../components/SemrushImportCard";
import SemrushChecklist from "../components/SemrushChecklist";
import DomainRatingEditor from "../components/DomainRatingEditor";
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
  const [reportStatusMsg, setReportStatusMsg] = useState("");
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
  const [hasDownloaded, setHasDownloaded] = useState(false);

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

  async function loadOverview(force = false) {
    setOverviewLoading(true);
    setOverviewMsg("");
    try {
      const [overviewRes, catalogueRes] = await Promise.all([
        api.get(`/clients/${clientId}/company-overview`, force ? { params: { force: true } } : undefined),
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
    setReportStatusMsg("Starting report build…");
    setError("");
    try {
      const startRes = await api.post(`/clients/${clientId}/generate-report/start`, body);
      const jobId = startRes.data.job_id;
      await pollGenerateReportJob(jobId, closePreviewAfter);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Report generation failed");
      setReportLoading(false);
      setReportStatusMsg("");
    }
  }

  async function pollGenerateReportJob(jobId: string, closePreviewAfter: boolean) {
    const res = await api.get(`/clients/${clientId}/generate-report/${jobId}`);
    const job = res.data;
    if (job.status === "done") {
      setReportStatusMsg("Downloading…");
      try {
        const fileRes = await api.get(`/clients/${clientId}/generate-report/${jobId}/download`, { responseType: "blob" });
        const url = window.URL.createObjectURL(new Blob([fileRes.data]));
        const link = document.createElement("a");
        link.href = url;
        link.setAttribute("download", `${client?.name || "client"}-seo-audit.pptx`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
        setHasDownloaded(true);
        if (closePreviewAfter) setPreviewData(null);
      } catch (err: any) {
        setError(err?.response?.data?.detail || "Report download failed");
      } finally {
        setReportLoading(false);
        setReportStatusMsg("");
      }
    } else if (job.status === "failed") {
      setError(job.error || "Report generation failed");
      setReportLoading(false);
      setReportStatusMsg("");
    } else {
      setReportStatusMsg(
        job.status === "running" ? "Building report… PageSpeed/AI steps can take a couple minutes" : "Queued…"
      );
      setTimeout(() => pollGenerateReportJob(jobId, closePreviewAfter), 2000);
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

  const [collapsedSections, setCollapsedSections] = useState<SectionKey[]>(
    SECTION_OPTIONS.map((s) => s.key)
  );

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
              {reportLoading && reportStatusMsg && (
                <span className="muted" style={{ fontSize: 12 }}>{reportStatusMsg}</span>
              )}
            </>
          )}
        </div>
      </div>

      {(() => {
        const steps = [
          {
            n: 1,
            label: "Connect Google (GA4 / Search Console)",
            hint: "Optional — enables the Analytics section",
            done: client.google_connected && !!(client.ga4_property_id || client.gsc_site_url),
            onClick: () => document.getElementById("google-section")?.scrollIntoView({ behavior: "smooth", block: "start" }),
          },
          {
            n: 2,
            label: "Upload Semrush data",
            hint: "Optional — competitor & keyword slides",
            done: imports.length > 0,
            onClick: () => document.getElementById("semrush-section")?.scrollIntoView({ behavior: "smooth", block: "start" }),
          },
          {
            n: 3,
            label: "Pick sections & Generate Report",
            hint: "Runs every checked section above",
            done: hasGenerated,
            onClick: () => window.scrollTo({ top: 0, behavior: "smooth" }),
          },
          {
            n: 4,
            label: "Preview & Download",
            hint: "Review, then export the PPTX",
            done: hasDownloaded,
            onClick: () => window.scrollTo({ top: 0, behavior: "smooth" }),
          },
        ];
        const doneCount = steps.filter((s) => s.done).length;
        return (
          <div className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
              <p className="eyebrow" style={{ margin: 0 }}>Getting started</p>
              <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-muted)" }}>
                {doneCount} of {steps.length} done
              </span>
            </div>
            <div style={{ height: 4, borderRadius: 2, background: "var(--border-strong)", overflow: "hidden" }}>
              <div
                style={{
                  height: "100%",
                  width: `${(doneCount / steps.length) * 100}%`,
                  background: "var(--success)",
                  transition: "width 0.3s ease",
                }}
              />
            </div>
            <div style={{ position: "relative", overflowX: "auto", paddingBottom: 4 }}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: `repeat(${steps.length}, minmax(140px, 1fr))`,
                  minWidth: 560,
                }}
              >
                <div
                  style={{
                    gridColumn: `1 / ${steps.length + 1}`,
                    gridRow: 1,
                    display: "flex",
                    alignItems: "center",
                    height: 24,
                    padding: "0 12px",
                  }}
                  aria-hidden
                >
                  {steps.slice(1).map((step, i) => (
                    <div
                      key={step.n}
                      style={{
                        flex: 1,
                        height: 2,
                        marginLeft: 12,
                        marginRight: 12,
                        background: steps[i].done ? "var(--success)" : "var(--border-strong)",
                      }}
                    />
                  ))}
                </div>
                {steps.map((step) => (
                  <div
                    key={step.n}
                    style={{
                      gridRow: 1,
                      display: "flex",
                      justifyContent: "center",
                    }}
                  >
                    <span
                      style={{
                        flexShrink: 0,
                        width: 24,
                        height: 24,
                        borderRadius: "50%",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: 12,
                        fontWeight: 700,
                        color: step.done ? "#fff" : "var(--text-muted)",
                        background: step.done ? "var(--success)" : "var(--border-strong)",
                        zIndex: 1,
                      }}
                    >
                      {step.done ? "✓" : step.n}
                    </span>
                  </div>
                ))}
                {steps.map((step) => (
                  <button
                    key={step.n}
                    onClick={step.onClick}
                    style={{
                      gridRow: 2,
                      marginTop: 8,
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      textAlign: "center",
                      background: "none",
                      border: "none",
                      padding: "0 8px",
                      cursor: "pointer",
                    }}
                  >
                    <span style={{ fontSize: 13.5, fontWeight: 600, color: step.done ? "var(--text-muted)" : "inherit" }}>
                      {step.label}
                    </span>
                    <span style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>{step.hint}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        );
      })()}

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
          clientId={clientId}
          gscConnected={!!client.gsc_site_url}
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
              {!overview && overviewMsg && !overviewLoading && (
                <button
                  className="secondary"
                  onClick={(e) => {
                    e.stopPropagation();
                    loadOverview(true);
                  }}
                >
                  Retry
                </button>
              )}
              {overview && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
                    <button
                      className="secondary"
                      disabled={overviewLoading}
                      onClick={(e) => {
                        e.stopPropagation();
                        loadOverview(true);
                      }}
                      title="Cached after the first extraction — re-crawls the site and calls Gemini/Claude again, only if the site's content has actually changed."
                    >
                      {overviewLoading ? "Refreshing..." : "Refresh from site"}
                    </button>
                  </div>
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
                <SiteAuditHistory clientId={clientId!} refreshKey={auditHistoryKey} gscConnected={!!client.gsc_site_url} />
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
              <SchemaValidationPanel pages={pageAuditResult.pages || []} />
            </div>
          )}
        </SectionCard>
      )}

      {/* Semrush uploads — one for our domain, one for competitors */}
      <div id="semrush-section" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        <DomainRatingEditor clientId={clientId!} />
        <SemrushChecklist imports={imports} />
        <SemrushImportCard
          clientId={clientId!}
          title="Our Website Data"
          description={`Semrush exports for ${client.website_url} — backlinks, keyword gap, domain overview, a Backlink List PDF, an Overview Trend CSV exported with Database set to Worldwide (Domain Overview alone is always a single country), or a Site Audit "Pages > Structured Data" CSV (schema markup coverage, e.g. missing FAQ/Product schema). DR comes from manual entry above, not Semrush. Type is auto-detected; select multiple files to bulk-upload.`}
          isOwnSite={true}
          mcpHint={`Use the Semrush MCP tools to pull backlinks, keyword data, and domain overview data for our own site, ${client.website_url}.`}
          imports={imports}
          onChanged={loadImports}
        />
        <SemrushImportCard
          clientId={clientId!}
          title="Competitor Data"
          description="Semrush exports for competitor domains — backlinks, organic competitors, domain overview, a Backlink List PDF, or an Overview Trend CSV with Database set to Worldwide (fills in that competitor's Worldwide traffic/keywords columns). DR comes from the manual Domain Rating entry above, not from these files. Keyword Gap does NOT go here — it's a single combined file comparing your domain and competitors together, upload it once under 'Our Website Data' instead. Type is auto-detected; select multiple files to bulk-upload, enter the competitor's domain first."
          isOwnSite={false}
          mcpHint={`Use the Semrush MCP tools to pull competitor data (backlinks, organic competitors) for competitors of ${client.website_url}.`}
          imports={imports}
          onChanged={loadImports}
        />
      </div>

      <SemrushAnalysis clientId={clientId!} />

      {/* Google Analytics / Search Console */}
      <div id="google-section" className="card">
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
