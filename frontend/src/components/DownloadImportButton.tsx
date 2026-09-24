import { useState } from "react";
import { api } from "../api/client";
import { useToast } from "./ToastProvider";

// Downloads an uploaded import file. The server sends the original bytes,
// or for files uploaded before originals were kept, a CSV rebuilt from the
// parsed rows named "<name> (rebuilt).csv" — so the filename comes from the
// response's Content-Disposition, with the listed filename as fallback.
export default function DownloadImportButton({
  clientId,
  importId,
  filename,
}: {
  clientId: string;
  importId: string;
  filename: string;
}) {
  const [busy, setBusy] = useState(false);
  const { showToast } = useToast();

  async function download() {
    setBusy(true);
    try {
      const res = await api.get(`/clients/${clientId}/semrush-imports/${importId}/download`, { responseType: "blob" });
      const match = /filename\*=UTF-8''([^;]+)/i.exec(res.headers["content-disposition"] || "");
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", match ? decodeURIComponent(match[1]) : filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err: any) {
      // responseType "blob" means an error body arrives as a Blob, not JSON.
      let detail = "Download failed";
      try {
        detail = JSON.parse(await err?.response?.data?.text())?.detail || detail;
      } catch {
        // keep the generic message
      }
      showToast(detail);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" className="btn-icon" aria-label={`Download ${filename}`} title="Download" onClick={download} disabled={busy}>
      <svg viewBox="0 0 24 24" fill="none" aria-hidden>
        <path
          d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}
