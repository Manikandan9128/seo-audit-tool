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

export default function DownloadedReportsPage() {
  const [reports, setReports] = useState<DownloadedReport[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get("/clients/reports/downloaded")
      .then((res) => setReports(res.data))
      .catch(() => setError("Couldn't load downloaded reports."));
  }, []);

  return (
    <div className="clients-page">
      <div className="clients-header">
        <h1>Downloaded Reports</h1>
        <p className="muted">
          Every report that was freshly generated and then downloaded at least once — re-downloading the
          same report again doesn't add another row here.
        </p>
      </div>

      {error && <div className="card" style={{ color: "#b91c1c" }}>{error}</div>}

      {reports === null && !error && <p className="muted">Loading...</p>}

      {reports && reports.length === 0 && (
        <p className="muted">No reports downloaded yet.</p>
      )}

      {reports && reports.length > 0 && (
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border, #e5e5e5)" }}>
                <th style={{ padding: "10px 16px" }}>Client</th>
                <th style={{ padding: "10px 16px" }}>Downloaded</th>
                <th style={{ padding: "10px 16px" }}>File</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.job_id} style={{ borderBottom: "1px solid var(--border, #f0f0f0)" }}>
                  <td style={{ padding: "10px 16px" }}>
                    <Link to={`/clients/${r.client_id}`}>{r.client_name}</Link>
                  </td>
                  <td style={{ padding: "10px 16px" }}>{formatDownloadedAt(r.downloaded_at)}</td>
                  <td style={{ padding: "10px 16px" }} className="muted">
                    {r.filename || "—"}
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
