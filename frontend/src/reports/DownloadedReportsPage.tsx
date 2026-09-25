import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";

interface DownloadedReport {
  job_id: string;
  client_id: string;
  client_name: string;
  filename: string | null;
  downloaded_at: string;
}

function formatDownloadedAt(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function filenameFrom(disposition: string | undefined, fallback: string) {
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition || "");
  if (star) return decodeURIComponent(star[1]);
  const plain = /filename="?([^";]+)"?/i.exec(disposition || "");
  return plain ? plain[1] : fallback;
}

function isToday(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

export default function DownloadedReportsPage() {
  const [reports, setReports] = useState<DownloadedReport[] | null>(null);
  const [error, setError] = useState("");
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  // Defaults to Today — this filter exists specifically so a fresh
  // generate+download can be checked against this tab right away without
  // scrolling past older rows.
  const [scope, setScope] = useState<"today" | "all">("today");

  useEffect(() => {
    api
      .get("/clients/reports/downloaded")
      .then((res) => setReports(res.data))
      .catch(() => setError("Couldn't load downloaded reports."));
  }, []);

  async function redownload(r: DownloadedReport) {
    // Re-fetching an already-downloaded job's file on purpose — the
    // backend only stamps downloaded_at on a job's FIRST fetch, so this
    // never adds another row or moves this one, same as clicking
    // Download again on the client page would do.
    setDownloadingId(r.job_id);
    try {
      const res = await api.get(`/clients/${r.client_id}/generate-report/${r.job_id}/download`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", filenameFrom(res.headers["content-disposition"], r.filename || "seo-audit.pptx"));
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      setError("Couldn't download that report — try again.");
    } finally {
      setDownloadingId(null);
    }
  }

  const visible = reports?.filter((r) => scope === "all" || isToday(r.downloaded_at)) ?? null;

  return (
    <div className="clients-page">
      <div className="clients-header">
        <h1>Downloaded Reports</h1>
        <p className="muted">
          Every report that was freshly generated and then downloaded at least once — re-downloading the
          same report again doesn't add another row here.
        </p>
      </div>

      {reports && reports.length > 0 && (
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <button
            className={`btn ${scope === "today" ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setScope("today")}
          >
            Today
          </button>
          <button
            className={`btn ${scope === "all" ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setScope("all")}
          >
            All
          </button>
        </div>
      )}

      {error && <div className="card" style={{ color: "#b91c1c" }}>{error}</div>}

      {reports === null && !error && <p className="muted">Loading...</p>}

      {reports && reports.length > 0 && visible && visible.length === 0 && (
        <p className="muted">No reports downloaded today yet.</p>
      )}

      {reports && reports.length === 0 && (
        <p className="muted">No reports downloaded yet.</p>
      )}

      {visible && visible.length > 0 && (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border, #e5e5e5)" }}>
                <th style={{ padding: "10px 16px" }}>Client</th>
                <th style={{ padding: "10px 16px" }}>Downloaded</th>
                <th style={{ padding: "10px 16px" }}>File</th>
                <th style={{ padding: "10px 16px" }}></th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr key={r.job_id} style={{ borderBottom: "1px solid var(--border, #f0f0f0)" }}>
                  <td style={{ padding: "10px 16px" }}>
                    <Link to={`/clients/${r.client_id}`}>{r.client_name}</Link>
                  </td>
                  <td style={{ padding: "10px 16px" }}>{formatDownloadedAt(r.downloaded_at)}</td>
                  <td style={{ padding: "10px 16px" }} className="muted">
                    {r.filename || "—"}
                  </td>
                  <td style={{ padding: "10px 16px" }}>
                    <button
                      className="btn btn-secondary"
                      onClick={() => redownload(r)}
                      disabled={downloadingId === r.job_id}
                    >
                      {downloadingId === r.job_id ? "Downloading..." : "Download"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
