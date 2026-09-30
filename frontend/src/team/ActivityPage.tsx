import { useEffect, useState } from "react";
import { api } from "../api/client";

interface ActivityRow {
  id: string;
  created_at: string;
  user_name: string;
  user_email: string;
  user_role: string;
  action: string;
  client_name: string | null;
  detail: Record<string, unknown> | null;
}

const ACTION_LABEL: Record<string, string> = {
  login: "Logged in",
  registered: "Created account",
  client_created: "Created client",
  file_uploaded: "Uploaded file",
  file_deleted: "Deleted file",
  report_previewed: "Previewed report",
  report_generation_started: "Started report generation",
  role_changed: "Changed a role",
};

function describe(row: ActivityRow): string {
  const d = row.detail || {};
  if (d.filename) return `${d.filename}${d.import_type ? ` (${String(d.import_type).replace(/_/g, " ")})` : ""}`;
  if (row.action === "role_changed") return `${d.target_user}: ${d.from_role} to ${d.to_role}`;
  if (d.keyword_cluster_mode) return `clusters: ${d.keyword_cluster_mode}${d.ai_provider ? `, AI: ${d.ai_provider}` : ""}`;
  return "";
}

export default function ActivityPage() {
  const [rows, setRows] = useState<ActivityRow[]>([]);
  const [action, setAction] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get("/activity", { params: { limit: 300, ...(action ? { action } : {}) } })
      .then((res) => setRows(res.data))
      .catch((err) => setError(err?.response?.data?.detail || "Could not load activity"));
  }, [action]);

  return (
    <div className="card">
      <h2 className="card-title">Activity log</h2>
      <p className="card-desc">Who uploaded, deleted or generated what, and when.</p>
      <label style={{ fontSize: 12 }}>
        Show{" "}
        <select value={action} onChange={(e) => setAction(e.target.value)}>
          <option value="">Everything</option>
          {Object.entries(ACTION_LABEL).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
      </label>
      {error && <p style={{ color: "var(--color-danger-text)", fontSize: 13 }}>{error}</p>}
      <table className="data-table">
        <thead>
          <tr>
            <th>When</th>
            <th>Who</th>
            <th>What</th>
            <th>Client</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{new Date(r.created_at).toLocaleString()}</td>
              <td>{r.user_name || r.user_email}<div className="muted" style={{ fontSize: 11 }}>{r.user_email}</div></td>
              <td>{ACTION_LABEL[r.action] ?? r.action}</td>
              <td>{r.client_name ?? "—"}</td>
              <td>{describe(r)}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={5} className="muted">No activity yet.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
