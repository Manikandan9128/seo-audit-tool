import { useEffect, useRef, useState, type ReactNode } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import SiteAuditHistory from "../components/SiteAuditHistory";
import PageAuditHistory from "../components/PageAuditHistory";
import PageSpeedReport from "../components/PageSpeedReport";
import PageAuditTable from "../components/PageAuditTable";
import SchemaValidationPanel from "../components/SchemaValidationPanel";
import AnalyticsReport from "../components/AnalyticsReport";
import CompanyOverviewEditor from "../components/CompanyOverviewEditor";
import type { CompanyOverview } from "../components/CompanyOverviewEditor";
import SemrushImportCard from "../components/SemrushImportCard";
import ManualKeywordClusterCard from "../components/ManualKeywordClusterCard";
import GeoPulseImportCard from "../components/GeoPulseImportCard";
import DomainRatingEditor from "../components/DomainRatingEditor";
import SemrushAnalysis from "../components/SemrushAnalysis";
import { useToast } from "../components/ToastProvider";
import { useReportReadiness } from "../components/ReportReadinessProvider";
import ReportPreviewModal from "../components/ReportPreviewModal";
import SemrushSourceModal from "../components/SemrushSourceModal";
import { SEMRUSH_MCP_ENABLED } from "../features";
import { downloadReadyReport, resumeGenerate, resumeReportJob, startDownload, startGenerate, useReportJobState } from "../reportJobs";
import type { PrepSection } from "../reportJobs";
import type { SemrushSource, SemrushMcpState } from "../components/SemrushSourceModal";
import type { ReportPreviewData } from "../components/ReportPreviewModal";
import type { CompetitorAnalysis } from "../components/CompetitorAnalysisEditor";
import Tip from "../components/Tip";

// Redesign v3 stage 2 — decorative section-row icon anchors, purely for
// scannability (never repeated as a data-encoding color elsewhere).
// Keyed by SectionKey string literal; module-level since it's static.
const SECTION_ICONS: Record<string, ReactNode> = {
  overview: (
    <svg viewBox="0 0 24 24" fill="none"><path d="M3 21h18M6 21V7l6-4 6 4v14M10 21v-6h4v6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
  ),
  site_audit: (
    <svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" /><path d="M21 21l-4.3-4.3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
  ),
  pagespeed: (
    <svg viewBox="0 0 24 24" fill="none"><path d="M12 20a8 8 0 100-16 8 8 0 000 16z" stroke="currentColor" strokeWidth="2" /><path d="M12 12l3-3M8 12a4 4 0 118 0" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
  ),
  tech_stack: (
    <svg viewBox="0 0 24 24" fill="none"><rect x="2" y="3" width="20" height="8" rx="2" stroke="currentColor" strokeWidth="2" /><rect x="2" y="13" width="20" height="8" rx="2" stroke="currentColor" strokeWidth="2" /><circle cx="6" cy="7" r="1" fill="currentColor" /><circle cx="6" cy="17" r="1" fill="currentColor" /></svg>
  ),
  all_pages: (
    <svg viewBox="0 0 24 24" fill="none"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" /><path d="M14 2v6h6M9 13h6M9 17h6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
  ),
};

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

interface DomainRatingSummary {
  id: string;
  domain: string;
  dr: number;
}

function normalizeDomainForKpi(d: string) {
  return (d || "").replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/$/, "").toLowerCase();
}

export default function ClientDetailPage() {
  const { clientId } = useParams();
  const { showToast } = useToast();
  const { readiness, setReadiness } = useReportReadiness();
  const [searchParams] = useSearchParams();
  const missingScopes = searchParams.get("missing_scopes") === "1";
  const [client, setClient] = useState<Client | null>(null);
  const [properties, setProperties] = useState<GA4Property[]>([]);
  const [sites, setSites] = useState<GSCSite[]>([]);
  const [error, setError] = useState("");

  const [auditLoading, setAuditLoading] = useState(false);
  const [auditHistoryKey, setAuditHistoryKey] = useState(0);
  // True when at least one saved site-audit run exists for this client. Fetched
  // here rather than read from SiteAuditHistory, since that only mounts when
  // the (collapsed-by-default) Site Audit card is expanded.
  const [siteAuditHasData, setSiteAuditHasData] = useState(false);
  useEffect(() => {
    if (!clientId) return;
    api
      .get(`/clients/${clientId}/site-audit/history`)
      .then((res) => setSiteAuditHasData(Array.isArray(res.data) && res.data.length > 0))
      .catch(() => setSiteAuditHasData(false));
  }, [clientId, auditHistoryKey]);

  const [psiMobile, setPsiMobile] = useState<any>(null);
  const [psiDesktop, setPsiDesktop] = useState<any>(null);
  const [psiLoading, setPsiLoading] = useState(false);
  // Separate from `error` (the harsh red banner) — this preview check uses
  // a tight 50s/no-retry budget (see backend /pagespeed endpoint comment)
  // and reliably times out on a heavy site even though the real "Generate
  // Report" download has its own 150s x retry PSI call and gets the data
  // fine regardless. A red "request failed" banner here read as a real
  // failure to the user even though nothing was actually broken.
  const [psiPreviewNote, setPsiPreviewNote] = useState("");

  const [pageAuditResult, setPageAuditResult] = useState<any>(null);
  const [pageAuditLoading, setPageAuditLoading] = useState(false);
  const [pageAuditProgress, setPageAuditProgress] = useState<{ checked: number; total: number | null } | null>(null);
  const [pageAuditHistoryKey, setPageAuditHistoryKey] = useState(0);

  const [analyticsResult, setAnalyticsResult] = useState<any>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsStart, setAnalyticsStart] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().slice(0, 10);
  });
  const [analyticsEnd, setAnalyticsEnd] = useState(() => new Date().toISOString().slice(0, 10));

  const [imports, setImports] = useState<SemrushImportSummary[]>([]);
  // KPI snapshot strip (redesign v3 stage 3) reads the same already-
  // existing GET /domain-ratings endpoint DomainRatingEditor calls —
  // duplicate read, no new route, so the strip can show Domain Rating
  // without prop-drilling that component's own internal state.
  const [domainRatings, setDomainRatings] = useState<DomainRatingSummary[]>([]);

  // Generate/Download state lives in reportJobs.ts, not here — this page
  // unmounts on navigation, and local state used to reset the buttons while
  // the work was still running (see that file's header).
  const reportJob = useReportJobState(clientId!);
  const dl = reportJob.download;
  const reportLoading = dl.status === "building" || dl.status === "downloading";
  const reportStatusMsg = dl.stage;
  const reportProgressPct = dl.pct;
  const contentGenerationIssues = dl.issues;
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

  type TabKey = "overview" | "datasources" | "analytics" | "keywordclusters";
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  function goToTab(tab: TabKey) {
    setActiveTab(tab);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

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
  const generating = reportJob.generating;
  // hasGenerated comes from the server's latest Generate Report run, so it
  // survives a refresh; a running or finished PPTX build also counts.
  const hasGenerated = reportJob.hasGenerated || dl.status !== "idle" || !!dl.readyJob;
  const hasDownloaded = dl.downloadedSeq > 0;
  const closePreviewAfterDownload = useRef(false);

  useEffect(() => {
    resumeReportJob(clientId!);
    resumeGenerate(clientId!);
  }, [clientId]);

  useEffect(() => {
    if (dl.error) setError(dl.error);
  }, [dl.errorSeq]);

  useEffect(() => {
    if (reportJob.generate.error) setError(reportJob.generate.error);
  }, [reportJob.generate.errorSeq]);

  // Generate Report runs server-side (report_prep.py); each section's result
  // arrives on the job. Applied once per job + section + status, so polling
  // doesn't overwrite edits, and again after a remount/refresh to restore
  // the section previews this page instance never received.
  const appliedPrepSections = useRef(new Set<string>());
  useEffect(() => {
    const { jobId, sections } = reportJob.generate;
    if (!jobId) return;
    for (const [key, sec] of Object.entries(sections)) {
      const mark = `${jobId}:${key}:${sec.status}`;
      if (appliedPrepSections.current.has(mark)) continue;
      appliedPrepSections.current.add(mark);
      applyPrepSection(key, sec);
    }
  }, [reportJob.generate]);

  useEffect(() => {
    if (dl.downloadedSeq && closePreviewAfterDownload.current) {
      closePreviewAfterDownload.current = false;
      setPreviewData(null);
    }
  }, [dl.downloadedSeq]);

  // "" = default automatic order (Groq, then Gemini, then Claude — see
  // text_ai_client.py). Picking one here pins it first for this report's
  // AI calls; still falls back to the others on failure, same as the
  // default order does. Only keys actually configured in Settings are
  // offered — no point letting someone pick a provider with no key.
  const [preferredProvider, setPreferredProvider] = useState("");
  const [availableProviders, setAvailableProviders] = useState<{ value: string; label: string }[]>([]);

  // Semrush data source for this report: "manual" = the uploaded CSVs,
  // exactly as before; "mcp" = fetched from the connected Semrush account
  // on Generate Report and stored server-side as a snapshot that Preview
  // and Download then reuse. Never combined.
  const [semrushSource, setSemrushSource] = useState<SemrushSource>("manual");
  const [semrushDatabase, setSemrushDatabase] = useState("us");
  const [semrushMcp, setSemrushMcp] = useState<SemrushMcpState>({ status: "idle" });
  const [showSemrushSourceModal, setShowSemrushSourceModal] = useState(false);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(`semrush_source:${clientId}`) || "null");
      if ((saved?.source === "mcp" && SEMRUSH_MCP_ENABLED) || saved?.source === "manual") setSemrushSource(saved.source);
      if (typeof saved?.database === "string") setSemrushDatabase(saved.database);
    } catch {
      // per-browser convenience only
    }
    setSemrushMcp({ status: "idle" });
  }, [clientId]);

  function updateSemrushSource(source: SemrushSource, database = semrushDatabase) {
    setSemrushSource(source);
    setSemrushDatabase(database);
    setSemrushMcp({ status: "idle" });
    try {
      localStorage.setItem(`semrush_source:${clientId}`, JSON.stringify({ source, database }));
    } catch {
      // ignore
    }
  }

  async function fetchSemrushMcpData(database = semrushDatabase) {
    setSemrushMcp({ status: "fetching" });
    try {
      const res = await api.post(`/clients/${clientId}/semrush-mcp/fetch`, { database });
      const snap = res.data.snapshot;
      setSemrushMcp({
        status: "ready",
        message: `${res.data.reused ? "Using Semrush data fetched" : "Fetched Semrush data"} ${new Date(snap.fetched_at * 1000).toLocaleString()} (${snap.database.toUpperCase()}${snap.competitors.length ? `, vs ${snap.competitors.join(", ")}` : ""})${res.data.reused ? "" : ` — ${snap.api_units} API units`}`,
      });
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      if (detail?.code === "not_connected") {
        setSemrushMcp({ status: "not_connected" });
      } else {
        setSemrushMcp({
          status: "error",
          message: (typeof detail === "string" ? detail : detail?.message) || "Couldn't fetch Semrush data — try again.",
        });
      }
    }
  }

  function connectSemrushForReport() {
    try {
      sessionStorage.setItem("semrush_return_to", window.location.pathname);
    } catch {
      // ignore
    }
    window.location.href = "/settings";
  }

  const semrushBody = semrushSource === "mcp" ? { semrush_source: "mcp" } : {};
  const semrushBlocksReport = semrushSource === "mcp" && semrushMcp.status !== "ready";

  useEffect(() => {
    api.get("/settings").then((res) => {
      const opts: { value: string; label: string }[] = [];
      if (res.data.groq_api_key_set) opts.push({ value: "groq", label: "Groq" });
      if (res.data.gemini_api_key_set) opts.push({ value: "gemini", label: "Gemini" });
      if (res.data.claude_api_key_set) opts.push({ value: "claude", label: "Claude" });
      if (res.data.browser_use_api_key_set) opts.push({ value: "browser_use", label: "Browser Use" });
      if (res.data.openrouter_api_key_set) opts.push({ value: "openrouter", label: "OpenRouter" });
      setAvailableProviders(opts);
    }).catch(() => {});
  }, []);

  function toggleSection(key: SectionKey) {
    setSelectedSections((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  }

  // Generate Report opens the Semrush Data Source popup first; its
  // choice is applied here, then the normal generate runs.
  function confirmSemrushSourceAndGenerate(source: SemrushSource, database: string) {
    setShowSemrushSourceModal(false);
    updateSemrushSource(source, database);
    generateSelectedReport(source, database);
  }

  async function generateSelectedReport(source: SemrushSource = semrushSource, database: string = semrushDatabase) {
    if (generating) return;
    // Analytics only runs with a connected Google account, as before.
    const sections = selectedSections.filter((k) => k !== "analytics" || client?.google_connected);
    showToast("Report generation started — this can take a minute.");
    setError("");
    setCollapsedSections((prev) => prev.filter((k) => !selectedSections.includes(k)));
    // Semrush MCP (behind SEMRUSH_MCP_ENABLED, off) still fetches from the page.
    if (source === "mcp") fetchSemrushMcpData(database);
    if (sections.length) {
      await startGenerate(clientId!, { sections, analytics_start: analyticsStart, analytics_end: analyticsEnd });
    }
  }

  function applyOverviewData(data: any, catalogueNames: string[]) {
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
  }

  // One Generate Report section's server-side result, shown the same way
  // the page's own requests used to show it.
  function applyPrepSection(key: string, sec: PrepSection) {
    const busy = sec.status === "pending" || sec.status === "running";
    const failed = sec.status === "failed";
    switch (key) {
      case "overview":
        setOverviewLoading(busy);
        if (busy) setOverviewMsg("");
        if (sec.status === "done") {
          applyOverviewData(sec.data.overview, (sec.data.catalogue?.products || []).map((p: any) => p.name));
        }
        if (failed) setOverviewMsg(sec.error || "Couldn't load company overview");
        break;
      case "site_audit":
        setAuditLoading(busy);
        if (sec.status === "done") setAuditHistoryKey((k) => k + 1);
        if (failed) setError(sec.error || "Site audit failed");
        break;
      case "all_pages":
        // The crawl is its own background job; follow it as before.
        if (busy) {
          setPageAuditLoading(true);
          setPageAuditResult(null);
          setPageAuditProgress(null);
        }
        if (sec.status === "done") pollPageAuditJob(sec.data.job_id);
        if (failed) {
          setError(sec.error || "Page-by-page audit failed");
          setPageAuditLoading(false);
        }
        break;
      case "pagespeed":
        setPsiLoading(busy);
        if (busy) setPsiPreviewNote("");
        if (sec.status === "done") {
          setPsiMobile(sec.data.mobile);
          setPsiDesktop(sec.data.desktop);
        }
        // A timeout here is this quick preview check's own tight budget, not
        // a real failure — the actual report generation has a longer budget
        // with retries and fetches PSI data correctly regardless. Any other
        // failure (e.g. missing API key) is a real problem worth the red
        // banner.
        if (failed && /timed out|timeout/i.test(sec.error || "")) {
          setPsiPreviewNote(
            "PageSpeed preview check timed out — this is common on larger sites and won't affect the downloaded report, which uses a longer timeout with retries."
          );
        } else if (failed) {
          setError(sec.error || "PageSpeed Insights failed");
        }
        break;
      case "tech_stack":
        setTechStackLoading(busy);
        if (busy) setTechStackMsg("");
        if (sec.status === "done") setTechStack(sec.data);
        if (failed) setTechStackMsg(sec.error || "Couldn't detect tech stack");
        break;
      case "analytics":
        setAnalyticsLoading(busy);
        if (sec.status === "done") setAnalyticsResult(sec.data);
        if (failed) setError(sec.error || "Analytics report failed");
        break;
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

  async function loadDomainRatingsForKpi() {
    try {
      const res = await api.get(`/clients/${clientId}/domain-ratings`);
      setDomainRatings(res.data);
    } catch {
      // quiet — same fail-open discipline as DomainRatingEditor's own load(); the KPI tile just shows "—"
    }
  }

  useEffect(() => {
    loadClient();
    loadImports();
    loadDomainRatingsForKpi();
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

  async function pollPageAuditJob(jobId: string) {
    const res = await api.get(`/clients/${clientId}/site-audit-pages/${jobId}`);
    const job = res.data;
    setPageAuditProgress({ checked: job.pages_checked, total: job.pages_total });
    if (job.status === "done") {
      setPageAuditResult(job.result);
      setPageAuditLoading(false);
      setPageAuditHistoryKey((k) => k + 1);
    } else if (job.status === "failed") {
      setError(job.error || "Page-by-page audit failed");
      setPageAuditLoading(false);
    } else {
      setTimeout(() => pollPageAuditJob(jobId), 1500);
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
      applyOverviewData(overviewRes.data, catalogueNames);
    } catch (err: any) {
      setOverviewMsg(err?.response?.data?.detail || "Couldn't load company overview");
    } finally {
      setOverviewLoading(false);
    }
  }

  async function openPreview() {
    setPreviewLoading(true);
    setError("");
    try {
      const body = {
        ...(overview ? { company_overview_override: overview } : {}),
        ...semrushBody,
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

  function downloadReportWithBody(body: any, closePreviewAfter: boolean) {
    if (reportLoading) return;
    setError("");
    closePreviewAfterDownload.current = closePreviewAfter;
    startDownload(clientId!, body);
  }

  function downloadReportDirect() {
    const body = {
      ...(overview ? { company_overview_override: overview } : {}),
      ...(preferredProvider ? { preferred_provider: preferredProvider } : {}),
      ...semrushBody,
    };
    downloadReportWithBody(Object.keys(body).length ? body : null, false);
  }

  function downloadReportFromPreview() {
    const body = {
      company_overview_override: previewOverview,
      competitor_analysis_override: previewCompetitorAnalysis,
      ...(preferredProvider ? { preferred_provider: preferredProvider } : {}),
      ...semrushBody,
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
        className="card card-interactive section-card"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 4,
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
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              style={{
                display: "inline-block",
                transition: "transform 0.15s ease",
                transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)",
                color: "var(--muted)",
                fontSize: 12,
              }}
            >
              ▾
            </span>
            {SECTION_ICONS[sectionKey] && (
              <span className={`section-icon${hasData ? " ready" : ""}`}>{SECTION_ICONS[sectionKey]}</span>
            )}
            <h3 style={{ margin: 0, fontSize: 19, fontFamily: "var(--font-display)", fontWeight: 600 }}>{title}</h3>
          </div>
          <span className={`badge ${status.cls}`}>{status.label}</span>
        </div>
        {/* Always visible, even collapsed (Cyces editorial spec, NUE §7) —
            a one-line orientation for what this section covers shouldn't
            need an expand click to read. */}
        <p style={{ color: "var(--muted)", fontSize: 12.5, margin: "2px 0 0" }}>{description}</p>
        {!collapsed && (
          <>
            {!loading && !hasData && (
              <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 10, fontStyle: "italic" }}>
                Not generated yet — check this section and click Generate Report.
              </p>
            )}
            {children}
          </>
        )}
      </div>
    );
  }

  // Sidebar "Report readiness" (point 7) — same hasData signal each
  // SectionCard/Analytics block below already uses, just totalled up.
  // Fixed 6-section denominator (SECTION_OPTIONS.length), independent of
  // which are currently checked, so the count doesn't jump around as the
  // Sections dropdown selection changes.
  useEffect(() => {
    setReadiness({
      ready: [siteAuditHasData, !!overview, !!psiMobile, !!techStack, !!pageAuditResult, !!analyticsResult].filter(Boolean).length,
      total: SECTION_OPTIONS.length,
    });
    return () => setReadiness(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [siteAuditHasData, overview, psiMobile, techStack, pageAuditResult, analyticsResult]);

  if (!client) return <p>Loading...</p>;

  return (
    <div style={{ maxWidth: 1120, margin: "0 auto", display: "flex", flexDirection: "column", gap: "var(--sp-5)" }}>
      <div
        style={{
          position: "sticky",
          top: 0,
          zIndex: 30,
          background: "var(--bg)",
          margin: "-32px -24px 0 -24px",
          padding: "24px 24px 0 24px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 24 }}>
          <div>
            <p className="eyebrow" style={{ margin: "0 0 4px" }}>
              Client
            </p>
            <h2 style={{ margin: 0, fontSize: 40 }}>{client.name}</h2>
            <a
              href={client.website_url}
              target="_blank"
              rel="noreferrer"
              style={{ color: "var(--accent-deep)", margin: "4px 0 0", fontSize: 13.5, display: "inline-block" }}
            >
              {client.website_url.replace(/^https?:\/\//, "")}
            </a>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "flex-start", position: "relative", flexWrap: "wrap" }}>
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
            {availableProviders.length > 1 && (
              <select
                value={preferredProvider}
                onChange={(e) => setPreferredProvider(e.target.value)}
                title="AI provider to try first for this report's AI sections (Company Overview, Core Problem, competitor narratives, Next Steps) — still falls back to the others on failure"
                style={{ marginRight: 8 }}
              >
                <option value="">Auto (default order)</option>
                {availableProviders.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label} first
                  </option>
                ))}
              </select>
            )}
            <button
              className="btn btn-primary"
              onClick={() => (SEMRUSH_MCP_ENABLED ? setShowSemrushSourceModal(true) : generateSelectedReport("manual"))}
              disabled={generating || selectedSections.length === 0}
            >
              {generating ? "Generating..." : "Generate Report"}
            </button>
            {showSemrushSourceModal && (
              <SemrushSourceModal
                clientId={clientId!}
                imports={imports || []}
                initialSource={semrushSource}
                initialDatabase={semrushDatabase}
                onClose={() => setShowSemrushSourceModal(false)}
                onConfirm={confirmSemrushSourceAndGenerate}
                onConnectSemrush={connectSemrushForReport}
              />
            )}
            {SEMRUSH_MCP_ENABLED && !generating && hasGenerated && (
              <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>
                Semrush: {semrushSource === "mcp" ? `MCP (${semrushDatabase.toUpperCase()})` : "Manual Upload"}
              </span>
            )}
            {semrushSource === "mcp" && semrushMcp.status !== "idle" && (
              <div style={{ fontSize: 12, maxWidth: 360, alignSelf: "center" }}>
                {semrushMcp.status === "fetching" && <span className="muted">Fetching Semrush data via MCP…</span>}
                {semrushMcp.status === "ready" && <span style={{ color: "var(--success)" }}>✓ {semrushMcp.message}</span>}
                {semrushMcp.status === "not_connected" && (
                  <span>
                    Connect your Semrush account to continue.{" "}
                    <button className="btn btn-secondary" onClick={connectSemrushForReport} style={{ marginLeft: 6 }}>
                      Connect Semrush
                    </button>
                  </span>
                )}
                {semrushMcp.status === "error" && (
                  <span style={{ color: "#991b1b" }}>
                    {semrushMcp.message}{" "}
                    <button className="btn btn-secondary" onClick={() => fetchSemrushMcpData()} style={{ marginLeft: 6 }}>
                      Retry
                    </button>
                  </span>
                )}
              </div>
            )}
            {hasGenerated && (
              <>
                <button className="btn btn-secondary" onClick={openPreview} disabled={previewLoading || semrushBlocksReport}>
                  {previewLoading ? "Loading..." : "Preview Report"}
                </button>
                <button className="btn btn-secondary" onClick={downloadReportDirect} disabled={reportLoading || semrushBlocksReport}>
                  {reportLoading ? "Downloading..." : "Download Report (PPTX)"}
                </button>
                {!reportLoading && dl.readyJob && (
                  <button
                    className="btn btn-secondary"
                    onClick={() => downloadReadyReport(clientId!)}
                    title="Downloads the report already built, without building it again"
                  >
                    Download last report ({new Date(dl.readyJob.createdAt).toLocaleString()})
                  </button>
                )}
                {reportLoading && reportStatusMsg && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 220 }}>
                    <span className="muted" style={{ fontSize: 12 }}>
                      {reportStatusMsg}
                      {typeof reportProgressPct === "number" ? ` — ${reportProgressPct}%` : ""}
                    </span>
                    {typeof reportProgressPct === "number" && (
                      <div style={{ height: 6, borderRadius: 3, background: "#e5e7eb", overflow: "hidden" }}>
                        <div
                          style={{
                            height: "100%",
                            width: `${reportProgressPct}%`,
                            background: "#2563eb",
                            transition: "width 0.3s ease",
                          }}
                        />
                      </div>
                    )}
                  </div>
                )}
                {!reportLoading && contentGenerationIssues && (
                  <div
                    style={{
                      fontSize: 12,
                      color: "#92400e",
                      background: "#fef3c7",
                      border: "1px solid #fde68a",
                      borderRadius: 6,
                      padding: "8px 10px",
                      maxWidth: 420,
                    }}
                  >
                    <strong>Heads up:</strong> {contentGenerationIssues.length} section(s) didn't generate this run
                    (shown below, not in the downloaded file) — usually a temporary AI rate limit. Regenerating often
                    fixes it.
                    <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                      {contentGenerationIssues.map((issue, i) => (
                        <li key={i}>{issue}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
        <div className="tabs" role="tablist" aria-label="Client sections">
          <button role="tab" aria-selected={activeTab === "overview"} className={`tab ${activeTab === "overview" ? "active" : ""}`} onClick={() => setActiveTab("overview")}>
            <span className="tab-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="7" height="9" rx="1.5" stroke="currentColor" strokeWidth="2" /><rect x="14" y="3" width="7" height="5" rx="1.5" stroke="currentColor" strokeWidth="2" /><rect x="14" y="12" width="7" height="9" rx="1.5" stroke="currentColor" strokeWidth="2" /><rect x="3" y="16" width="7" height="5" rx="1.5" stroke="currentColor" strokeWidth="2" /></svg>
            </span>
            Overview
          </button>
          <button role="tab" aria-selected={activeTab === "datasources"} className={`tab ${activeTab === "datasources" ? "active" : ""}`} onClick={() => setActiveTab("datasources")}>
            <span className="tab-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="5" rx="8" ry="3" stroke="currentColor" strokeWidth="2" /><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" stroke="currentColor" strokeWidth="2" /></svg>
            </span>
            Data Sources <span className="tab-count">{imports.length} file{imports.length === 1 ? "" : "s"}</span>
          </button>
          <button role="tab" aria-selected={activeTab === "analytics"} className={`tab ${activeTab === "analytics" ? "active" : ""}`} onClick={() => setActiveTab("analytics")}>
            <span className="tab-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none"><path d="M3 3v18h18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /><path d="M7 15l4-4 3 3 5-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </span>
            Analytics
          </button>
          <button role="tab" aria-selected={activeTab === "keywordclusters"} className={`tab ${activeTab === "keywordclusters" ? "active" : ""}`} onClick={() => setActiveTab("keywordclusters")}>
            <span className="tab-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none"><circle cx="6" cy="7" r="3" stroke="currentColor" strokeWidth="2" /><circle cx="18" cy="7" r="3" stroke="currentColor" strokeWidth="2" /><circle cx="12" cy="17" r="3" stroke="currentColor" strokeWidth="2" /><path d="M8.5 8.8l2 5.4M15.5 8.8l-2 5.4M9 7h6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
            </span>
            Keyword Clusters
          </button>
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

      <section className={`panel ${activeTab === "overview" ? "active" : ""}`} style={{ display: activeTab === "overview" ? "flex" : "none", flexDirection: "column", gap: "var(--card-stack-gap)" }}>
      {(() => {
        // KPI snapshot strip (redesign v3 stage 3) — every number here
        // is derived from state this page already loads (domainRatings,
        // imports, readiness), never invented or hardcoded.
        const ownNorm = client.website_url ? normalizeDomainForKpi(client.website_url) : null;
        const ownDrRow = ownNorm ? domainRatings.find((r) => normalizeDomainForKpi(r.domain) === ownNorm) : undefined;
        const competitorDrRows = domainRatings.filter((r) => normalizeDomainForKpi(r.domain) !== ownNorm);
        const avgCompetitorDr = competitorDrRows.length
          ? Math.round(competitorDrRows.reduce((s, r) => s + r.dr, 0) / competitorDrRows.length)
          : null;
        const topCompetitorDr = competitorDrRows.length
          ? competitorDrRows.reduce((a, b) => (b.dr > a.dr ? b : a))
          : null;
        // Own-site only (matches the PPTX report's Backlink Profile slide) —
        // without this filter, competitor backlink exports got summed in
        // too, so "Backlinks tracked" silently included every uploaded
        // domain's backlinks, not just the client's own.
        const backlinkImports = imports.filter((i) => i.import_type === "backlinks" && i.is_own_site);
        const backlinksTracked = backlinkImports.reduce((s, i) => s + (i.row_count || 0), 0);
        const competitorsTrackedCount = new Set(
          imports.filter((i) => !i.is_own_site && i.domain_label).map((i) => i.domain_label)
        ).size;
        const readyCount = readiness?.ready ?? 0;
        const readyTotal = readiness?.total || 1;

        return (
          <div className="kpi-row">
            <div className="kpi-tile kpi-primary">
              <div className="kpi-top">
                <div className="kpi-icon">
                  <svg viewBox="0 0 24 24" fill="none"><path d="M3 3v18h18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /><rect x="7" y="12" width="3" height="6" rx="1" fill="currentColor" /><rect x="12" y="8" width="3" height="10" rx="1" fill="currentColor" /><rect x="17" y="5" width="3" height="13" rx="1" fill="currentColor" /></svg>
                </div>
                <Tip text="A 0-100 score estimating how strong this domain's overall backlink profile is compared to others.">
                  <span className="kpi-label">Domain Rating</span>
                </Tip>
              </div>
              <div className="kpi-value">{ownDrRow ? ownDrRow.dr : "—"}</div>
              <div className="kpi-sub">
                <span>{avgCompetitorDr !== null ? `vs. avg. competitor DR ${avgCompetitorDr}` : "no competitor DR entered yet"}</span>
              </div>
              {ownDrRow && (
                <div className="kpi-bar-track">
                  <div className="kpi-bar-fill" style={{ width: `${Math.min(ownDrRow.dr, 100)}%`, background: "var(--gradient-primary-h)" }} />
                </div>
              )}
            </div>

            <div className="kpi-tile kpi-teal">
              <div className="kpi-top">
                <div className="kpi-icon">
                  <svg viewBox="0 0 24 24" fill="none"><path d="M10 13a5 5 0 007.07 0l2.83-2.83a5 5 0 00-7.07-7.07L11.5 4.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /><path d="M14 11a5 5 0 00-7.07 0L4.1 13.83a5 5 0 007.07 7.07l1.4-1.4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </div>
                <Tip text="Links from other websites pointing to this one — a core signal search engines use to judge authority.">
                  <span className="kpi-label">Backlinks tracked</span>
                </Tip>
              </div>
              <div className="kpi-value">{backlinksTracked.toLocaleString()}</div>
              <div className="kpi-sub">
                <span>
                  {backlinkImports.length
                    ? `across ${backlinkImports.length} exported file${backlinkImports.length === 1 ? "" : "s"}`
                    : "no backlink export uploaded yet"}
                </span>
              </div>
            </div>

            <div className="kpi-tile kpi-violet">
              <div className="kpi-top">
                <div className="kpi-icon">
                  <svg viewBox="0 0 24 24" fill="none"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /><circle cx="9" cy="7" r="4" stroke="currentColor" strokeWidth="2" /><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </div>
                <Tip text="Other domains you're comparing this site against for rankings, backlinks, and keyword coverage.">
                  <span className="kpi-label">Competitors tracked</span>
                </Tip>
              </div>
              <div className="kpi-value">{competitorsTrackedCount}</div>
              <div className="kpi-sub">
                <span>
                  {topCompetitorDr ? `${topCompetitorDr.domain} leads at DR ${topCompetitorDr.dr}` : "no competitor DR entered yet"}
                </span>
              </div>
            </div>

            <div className="kpi-tile kpi-success">
              <div className="kpi-top">
                <div className="kpi-icon">
                  <svg viewBox="0 0 24 24" fill="none"><path d="M20 6L9 17l-5-5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </div>
                <span className="kpi-label">Sections ready</span>
              </div>
              <div className="kpi-value">
                {readyCount} <span className="kpi-value-of">/ {readiness?.total ?? SECTION_OPTIONS.length}</span>
              </div>
              <div className="kpi-bar-track">
                <div className="kpi-bar-fill" style={{ width: `${(readyCount / readyTotal) * 100}%`, background: "var(--gradient-success-h)" }} />
              </div>
            </div>
          </div>
        );
      })()}
      {(() => {
        const steps = [
          {
            n: 1,
            label: "Connect Google (GA4 / Search Console)",
            time: "1 min",
            done: client.google_connected && !!(client.ga4_property_id || client.gsc_site_url),
            onClick: () => goToTab("analytics"),
          },
          {
            n: 2,
            label: "Upload Semrush data",
            time: "3 min",
            done: imports.length > 0,
            onClick: () => goToTab("datasources"),
          },
          {
            n: 3,
            label: "Pick sections & Generate Report",
            time: "2 min",
            done: hasGenerated,
            onClick: () => window.scrollTo({ top: 0, behavior: "smooth" }),
          },
          {
            n: 4,
            label: "Preview & Download",
            time: "1 min",
            done: hasDownloaded,
            onClick: () => window.scrollTo({ top: 0, behavior: "smooth" }),
          },
        ];
        const doneCount = steps.filter((s) => s.done).length;
        const currentIndex = steps.findIndex((s) => !s.done);
        return (
          <div className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div className="card-title-row" style={{ alignItems: "center", justifyContent: "space-between", marginBottom: 0 }}>
              <div className="card-title-row" style={{ marginBottom: 0 }}>
                <span className="card-icon">
                  <svg viewBox="0 0 24 24" fill="none"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 00-2.91-.09z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /><path d="M12 15l-3-3a22 22 0 0110-10c2 2 2 5 0 8a22 22 0 01-7 5z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </span>
                <span className="card-title-text" style={{ paddingTop: 5 }}>
                  <span className="card-title">Getting started</span>
                </span>
              </div>
              <span className={doneCount === steps.length ? "badge-ready" : "badge-pending"}>
                {doneCount} of {steps.length} done
              </span>
            </div>
            <div style={{ height: 2, borderRadius: 0, background: "var(--rule)", overflow: "hidden" }}>
              <div
                style={{
                  height: "100%",
                  width: `${(doneCount / steps.length) * 100}%`,
                  background: "var(--ok)",
                  transition: "width 0.15s ease",
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
                {steps.map((step, i) => {
                  const isCurrent = i === currentIndex;
                  return (
                    <div
                      key={step.n}
                      style={{
                        gridColumn: i + 1,
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
                          fontFamily: "var(--font-display)",
                          fontSize: 12,
                          fontWeight: 600,
                          color: step.done ? "#fff" : isCurrent ? "var(--accent-deep)" : "var(--muted)",
                          background: step.done ? "var(--ok)" : isCurrent ? "var(--accent-wash)" : "transparent",
                          border: step.done ? "none" : isCurrent ? "2px solid var(--accent)" : "1px solid var(--rule-strong)",
                          boxShadow: isCurrent ? "0 0 0 4px var(--accent-misty)" : "none",
                          zIndex: 1,
                        }}
                      >
                        {step.done ? "✓" : step.n}
                      </span>
                    </div>
                  );
                })}
                {steps.map((step, i) => {
                  const isCurrent = i === currentIndex;
                  return (
                    <button
                      key={step.n}
                      className="step-btn"
                      onClick={step.onClick}
                      style={{
                        gridColumn: i + 1,
                        gridRow: 2,
                        marginTop: 8,
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "center",
                        textAlign: "center",
                        background: isCurrent ? "var(--accent-wash)" : "none",
                        border: "none",
                        borderRadius: "var(--radius-sm)",
                        padding: "6px 8px",
                        cursor: "pointer",
                      }}
                    >
                      <span style={{ fontSize: 13.5, fontWeight: 600, color: step.done ? "var(--muted)" : "var(--ink)" }}>
                        {step.label}
                      </span>
                      <span
                        style={{
                          fontSize: 10.5,
                          fontWeight: 500,
                          textTransform: "uppercase",
                          letterSpacing: "0.1em",
                          color: step.done ? "var(--muted)" : isCurrent ? "var(--accent-deep)" : "var(--muted)",
                          marginTop: 4,
                        }}
                      >
                        {step.done ? "Done" : isCurrent ? `Next up · about ${step.time}` : `About ${step.time}`}
                      </span>
                    </button>
                  );
                })}
                {steps.map((step, i) =>
                  i === currentIndex ? (
                    <button
                      key={`start-${step.n}`}
                      className="btn btn-primary"
                      onClick={step.onClick}
                      style={{
                        gridColumn: i + 1,
                        gridRow: 3,
                        justifySelf: "center",
                        marginTop: 6,
                        padding: "3px 12px",
                        fontSize: 11,
                      }}
                    >
                      Start this step
                    </button>
                  ) : null
                )}
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

      {(selectedSections.includes("overview") ||
        selectedSections.includes("site_audit") ||
        selectedSections.includes("pagespeed") ||
        selectedSections.includes("tech_stack")) && (
        <div>
        <div className="section-group-title">Report sections</div>
        <div className="stack-sections">
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
              hasData={siteAuditHasData}
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
              {!psiLoading && psiPreviewNote && (
                <div
                  style={{
                    fontSize: 12, color: "#92400e", background: "#fef3c7", border: "1px solid #fde68a",
                    borderRadius: 6, padding: "8px 10px", marginTop: 12, maxWidth: 480,
                  }}
                >
                  {psiPreviewNote}
                </div>
              )}
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
          <PageAuditHistory clientId={clientId!} refreshKey={pageAuditHistoryKey} onSelect={setPageAuditResult} />

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
      </section>

      <section className={`panel ${activeTab === "datasources" ? "active" : ""}`} style={{ display: activeTab === "datasources" ? "flex" : "none", flexDirection: "column", gap: "var(--card-stack-gap)" }}>
      {/* Semrush uploads — one for our domain, one for competitors */}
      <div id="semrush-section" className="stack-cards">
        <DomainRatingEditor clientId={clientId!} ownDomain={client.website_url} onChanged={loadDomainRatingsForKpi} />
        {/* Upload checklist matrix cut 2026-09-22 per user request — "will
            share better idea for this later." Component kept in
            ../components/SemrushChecklist.tsx for fast re-enable. */}
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
        <GeoPulseImportCard clientId={clientId!} imports={imports} onChanged={loadImports} />
      </div>
      </section>

      <section className={`panel ${activeTab === "keywordclusters" ? "active" : ""}`} style={{ display: activeTab === "keywordclusters" ? "flex" : "none", flexDirection: "column", gap: "var(--card-stack-gap)" }}>
        <ManualKeywordClusterCard clientId={clientId!} imports={imports} onChanged={loadImports} />
      </section>

      <section className={`panel ${activeTab === "analytics" ? "active" : ""}`} style={{ display: activeTab === "analytics" ? "flex" : "none", flexDirection: "column", gap: "var(--card-stack-gap)" }}>
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
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="secondary" onClick={loadProperties}>
                Load GA4 properties / GSC sites
              </button>
              {/* google_connected only ever flips true on first connect and
                  never resets, so a later revoked/expired refresh token
                  (invalid_grant) left no way back to the OAuth flow — the
                  "Connect Google" button above only shows pre-connect.
                  Always-visible reconnect fixes that dead end. */}
              <button className="secondary" onClick={connectGoogle}>
                Reconnect Google
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
      </section>
    </div>
  );
}
