interface SemrushImportSummary {
  import_type: string;
  is_own_site?: boolean;
  domain_label?: string | null;
}

interface ChecklistItem {
  type: string;
  label: string;
  altType?: string;
}

// What the report pipeline actually reads per domain (see backend
// site_audit.py _gather_report_data) — kept in sync with that, not a
// hard requirement, purely a reminder so an upload round doesn't quietly
// skip a file type for one domain.
const OWN_SITE_TYPES: ChecklistItem[] = [
  { type: "domain_overview", label: "Domain Overview" },
  { type: "backlink_summary", label: "Backlink List (DR)" },
  { type: "overview_trend", label: "Overview Trend (Global)" },
  { type: "keyword_gap", label: "Target Keywords" },
  { type: "site_audit_overview", label: "Site Audit Overview" },
  { type: "site_audit_issues", label: "Site Audit Issues" },
  { type: "structured_data", label: "Structured Data" },
];

const COMPETITOR_TYPES: ChecklistItem[] = [
  { type: "domain_overview", label: "Domain Overview", altType: "organic_competitors" },
  { type: "backlink_summary", label: "Backlink List (DR)" },
  { type: "overview_trend", label: "Overview Trend (Global)" },
  { type: "organic_positions", label: "Keyword Positions" },
];

function ChecklistTable({
  rows,
  columns,
}: {
  rows: { label: string; types: Set<string>; bold?: boolean }[];
  columns: ChecklistItem[];
}) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ fontSize: 12.5 }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>Domain</th>
            {columns.map((c) => (
              <th key={c.type}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <td style={{ textAlign: "left", fontWeight: row.bold ? 600 : 400 }}>{row.label}</td>
              {columns.map((item) => {
                const has = row.types.has(item.type) || (item.altType ? row.types.has(item.altType) : false);
                return (
                  <td key={item.type} title={item.label} style={{ textAlign: "center" }}>
                    {has ? <span style={{ color: "#1f9d66" }}>✓</span> : <span style={{ color: "#c9ccd1" }}>—</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SemrushChecklist({ imports }: { imports: SemrushImportSummary[] }) {
  if (imports.length === 0) return null;

  const ownTypes = new Set(imports.filter((i) => (i.is_own_site ?? true)).map((i) => i.import_type));
  const competitorDomains = Array.from(
    new Set(
      imports
        .filter((i) => !(i.is_own_site ?? true) && i.domain_label)
        .map((i) => i.domain_label as string)
    )
  );

  return (
    <div className="card" style={{ marginBottom: 4 }}>
      <h4 style={{ marginTop: 0, marginBottom: 8, fontSize: 14 }}>Upload checklist</h4>
      <p style={{ color: "#6b7280", fontSize: 12, marginTop: 0, marginBottom: 12 }}>
        Reminder only — a missing item just means that slide/column stays blank, nothing is blocked. Domain Overview,
        Backlink List, and Overview Trend apply per domain — upload one for your own site <em>and</em> one for each
        competitor (export Overview Trend with Database set to Worldwide — Domain Overview alone is always a single
        country). Target Keywords / Site Audit Overview / Site Audit Issues are own-site only; Keyword Positions is
        competitor-only.
      </p>
      <ChecklistTable rows={[{ label: "Own site", types: ownTypes, bold: true }]} columns={OWN_SITE_TYPES} />
      {competitorDomains.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <ChecklistTable
            rows={competitorDomains.map((domain) => ({
              label: domain,
              types: new Set(
                imports.filter((i) => !(i.is_own_site ?? true) && i.domain_label === domain).map((i) => i.import_type)
              ),
            }))}
            columns={COMPETITOR_TYPES}
          />
        </div>
      )}
    </div>
  );
}
