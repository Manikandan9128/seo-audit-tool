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

interface UndownloadedReport {
  job_id: string;
  client_id: string;
  client_name: string;
  filename: string | null;
  generated_at: string;
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

// Local YYYY-MM-DD for both the date input's value and the row-vs-filter
// comparison — using an ISO date-only string here would compare in UTC
// and could put a late-evening local download on the "wrong" day.
function localDateKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export default function DownloadedReportsPage() {
  const [reports, setReports] = useState<DownloadedReport[] | null>(null);
  const [error, setError] = useState("");
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  // Custom date filter, defaults to today; cleared to show every date.
  const [dateFilter, setDateFilter] = useState(localDateKey(new Date()));
  const [legacy, setLegacy] = useState<UndownloadedReport[] | null>(null);
  const [markingId, setMarkingId] = useState<string | null>(null);

  function loadDownloaded() {
    api
      .get("/clients/reports/downloaded")
      .then((res) => setReports(res.data))
      .catch(() => setError("Couldn't load downloaded reports."));
  }

  function loadLegacy() {
    api
      .get("/clients/reports/undownloaded")
      .then((res) => setLegacy(res.data))
      .catch(() => {});
  }

  useEffect(() => {
    loadDownloaded();
    loadLegacy();
  }, []);

  async function markDownloaded(r: UndownloadedReport) {
    // One-time action: the row is only ever in this legacy list because
    // downloaded_at is still null — once this succeeds it's stamped,
    // the row disappears from here (loadLegacy() refetch below) and
    // clicking it again is no longer possible.
    setMarkingId(r.job_id);
    try {
      await api.post(`/clients/${r.client_id}/generate-report/${r.job_id}/mark-downloaded`);
      loadDownloaded();
      loadLegacy();
    } catch {
      setError("Couldn't add that report — try again.");
    } finally {
      setMarkingId(null);
    }
  }

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

  const visible = reports?.filter((r) => !dateFilter || localDateKey(new Date(r.downloaded_at)) === dateFilter) ?? null;

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
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
          <label htmlFor="downloaded-date-filter" className="muted" style={{ fontSize: 13 }}>
            Date
          </label>
          <input
            id="downloaded-date-filter"
            type="date"
            value={dateFilter}
            onChange={(e) => setDateFilter(e.target.value)}
          />
          {dateFilter && (
            <button className="btn btn-secondary" onClick={() => setDateFilter("")}>
              Clear
            </button>
          )}
        </div>
      )}

      {legacy && legacy.length > 0 && (
        <div className="card" style={{ padding: 0, overflow: "hidden", marginBottom: 16 }}>
          <p className="muted" style={{ margin: "16px 16px 0" }}>
            Reports generated before this tab existed — add the ones you already downloaded.
          </p>
          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 12 }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border, #e5e5e5)" }}>
                <th style={{ padding: "10px 16px" }}>Client</th>
                <th style={{ padding: "10px 16px" }}>Generated</th>
                <th style={{ padding: "10px 16px" }}>File</th>
                <th style={{ padding: "10px 16px" }}></th>
              </tr>
            </thead>
            <tbody>
              {legacy.map((r) => (
                <tr key={r.job_id} style={{ borderBottom: "1px solid var(--border, #f0f0f0)" }}>
                  <td style={{ padding: "10px 16px" }}>
                    <Link to={`/clients/${r.client_id}`}>{r.client_name}</Link>
                  </td>
                  <td style={{ padding: "10px 16px" }}>{formatDownloadedAt(r.generated_at)}</td>
                  <td style={{ padding: "10px 16px" }} className="muted">
                    {r.filename || "—"}
                  </td>
                  <td style={{ padding: "10px 16px" }}>
                    <button
                      className="btn btn-secondary"
                      onClick={() => markDownloaded(r)}
                      disabled={markingId === r.job_id}
                    >
                      {markingId === r.job_id ? "Adding..." : "Add"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {error && <div className="card" style={{ color: "#b91c1c" }}>{error}</div>}

      {reports === null && !error && <p className="muted">Loading...</p>}

      {reports && reports.length > 0 && visible && visible.length === 0 && (
        <p className="muted">No reports downloaded on that date.</p>
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
