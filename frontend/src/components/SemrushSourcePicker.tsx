import { useState } from "react";

export type SemrushSource = "manual" | "mcp";

export interface SemrushMcpState {
  status: "idle" | "fetching" | "ready" | "not_connected" | "error";
  message?: string;
}

// Common Semrush regional databases; the full list is Semrush's own.
const DATABASES = ["us", "in", "uk", "ca", "au", "ae", "sg", "de", "fr", "es", "it", "nl", "br", "mx", "jp", "za"];

// "Semrush Data Source" chooser in the client page's Generate Report bar —
// same dropdown-card pattern as the Sections picker next to it.
export default function SemrushSourcePicker({
  source,
  onSourceChange,
  database,
  onDatabaseChange,
  disabled,
}: {
  source: SemrushSource;
  onSourceChange: (s: SemrushSource) => void;
  database: string;
  onDatabaseChange: (d: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const options: { value: SemrushSource; label: string; description: string }[] = [
    { value: "manual", label: "Manual Upload", description: "Upload Semrush CSV files manually" },
    { value: "mcp", label: "Semrush MCP", description: "Automatically fetch data from your connected Semrush account" },
  ];

  return (
    <div style={{ position: "relative" }}>
      <button className="secondary" onClick={() => setOpen((o) => !o)} disabled={disabled}>
        Semrush: {source === "mcp" ? `MCP (${database.toUpperCase()})` : "Manual"} ▾
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 10 }} />
          <div
            className="card"
            style={{
              position: "absolute",
              top: "110%",
              right: 0,
              zIndex: 11,
              width: 300,
              padding: 12,
              display: "flex",
              flexDirection: "column",
              gap: 10,
              boxShadow: "0 12px 32px rgba(20, 20, 15, 0.14)",
            }}
          >
            <p className="eyebrow" style={{ margin: 0 }}>
              Semrush Data Source
            </p>
            {options.map((opt) => (
              <label key={opt.value} style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 13, cursor: "pointer" }}>
                <input
                  type="radio"
                  name="semrush-source"
                  checked={source === opt.value}
                  onChange={() => onSourceChange(opt.value)}
                  style={{ marginTop: 3 }}
                />
                <span>
                  <strong>{opt.label}</strong>
                  <br />
                  <span className="muted" style={{ fontSize: 12 }}>
                    {opt.description}
                  </span>
                </span>
              </label>
            ))}
            {source === "mcp" && (
              <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                Database
                <select value={database} onChange={(e) => onDatabaseChange(e.target.value)}>
                  {DATABASES.map((d) => (
                    <option key={d} value={d}>
                      {d.toUpperCase()}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        </>
      )}
    </div>
  );
}
