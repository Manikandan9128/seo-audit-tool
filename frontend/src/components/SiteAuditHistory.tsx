import { useEffect, useState } from "react";
import { api } from "../api/client";
import SiteAuditReport from "./SiteAuditReport";

interface HistoryEntry {
  id: string;
  created_at: string;
  issue_count: number;
  reachable: boolean | null;
}

function fmtWhen(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function diffIssues(current: string[], previous: string[]) {
  const currentSet = new Set(current);
  const previousSet = new Set(previous);
  return {
    added: current.filter((i) => !previousSet.has(i)),
    resolved: previous.filter((i) => !currentSet.has(i)),
  };
}

export default function SiteAuditHistory({ clientId, refreshKey, gscConnected }: { clientId: string; refreshKey: number; gscConnected?: boolean }) {
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedResult, setSelectedResult] = useState<any>(null);
  const [previousResult, setPreviousResult] = useState<any>(null);
  const [previousMeta, setPreviousMeta] = useState<HistoryEntry | null>(null);
  const [error, setError] = useState("");

  async function loadHistory() {
    setLoading(true);
    setError("");
    try {
      const res = await api.get(`/clients/${clientId}/site-audit/history`);
      setHistory(res.data);
      if (res.data.length > 0) {
        selectRun(res.data[0].id, res.data);
      } else {
        setSelectedId(null);
        setSelectedResult(null);
        setPreviousResult(null);
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't load site audit history");
    } finally {
      setLoading(false);
    }
  }

  async function selectRun(runId: string, list: HistoryEntry[] = history) {
    setSelectedId(runId);
    const idx = list.findIndex((h) => h.id === runId);
    const prevEntry = idx >= 0 && idx + 1 < list.length ? list[idx + 1] : null;
    const [currentRes, previousRes] = await Promise.all([
      api.get(`/clients/${clientId}/site-audit/${runId}`),
      prevEntry ? api.get(`/clients/${clientId}/site-audit/${prevEntry.id}`) : Promise.resolve(null),
    ]);
    setSelectedResult(currentRes.data.result);
    setPreviousResult(previousRes ? previousRes.data.result : null);
    setPreviousMeta(prevEntry);
  }

  useEffect(() => {
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, refreshKey]);

  if (loading && history.length === 0) return <p style={{ fontSize: 13, color: "var(--text-muted)" }}>Loading history...</p>;
  if (error) return <p style={{ fontSize: 13, color: "#991b1b" }}>{error}</p>;
  if (history.length === 0) return null;

  const diff = selectedResult && previousResult ? diffIssues(selectedResult.issues || [], previousResult.issues || []) : null;

  return (
    <div style={{ marginTop: 20 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", borderBottom: "1px solid var(--border)", paddingBottom: 8, marginBottom: 16 }}>
        {history.map((h) => (
          <button
            key={h.id}
            onClick={() => selectRun(h.id)}
            className={h.id === selectedId ? "" : "secondary"}
            style={{ fontSize: 12, padding: "6px 10px" }}
          >
            {fmtWhen(h.created_at)}
            {h.issue_count > 0 ? ` · ${h.issue_count} issue${h.issue_count === 1 ? "" : "s"}` : " · clean"}
          </button>
        ))}
      </div>

      {diff && (diff.added.length > 0 || diff.resolved.length > 0) && (
        <div className="card" style={{ marginBottom: 16, padding: 14 }}>
          <p style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>
            Changes vs. previous run{previousMeta ? ` (${fmtWhen(previousMeta.created_at)})` : ""}
          </p>
          {diff.resolved.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <p style={{ margin: 0, fontSize: 12, color: "var(--success)", fontWeight: 600 }}>Resolved ({diff.resolved.length})</p>
              <ul style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 12.5 }}>
                {diff.resolved.map((i, idx) => <li key={idx}>{i}</li>)}
              </ul>
            </div>
          )}
          {diff.added.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <p style={{ margin: 0, fontSize: 12, color: "#991b1b", fontWeight: 600 }}>New ({diff.added.length})</p>
              <ul style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 12.5 }}>
                {diff.added.map((i, idx) => <li key={idx}>{i}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
      {diff && diff.added.length === 0 && diff.resolved.length === 0 && (
        <p style={{ fontSize: 12.5, color: "var(--text-muted)", marginBottom: 16 }}>No change in issues since the previous run.</p>
      )}

      {selectedResult && <SiteAuditReport result={selectedResult} clientId={clientId} gscConnected={gscConnected} />}
    </div>
  );
}
