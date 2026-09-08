import { useState } from "react";
import type { ChangeEvent } from "react";
import { api } from "../api/client";

interface GeoPulseImportSummary {
  id: string;
  import_type: string;
  original_filename: string;
  row_count: number;
  created_at: string;
}

export default function GeoPulseImportCard({
  clientId,
  imports,
  onChanged,
}: {
  clientId: string;
  imports: GeoPulseImportSummary[];
  onChanged: () => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState("");

  const rows = imports.filter((i) => i.import_type === "geopulse");

  function onFileChange(e: ChangeEvent<HTMLInputElement>) {
    setFiles(Array.from(e.target.files || []));
  }

  async function upload() {
    if (files.length === 0) return;
    setUploading(true);
    setMsg("");
    const results: string[] = [];
    for (const file of files) {
      try {
        const formData = new FormData();
        formData.append("file", file);
        await api.post(`/clients/${clientId}/geopulse-upload`, formData, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        results.push(`${file.name}: uploaded`);
      } catch (err: any) {
        results.push(`${file.name}: ${err?.response?.data?.detail || "upload failed"}`);
      }
    }
    setMsg(results.join(" · "));
    setFiles([]);
    setUploading(false);
    onChanged();
  }

  async function deleteImport(importId: string) {
    if (!confirm("Delete this GeoPulse file?")) return;
    try {
      await api.delete(`/clients/${clientId}/semrush-imports/${importId}`);
      onChanged();
    } catch (err: any) {
      setMsg(err?.response?.data?.detail || "Delete failed");
    }
  }

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>GeoPulse Data (AEO / GEO)</h3>
      <p style={{ color: "#6b7280", fontSize: 13 }}>
        Upload the GeoPulse export for this client — any file format accepted. The AI Overview
        (AEO) and Generative Engine (GEO) Next Steps slides are generated from this data instead
        of the generic checklist once uploaded.
      </p>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <input type="file" multiple onChange={onFileChange} />
        <button onClick={upload} disabled={files.length === 0 || uploading}>
          {uploading ? "Uploading..." : files.length > 1 ? `Upload ${files.length} files` : "Upload"}
        </button>
      </div>
      {msg && <p style={{ fontSize: 13, marginTop: 8 }}>{msg}</p>}

      {rows.length > 0 && (
        <table style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th>File</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((imp) => (
              <tr key={imp.id}>
                <td>{imp.original_filename}</td>
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
