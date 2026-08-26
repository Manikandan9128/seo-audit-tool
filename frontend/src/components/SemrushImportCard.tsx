import { useState } from "react";
import type { ChangeEvent } from "react";
import { api } from "../api/client";

interface SemrushImportSummary {
  id: string;
  import_type: string;
  original_filename: string;
  row_count: number;
  created_at: string;
  is_own_site?: boolean;
  domain_label?: string | null;
}

export default function SemrushImportCard({
  clientId,
  title,
  description,
  isOwnSite,
  mcpHint,
  imports,
  onChanged,
}: {
  clientId: string;
  title: string;
  description: string;
  isOwnSite: boolean;
  mcpHint: string;
  imports: SemrushImportSummary[];
  onChanged: () => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [domainLabel, setDomainLabel] = useState("");
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState("");

  const rows = imports.filter((i) => (i.is_own_site ?? true) === isOwnSite);

  function onFileChange(e: ChangeEvent<HTMLInputElement>) {
    setFiles(Array.from(e.target.files || []));
  }

  async function upload() {
    if (files.length === 0) return;
    if (!isOwnSite && !domainLabel.trim()) {
      setMsg("Enter the competitor's domain first, so it doesn't get mixed up with your own data.");
      return;
    }
    setUploading(true);
    setMsg("");
    const results: string[] = [];
    for (const file of files) {
      try {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("is_own_site", String(isOwnSite));
        if (!isOwnSite && domainLabel.trim()) formData.append("domain_label", domainLabel.trim());
        const res = await api.post(`/clients/${clientId}/semrush-upload`, formData, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        results.push(`${file.name}: ${res.data.import_type.replace("_", " ")} (${res.data.row_count} rows)`);
      } catch (err: any) {
        results.push(`${file.name}: ${err?.response?.data?.detail || "upload failed"}`);
      }
    }
    setMsg(results.join(" · "));
    setFiles([]);
    setDomainLabel("");
    setUploading(false);
    onChanged();
  }

  async function deleteImport(importId: string) {
    if (!confirm("Delete this import?")) return;
    try {
      await api.delete(`/clients/${clientId}/semrush-imports/${importId}`);
      onChanged();
    } catch (err: any) {
      setMsg(err?.response?.data?.detail || "Delete failed");
    }
  }

  async function copyMcpPrompt() {
    const backendBase = `${window.location.protocol}//${window.location.hostname}:8001`;
    const prompt = `${mcpHint} Save each export as a CSV, then POST it as multipart/form-data field "file" to ${backendBase}/api/clients/${clientId}/semrush-upload${
      isOwnSite ? "" : ' with a "domain_label" field naming the competitor domain'
    }.`;
    try {
      await navigator.clipboard.writeText(prompt);
      setMsg("Prompt copied — paste into Claude Code chat.");
    } catch {
      setMsg(prompt);
    }
  }

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <p style={{ color: "#6b7280", fontSize: 13 }}>{description}</p>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <input type="file" accept=".csv,.xlsx,.xls" multiple onChange={onFileChange} />
        {!isOwnSite && (
          <input
            type="text"
            placeholder="Competitor domain (required)"
            value={domainLabel}
            onChange={(e) => setDomainLabel(e.target.value)}
            style={{ width: 200 }}
          />
        )}
        <button onClick={upload} disabled={files.length === 0 || uploading || (!isOwnSite && !domainLabel.trim())}>
          {uploading ? "Uploading..." : files.length > 1 ? `Upload ${files.length} files` : "Upload"}
        </button>
        <button onClick={copyMcpPrompt}>Fetch via Claude (Semrush MCP)</button>
      </div>
      {msg && <p style={{ fontSize: 13, marginTop: 8 }}>{msg}</p>}

      {rows.length > 0 && (
        <table style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th>File</th>
              <th>Type</th>
              {!isOwnSite && <th>Domain</th>}
              <th>Rows</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((imp) => (
              <tr key={imp.id}>
                <td>{imp.original_filename}</td>
                <td>{imp.import_type.replace("_", " ")}</td>
                {!isOwnSite && <td>{imp.domain_label || "—"}</td>}
                <td>{imp.row_count}</td>
                <td>
                  <button onClick={() => deleteImport(imp.id)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
