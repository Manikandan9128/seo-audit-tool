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

// One shared row shape so both endpoints' results can live in a single
// list — a report generated but not yet downloaded still shows here
// (2026-09-25: user found it confusing having it split into a separate
// segment above the main list), just marked "Not downloaded yet" instead
// of a real time, and using generated_at to sort/filter until it's
// actually fetched.
interface ReportRow {
  job_id: string;
  client_id: string;
  client_name: string;
  filename: string | null;
  timestamp: string;
  downloaded: boolean;
}

function formatTimestamp(iso: string): string {
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
  const [downloaded, setDownloaded] = useState<DownloadedReport[] | null>(null);
  const [undownloaded, setUndownloaded] = useState<UndownloadedReport[] | null>(null);
  const [error, setError] = useState("");
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  // Custom date-range filter, defaults to today for both ends; either side
  // cleared to make that end open-ended, both cleared to show every date.
  const today = localDateKey(new Date());
  const [dateFrom, setDateFrom] = useState(today);
  const [dateTo, setDateTo] = useState(today);

  function loadDownloaded() {
    api
      .get("/clients/reports/downloaded")
      .then((res) => setDownloaded(res.data))
      .catch(() => setError("Couldn't load downloaded reports."));
  }

  function loadUndownloaded() {
    api
      .get("/clients/reports/undownloaded")
      .then((res) => setUndownloaded(res.data))
      .catch(() => {});
  }

  useEffect(() => {
    loadDownloaded();
    loadUndownloaded();
  }, []);

  async function downloadJob(r: { job_id: string; client_id: string; filename: string | null }) {
    // Same real /download endpoint for every row — it stamps
    // downloaded_at on a job's first fetch, so downloading a row that was
    // only "generated" turns it into a real downloaded one (and the two
    // lists get refetched below so it reflects that immediately).
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
      loadDownloaded();
      loadUndownloaded();
    } catch {
      setError("Couldn't download that report — try again.");
    } finally {
      setDownloadingId(null);
    }
  }

  const rows: ReportRow[] | null =
    downloaded === null || undownloaded === null
      ? null
      : [
          ...downloaded.map((r): ReportRow => ({ ...r, timestamp: r.downloaded_at, downloaded: true })),
          ...undownloaded.map((r): ReportRow => ({ ...r, timestamp: r.generated_at, downloaded: false })),
        ].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

  const visible =
    rows?.filter((r) => {
      const key = localDateKey(new Date(r.timestamp));
      if (dateFrom && key < dateFrom) return false;
      if (dateTo && key > dateTo) return false;
      return true;
    }) ?? null;

  return (
    <div className="clients-page">
      <div className="clients-header">
        <h1>Downloaded Reports</h1>
        <p className="muted">
          Every report that's been generated, most recently downloaded first — re-downloading an
          already-downloaded report doesn't add another row.
        </p>
      </div>

      {rows && rows.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
          <label htmlFor="downloaded-date-from" className="muted" style={{ fontSize: 13 }}>
            From
          </label>
          <input
            id="downloaded-date-from"
            type="date"
            value={dateFrom}
            max={dateTo || undefined}
            onChange={(e) => setDateFrom(e.target.value)}
          />
          <label htmlFor="downloaded-date-to" className="muted" style={{ fontSize: 13 }}>
            To
          </label>
          <input
            id="downloaded-date-to"
            type="date"
            value={dateTo}
            min={dateFrom || undefined}
            onChange={(e) => setDateTo(e.target.value)}
          />
          {(dateFrom || dateTo) && (
            <button
              className="btn btn-secondary"
              onClick={() => {
                setDateFrom("");
                setDateTo("");
              }}
            >
              Clear
            </button>
          )}
        </div>
      )}

      {error && <div className="card" style={{ color: "#b91c1c" }}>{error}</div>}

      {rows === null && !error && <p className="muted">Loading...</p>}

      {rows && rows.length > 0 && visible && visible.length === 0 && (
        <p className="muted">No reports in that date range.</p>
      )}

      {rows && rows.length === 0 && <p className="muted">No reports yet.</p>}

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
                  <td style={{ padding: "10px 16px" }}>
                    {r.downloaded ? formatTimestamp(r.timestamp) : (
                      <span className="muted">{formatTimestamp(r.timestamp)}</span>
                    )}
                  </td>
                  <td style={{ padding: "10px 16px" }} className="muted">
                    {r.filename || "—"}
                  </td>
                  <td style={{ padding: "10px 16px" }}>
                    <button
                      className="btn btn-secondary"
                      onClick={() => downloadJob(r)}
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
