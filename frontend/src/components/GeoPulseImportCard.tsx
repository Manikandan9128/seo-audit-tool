import { useState } from "react";
import { api } from "../api/client";
import Dropzone from "./Dropzone";
import { fileTypeChip } from "./fileTypeChip";
import ConfirmDeleteButton from "./ConfirmDeleteButton";
import DownloadImportButton from "./DownloadImportButton";
import { useToast } from "./ToastProvider";

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
  const [uploadProgress, setUploadProgress] = useState<{ name: string; pct: number } | null>(null);
  const { showToast } = useToast();

  const rows = imports.filter((i) => i.import_type === "geopulse");

  async function upload() {
    if (files.length === 0) return;
    setUploading(true);
    setMsg("");
    const results: string[] = [];
    let successCount = 0;
    for (const file of files) {
      setUploadProgress({ name: file.name, pct: 0 });
      try {
        const formData = new FormData();
        formData.append("file", file);
        await api.post(`/clients/${clientId}/geopulse-upload`, formData, {
          headers: { "Content-Type": "multipart/form-data" },
          onUploadProgress: (evt) => {
            const pct = evt.total ? Math.round((evt.loaded / evt.total) * 100) : 0;
            setUploadProgress({ name: file.name, pct });
          },
        });
        results.push(`${file.name}: uploaded`);
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

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>GeoPulse Data (AEO / GEO)</h3>
      <p style={{ color: "#6b7280", fontSize: 13 }}>
        Upload the GeoPulse export for this client — any file format accepted. The AI Overview
        (AEO) and Generative Engine (GEO) Next Steps slides are generated from this data instead
        of the generic checklist once uploaded.
      </p>

      <Dropzone multiple onFiles={setFiles} compact />

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
        <button className="btn btn-primary" onClick={upload} disabled={files.length === 0 || uploading}>
          {uploading ? "Uploading..." : files.length > 0 ? `Upload ${files.length} file${files.length > 1 ? "s" : ""}` : "Upload"}
        </button>
      </div>
      {msg && <p style={{ fontSize: 13, marginTop: 8 }}>{msg}</p>}

      {rows.length > 0 && (
        <div className="table-wrap" style={{ marginTop: 16 }}>
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((imp) => {
                const chip = fileTypeChip(imp.original_filename);
                return (
                  <tr key={imp.id}>
                    <td>
                      <div className="file-name">
                        <span className={`file-icon ${chip.cls}`}>{chip.label}</span>
                        {imp.original_filename}
                      </div>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <DownloadImportButton clientId={clientId} importId={imp.id} filename={imp.original_filename} />
                      <ConfirmDeleteButton label={imp.original_filename} onConfirm={() => deleteImport(imp.id)} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
