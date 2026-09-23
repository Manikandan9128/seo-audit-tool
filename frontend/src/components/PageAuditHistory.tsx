import { useEffect, useState } from "react";
import { api } from "../api/client";

interface HistoryEntry {
  id: string;
  created_at: string;
  pages_checked: number;
  pages_with_issues: number;
}

function fmtWhen(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

// Tabs for past finished All Pages crawls. The selected run's result is
// handed up via onSelect so the parent keeps rendering it with the same
// PageAuditTable/SchemaValidationPanel as a fresh run. On a refreshKey bump
// (a crawl just finished) the parent already holds that run's result, so
// only the list reloads and the newest tab is marked — no refetch of a
// possibly 2000-page result.
export default function PageAuditHistory({
  clientId,
  refreshKey,
  onSelect,
}: {
  clientId: string;
  refreshKey: number;
  onSelect: (result: any) => void;
}) {
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState("");

  async function loadHistory(fetchNewest: boolean) {
    setError("");
    try {
      const res = await api.get(`/clients/${clientId}/site-audit-pages/history`);
      setHistory(res.data);
      if (res.data.length === 0) return;
      if (fetchNewest) await selectRun(res.data[0].id);
      else setSelectedId(res.data[0].id);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't load All Pages history");
    }
  }

  async function selectRun(jobId: string) {
    setSelectedId(jobId);
    setSwitching(true);
    try {
      const res = await api.get(`/clients/${clientId}/site-audit-pages/${jobId}`);
      onSelect(res.data.result);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't load that crawl");
    } finally {
      setSwitching(false);
    }
  }

  useEffect(() => {
    loadHistory(refreshKey === 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, refreshKey]);

  if (error) return <p style={{ fontSize: 13, color: "#991b1b" }}>{error}</p>;
  if (history.length === 0) return null;

  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", borderBottom: "1px solid var(--border)", paddingBottom: 8, marginTop: 12 }}>
      {history.map((h) => (
        <button
          key={h.id}
          onClick={() => selectRun(h.id)}
          disabled={switching}
          className={h.id === selectedId ? "" : "secondary"}
          style={{ fontSize: 12, padding: "6px 10px" }}
        >
          {fmtWhen(h.created_at)} · {h.pages_checked} page{h.pages_checked === 1 ? "" : "s"}
          {h.pages_with_issues > 0 ? ` · ${h.pages_with_issues} with issues` : " · clean"}
        </button>
      ))}
      {switching && <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading...</span>}
    </div>
  );
}
