import type { ReactNode } from "react";
import SiteAuditReport from "./SiteAuditReport";
import PageAuditTable from "./PageAuditTable";
import PageSpeedReport from "./PageSpeedReport";
import {
  SectionDateRange,
  SectionTrafficOverview,
  SectionTopPages,
  SectionPagePerformance,
  SectionTrafficSources,
  SectionSearchQueries,
} from "./AnalyticsReport";
import CompanyOverviewEditor from "./CompanyOverviewEditor";
import type { CompanyOverview } from "./CompanyOverviewEditor";
import CompetitorAnalysisEditor from "./CompetitorAnalysisEditor";
import type { CompetitorAnalysis } from "./CompetitorAnalysisEditor";

export interface ReportPreviewData {
  client_name: string;
  website_url: string;
  site_audit: any;
  page_audit: any;
  psi_mobile: any;
  psi_desktop: any;
  analytics: any;
  tech_stack: any;
  competitor_rows: any[] | null;
  keyword_rows: any[] | null;
  backlink_rows: any[] | null;
  company_overview: CompanyOverview | null;
  competitor_analysis: CompetitorAnalysis | null;
}

// Same palette as app/reporting/pptx_builder.py's DEFAULT_ACCENT — this
// preview mirrors the actual PPTX slide-by-slide, so it uses the deck's own
// theme, not the app's. A client with its own extractable brand color
// overrides this in the real PPTX; the preview always shows the default.
const ACCENT = "#FF0000";
const TEXT_DARK = "#1A1A1A";
const TEXT_MUTED = "#6B7280";
const GOOD = "#22A06B";
const WARN = "#E89B2E";
const BAD = "#D13B3B";

// 16:9 slide frame — every slide in the deck is this shape.
function Slide({
  eyebrow,
  title,
  editable,
  dark,
  children,
}: {
  eyebrow?: string;
  title?: string;
  editable?: boolean;
  dark?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      style={{
        aspectRatio: "16 / 9",
        width: "100%",
        background: dark ? ACCENT : "#fff",
        border: "1px solid var(--border)",
        borderRadius: 8,
        boxShadow: "0 2px 10px rgba(0,0,0,0.08)",
        marginBottom: 18,
        padding: "5% 6%",
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        position: "relative",
      }}
    >
      {editable && (
        <span
          style={{
            position: "absolute",
            top: 10,
            right: 12,
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: 0.4,
            color: ACCENT,
            textTransform: "uppercase",
          }}
        >
          Editable
        </span>
      )}
      {eyebrow && (
        <div
          style={{
            fontSize: 12,
            fontWeight: 700,
            letterSpacing: 0.6,
            textTransform: "uppercase",
            color: dark ? "rgba(255,255,255,0.85)" : ACCENT,
            marginBottom: 4,
          }}
        >
          {eyebrow}
        </div>
      )}
      {title && (
        <h3
          style={{
            fontSize: dark ? 30 : 20,
            margin: "0 0 12px",
            color: dark ? "#fff" : TEXT_DARK,
          }}
        >
          {title}
        </h3>
      )}
      <div style={{ flex: 1, minHeight: 0, fontSize: 13, color: TEXT_DARK }}>{children}</div>
    </div>
  );
}

function SectionDividerSlide({ title }: { title: string }) {
  return (
    <Slide dark title={title}>
      <div />
    </Slide>
  );
}

function StatDot({ color, label }: { color: string; label: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
      <span style={{ width: 10, height: 10, borderRadius: "50%", background: color, flexShrink: 0 }} />
      <span style={{ fontSize: 13 }}>{label}</span>
    </div>
  );
}

export default function ReportPreviewModal({
  data,
  companyOverview,
  onCompanyOverviewChange,
  competitorAnalysis,
  onCompetitorAnalysisChange,
  onDownload,
  onClose,
  downloading,
}: {
  data: ReportPreviewData;
  companyOverview: CompanyOverview | null;
  onCompanyOverviewChange: (next: CompanyOverview) => void;
  competitorAnalysis: CompetitorAnalysis | null;
  onCompetitorAnalysisChange: (next: CompetitorAnalysis) => void;
  onDownload: () => void;
  onClose: () => void;
  downloading: boolean;
}) {
  const domain = data.website_url.replace(/^https?:\/\//, "").replace(/\/$/, "");
  const hasSiteContent = data.site_audit || data.page_audit;
  const hasAnalytics = data.analytics;
  const hasCompetitorContent =
    (data.competitor_rows && data.competitor_rows.length > 0) ||
    (data.keyword_rows && data.keyword_rows.length > 0) ||
    (data.backlink_rows && data.backlink_rows.length > 0) ||
    competitorAnalysis;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        zIndex: 100,
        display: "flex",
        justifyContent: "center",
        padding: "24px 20px",
        overflowY: "auto",
      }}
      onClick={onClose}
    >
      <div
        style={{ maxWidth: 880, width: "100%" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={{
            position: "sticky",
            top: 0,
            zIndex: 1,
            background: "var(--bg, #f7f5f0)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "10px 4px",
            marginBottom: 8,
          }}
        >
          <div>
            <h3 style={{ margin: 0 }}>Report Preview</h3>
            <p style={{ margin: "2px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
              {data.client_name} — {domain} · slide-by-slide, in the order they'll appear in the PPTX
            </p>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={onClose} disabled={downloading}>
              Close
            </button>
            <button onClick={onDownload} disabled={downloading}>
              {downloading ? "Generating..." : "Download Report (PPTX)"}
            </button>
          </div>
        </div>

        {/* 1. Title slide */}
        <Slide dark>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", textAlign: "center" }}>
            <div style={{ fontSize: 13, color: "rgba(255,255,255,0.8)", marginBottom: 8 }}>Web and SEO Audit</div>
            <h1 style={{ fontSize: 34, color: "#fff", margin: "0 0 8px" }}>{data.client_name}</h1>
            <div style={{ fontSize: 15, color: "rgba(255,255,255,0.85)" }}>{domain}</div>
          </div>
        </Slide>

        {/* 2. Company overview (editable) */}
        {companyOverview && (
          <Slide eyebrow="Company Overview" title={data.client_name} editable>
            <CompanyOverviewEditor overview={companyOverview} onChange={onCompanyOverviewChange} />
          </Slide>
        )}

        {/* 3. Tech stack */}
        {data.tech_stack && (
          <Slide eyebrow="Technical Foundation" title="Tech Stack & Hosting">
            <pre style={{ fontSize: 12, background: "#f9f6f0", padding: 10, borderRadius: 6, overflowX: "auto" }}>
              {JSON.stringify(data.tech_stack, null, 2)}
            </pre>
          </Slide>
        )}

        {/* 4. Section divider + site health/issues */}
        {hasSiteContent && (
          <>
            <SectionDividerSlide title="Understanding Current Scenario" />
            {data.site_audit && (
              <Slide eyebrow="Site Health" title="Reachability & Crawlability">
                <SiteAuditReport result={data.site_audit} />
              </Slide>
            )}
            {data.page_audit && (
              <Slide eyebrow="Site Health" title="All Pages">
                <PageAuditTable result={data.page_audit} />
              </Slide>
            )}
          </>
        )}

        {/* 5. PageSpeed */}
        {(data.psi_mobile || data.psi_desktop) && (
          <Slide eyebrow="Performance" title="PageSpeed Insights">
            <PageSpeedReport mobile={data.psi_mobile} desktop={data.psi_desktop} />
          </Slide>
        )}

        {/* 6. Section divider + analytics — one slide per pptx_builder.py slide */}
        {hasAnalytics && (
          <>
            <SectionDividerSlide title="Traffic & Search Performance" />
            {data.analytics.traffic_overview && (
              <Slide eyebrow="Analytics" title="Traffic Overview">
                <SectionDateRange data={data.analytics} />
                <SectionTrafficOverview data={data.analytics} />
              </Slide>
            )}
            {data.analytics.top_pages && (
              <Slide eyebrow="Analytics" title="Top Pages">
                <SectionTopPages data={data.analytics} />
              </Slide>
            )}
            {data.analytics.page_performance && (
              <Slide eyebrow="Analytics" title="Top vs. Poor Performing Pages">
                <SectionPagePerformance data={data.analytics} />
              </Slide>
            )}
            {data.analytics.traffic_sources && (
              <Slide eyebrow="Analytics" title="Traffic Sources">
                <SectionTrafficSources data={data.analytics} />
              </Slide>
            )}
            {data.analytics.search_queries && (
              <Slide eyebrow="Search Console" title="Search Queries">
                <SectionSearchQueries data={data.analytics} />
              </Slide>
            )}
          </>
        )}

        {/* 7. Section divider + competitor research */}
        {hasCompetitorContent && (
          <>
            <SectionDividerSlide title="Competitor & Keyword Research" />
            {competitorAnalysis && (
              <Slide eyebrow="Competitive Gaps & Opportunities" title="Where you stand vs. competitors" editable>
                <CompetitorAnalysisEditor analysis={competitorAnalysis} onChange={onCompetitorAnalysisChange} />
              </Slide>
            )}
          </>
        )}

        {/* 8. Closing */}
        <SectionDividerSlide title="Next Steps" />
        <Slide eyebrow="Summary" title="Where to focus next">
          <StatDot color={BAD} label="Errors — fix first, they block search engines or users outright." />
          <StatDot color={WARN} label="Warnings — worth fixing, lower urgency." />
          <StatDot color={GOOD} label="Opportunities — content/keyword gaps to go after." />
          <p style={{ color: TEXT_MUTED, marginTop: 10 }}>Full prioritized list is in the downloaded PPTX.</p>
        </Slide>
      </div>
    </div>
  );
}
