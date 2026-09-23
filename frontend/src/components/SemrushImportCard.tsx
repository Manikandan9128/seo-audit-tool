import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { SEMRUSH_MCP_ENABLED } from "../features";
import Dropzone from "./Dropzone";
import { fileTypeChip } from "./fileTypeChip";
import ConfirmDeleteButton from "./ConfirmDeleteButton";
import { useToast } from "./ToastProvider";

interface SemrushImportSummary {
  id: string;
  import_type: string;
  original_filename: string;
  row_count: number;
  created_at: string;
  is_own_site?: boolean;
  domain_label?: string | null;
}

interface DomainRatingRow {
  id: string;
  domain: string;
  dr: number;
}

function normalizeDomain(d: string) {
  return d.replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/$/, "").toLowerCase();
}

function FileTable({ rows, deleteImport }: {
  rows: SemrushImportSummary[];
  deleteImport: (id: string) => void;
}) {
  const [search, setSearch] = useState("");
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.original_filename.toLowerCase().includes(q) ||
        r.import_type.toLowerCase().includes(q) ||
        (r.domain_label || "").toLowerCase().includes(q)
    );
  }, [rows, search]);

  return (
    <div>
      {rows.length > 10 && (
        <div className="table-toolbar">
          <div className="search-input">
            <svg viewBox="0 0 24 24" fill="none">
              <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
              <path d="M21 21l-4.3-4.3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            <input placeholder="Search files…" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <span style={{ fontSize: 12, color: "var(--color-text-tertiary)" }}>{filtered.length} of {rows.length} files</span>
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>File</th>
              <th>Type</th>
              <th className="num">Rows</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((imp) => {
              const chip = fileTypeChip(imp.original_filename);
              return (
                <tr key={imp.id}>
                  <td>
                    <div className="file-name">
                      <span className={`file-icon ${chip.cls}`}>{chip.label}</span>
                      {imp.original_filename}
                    </div>
                  </td>
                  <td>
                    <span className="type-badge">{imp.import_type.replace(/_/g, " ")}</span>
                  </td>
                  <td className="num">{imp.row_count}</td>
                  <td style={{ textAlign: "right" }}>
                    <ConfirmDeleteButton label={imp.original_filename} onConfirm={() => deleteImport(imp.id)} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
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
  const [domainRatings, setDomainRatings] = useState<DomainRatingRow[]>([]);
  const [closedDomains, setClosedDomains] = useState<Set<string>>(new Set());
  const [uploadProgress, setUploadProgress] = useState<{ name: string; pct: number } | null>(null);
  const { showToast } = useToast();

  const rows = imports.filter((i) => (i.is_own_site ?? true) === isOwnSite);

  // Read-only — the Competitor Data accordion shows each domain's DR next
  // to its file count (point 6), reusing the same data DomainRatingEditor
  // already manages; this is its own independent GET, not a write path.
  useEffect(() => {
    if (isOwnSite) return;
    api
      .get(`/clients/${clientId}/domain-ratings`)
      .then((res) => setDomainRatings(res.data))
      .catch(() => {});
  }, [clientId, isOwnSite]);

  function toggleDomain(domain: string) {
    setClosedDomains((prev) => {
      const next = new Set(prev);
      if (next.has(domain)) next.delete(domain);
      else next.add(domain);
      return next;
    });
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
    let successCount = 0;
    for (const file of files) {
      setUploadProgress({ name: file.name, pct: 0 });
      try {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("is_own_site", String(isOwnSite));
        if (!isOwnSite && domainLabel.trim()) formData.append("domain_label", domainLabel.trim());
        const res = await api.post(`/clients/${clientId}/semrush-upload`, formData, {
          headers: { "Content-Type": "multipart/form-data" },
          onUploadProgress: (evt) => {
            const pct = evt.total ? Math.round((evt.loaded / evt.total) * 100) : 0;
            setUploadProgress({ name: file.name, pct });
          },
        });
        results.push(`${file.name}: ${res.data.import_type.replace("_", " ")} (${res.data.row_count} rows)`);
        successCount += 1;
      } catch (err: any) {
        results.push(`${file.name}: ${err?.response?.data?.detail || "upload failed"}`);
      }
    }
    setUploadProgress(null);
    setMsg(results.join(" · "));
    if (successCount === files.length) {
      showToast(`${successCount} file${successCount > 1 ? "s" : ""} uploaded successfully`);
    } else if (successCount > 0) {
      showToast(`${successCount} of ${files.length} file(s) uploaded — see details below`);
    }
    setFiles([]);
    setDomainLabel("");
    setUploading(false);
    onChanged();
  }

  async function deleteImport(importId: string) {
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

  const competitorDomains = useMemo(() => {
    if (isOwnSite) return [];
    const byDomain = new Map<string, SemrushImportSummary[]>();
    for (const r of rows) {
      const key = r.domain_label || "Unlabeled";
      if (!byDomain.has(key)) byDomain.set(key, []);
      byDomain.get(key)!.push(r);
    }
    return Array.from(byDomain.entries()).sort((a, b) => b[1].length - a[1].length);
  }, [rows, isOwnSite]);

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <p style={{ color: "#6b7280", fontSize: 13 }}>{description}</p>

      {!isOwnSite && (
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 14 }}>
          <input
            type="text"
            placeholder="Competitor domain (required)"
            value={domainLabel}
            onChange={(e) => setDomainLabel(e.target.value)}
            style={{ width: 240 }}
          />
        </div>
      )}

      <Dropzone
        accept=".csv,.xlsx,.xls,.pdf,.json,.xml,.tsv,.png"
        multiple
        onFiles={setFiles}
        hint={
          isOwnSite
            ? "Accepted: CSV, XLSX, XLS, PDF, JSON, XML, TSV, PNG — type is auto-detected. PNG is a fallback for the Site Audit Overview export only — a screenshot of that Semrush page, for when the PDF download isn't available."
            : "Enter the competitor domain above first — select multiple files to bulk-upload"
        }
      />

      {uploadProgress && (
        <div className="upload-progress">
          <div className="upload-progress-top">
            <span className="fname">
              <span className={`file-icon ${fileTypeChip(uploadProgress.name).cls}`} style={{ width: 20, height: 20, fontSize: 7 }}>
                {fileTypeChip(uploadProgress.name).label}
              </span>
              {uploadProgress.name}
            </span>
            <span className="upload-pct">{uploadProgress.pct}%</span>
          </div>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${uploadProgress.pct}%` }} />
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button
          className="btn btn-primary"
          onClick={upload}
          disabled={files.length === 0 || uploading || (!isOwnSite && !domainLabel.trim())}
        >
          {uploading ? "Uploading..." : files.length > 0 ? `Upload ${files.length} file${files.length > 1 ? "s" : ""}` : "Upload"}
        </button>
        {SEMRUSH_MCP_ENABLED && (
          <button className="btn btn-outline btn-sm" onClick={copyMcpPrompt}>
            Fetch via Claude (Semrush MCP)
          </button>
        )}
      </div>
      {msg && <p style={{ fontSize: 13, marginTop: 8 }}>{msg}</p>}

      {isOwnSite && rows.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <FileTable rows={rows} deleteImport={deleteImport} />
        </div>
      )}

      {!isOwnSite && competitorDomains.length > 0 && (
        <div style={{ marginTop: 16 }}>
          {competitorDomains.map(([domain, domainRows]) => {
            const open = !closedDomains.has(domain);
            const rating = domainRatings.find((d) => normalizeDomain(d.domain) === normalizeDomain(domain));
            return (
              <div key={domain} className={`competitor-group${open ? " open" : ""}`}>
                <div className="competitor-head" onClick={() => toggleDomain(domain)}>
                  <div className="competitor-head-left">
                    <svg className="competitor-chev" viewBox="0 0 24 24" fill="none">
                      <path d="M9 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    <span className="competitor-domain">{domain}</span>
                  </div>
                  <div className="competitor-meta">
                    {rating && <span className="competitor-dr-badge">DR {rating.dr}</span>}
                    <span className="competitor-file-count">{domainRows.length} file{domainRows.length > 1 ? "s" : ""}</span>
                  </div>
                </div>
                {open && (
                  <div className="competitor-body">
                    <FileTable rows={domainRows} deleteImport={deleteImport} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
