import { useEffect, useState } from "react";
import { api } from "../api/client";

export type SemrushSource = "manual" | "mcp";

export interface SemrushMcpState {
  status: "idle" | "fetching" | "ready" | "not_connected" | "error";
  message?: string;
}

// Common Semrush regional databases; the full list is Semrush's own.
const DATABASES = ["us", "in", "uk", "ca", "au", "ae", "sg", "de", "fr", "es", "it", "nl", "br", "mx", "jp", "za"];

// Semrush-account import types a Semrush MCP report fetches instead —
// used here only to count what Manual Upload would use.
const SEMRUSH_ACCOUNT_TYPES = new Set([
  "domain_overview", "overview_trend", "organic_positions", "organic_competitors",
  "keyword_gap", "backlinks", "backlink_summary",
]);

// Popup shown when Generate Report is clicked: pick where this report's
// Semrush data comes from, with the state of each option, then generate.
export default function SemrushSourceModal({
  clientId,
  imports,
  initialSource,
  initialDatabase,
  onClose,
  onConfirm,
  onConnectSemrush,
}: {
  clientId: string;
  imports: { import_type: string }[];
  initialSource: SemrushSource;
  initialDatabase: string;
  onClose: () => void;
  onConfirm: (source: SemrushSource, database: string) => void;
  onConnectSemrush: () => void;
}) {
  const [source, setSource] = useState<SemrushSource>(initialSource);
  const [database, setDatabase] = useState(initialDatabase);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [snapshot, setSnapshot] = useState<{ database: string; fetched_at: number; competitors: string[] } | null>(null);

  useEffect(() => {
    api.get("/integrations/semrush/status").then((r) => setConnected(!!r.data.connected)).catch(() => setConnected(false));
    api.get(`/clients/${clientId}/semrush-mcp/snapshot`).then((r) => setSnapshot(r.data.snapshot)).catch(() => {});
  }, [clientId]);

  const manualFiles = imports.filter((i) => SEMRUSH_ACCOUNT_TYPES.has(i.import_type)).length;
  const snapshotFresh = snapshot && snapshot.database === database && Date.now() / 1000 - snapshot.fetched_at < 24 * 3600;
  const canGenerate = source === "manual" || connected === true;

  const optionStyle = (active: boolean) => ({
    display: "flex",
    alignItems: "flex-start",
    gap: 10,
    padding: 12,
    borderRadius: 8,
    border: `1px solid ${active ? "var(--primary, #4f46e5)" : "var(--border)"}`,
    cursor: "pointer",
    fontSize: 13,
  });

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}
      onClick={onClose}
    >
      <div className="card" style={{ maxWidth: 460, width: "100%", display: "flex", flexDirection: "column", gap: 12 }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ margin: 0, fontSize: 18 }}>Semrush Data Source</h3>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Choose where this report's Semrush data comes from.
        </p>

        <label style={optionStyle(source === "manual")}>
          <input type="radio" name="semrush-source" checked={source === "manual"} onChange={() => setSource("manual")} style={{ marginTop: 3 }} />
          <span>
            <strong>Manual Upload</strong>
            <br />
            <span className="muted" style={{ fontSize: 12 }}>Upload Semrush CSV files manually</span>
            {source === "manual" && (
              <span style={{ display: "block", fontSize: 12, marginTop: 6 }}>
                {manualFiles > 0
                  ? `Uses the ${manualFiles} Semrush file${manualFiles === 1 ? "" : "s"} uploaded under Data Sources.`
                  : "No Semrush files uploaded yet — add them under Data Sources, or the Semrush slides will be empty."}
              </span>
            )}
          </span>
        </label>

        <label style={optionStyle(source === "mcp")}>
          <input type="radio" name="semrush-source" checked={source === "mcp"} onChange={() => setSource("mcp")} style={{ marginTop: 3 }} />
          <span style={{ flex: 1 }}>
            <strong>Semrush MCP</strong>
            <br />
            <span className="muted" style={{ fontSize: 12 }}>Automatically fetch data from your connected Semrush account</span>
            {source === "mcp" && (
              <span style={{ display: "block", fontSize: 12, marginTop: 6 }}>
                {connected === null && <span className="muted">Checking Semrush connection…</span>}
                {connected === false && (
                  <span>
                    Connect your Semrush account to continue.{" "}
                    <button className="btn btn-secondary" onClick={onConnectSemrush} style={{ marginLeft: 4 }}>
                      Connect Semrush
                    </button>
                  </span>
                )}
                {connected === true && (
                  <>
                    <span style={{ color: "var(--success)" }}>✓ Semrush connected</span>
                    <span style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
                      Database
                      <select value={database} onChange={(e) => setDatabase(e.target.value)}>
                        {DATABASES.map((d) => (
                          <option key={d} value={d}>
                            {d.toUpperCase()}
                          </option>
                        ))}
                      </select>
                    </span>
                    <span className="muted" style={{ display: "block", marginTop: 6 }}>
                      {snapshotFresh
                        ? `Reuses data fetched ${new Date(snapshot!.fetched_at * 1000).toLocaleString()} — no new API units.`
                        : "Fetches fresh data — uses Semrush API units (about 10,000 for a full fetch)."}
                    </span>
                  </>
                )}
              </span>
            )}
          </span>
        </label>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <button className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={() => onConfirm(source, database)} disabled={!canGenerate}>
            Generate Report
          </button>
        </div>
      </div>
    </div>
  );
}
