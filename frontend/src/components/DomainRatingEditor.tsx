import { useEffect, useState } from "react";
import { api } from "../api/client";
import ConfirmDeleteButton from "./ConfirmDeleteButton";

interface DomainRatingRow {
  id: string;
  domain: string;
  dr: number;
}

interface LiveDomainRatingRow {
  domain: string;
  dr: number | null;
  source: "ahrefs" | "manual" | "unavailable";
  is_own: boolean;
}

function domainInitials(d: string) {
  const name = normalizeDomain(d).split(".")[0] || d;
  return name.slice(0, 2).toUpperCase();
}

function normalizeDomain(d: string) {
  return d.replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/$/, "").toLowerCase();
}

const SOURCE_LABEL: Record<LiveDomainRatingRow["source"], string> = {
  ahrefs: "Ahrefs",
  manual: "Manual",
  unavailable: "No data",
};
const SOURCE_COLOR: Record<LiveDomainRatingRow["source"], string> = {
  ahrefs: "var(--color-success-text, #15803d)",
  manual: "var(--text-muted)",
  unavailable: "var(--color-danger-text, #b91c1c)",
};

export default function DomainRatingEditor({
  clientId, ownDomain, onChanged,
}: { clientId: string; ownDomain?: string; onChanged?: () => void }) {
  const [rows, setRows] = useState<DomainRatingRow[]>([]);
  const [liveRows, setLiveRows] = useState<LiveDomainRatingRow[]>([]);
  const [loadingLive, setLoadingLive] = useState(false);
  const [liveError, setLiveError] = useState("");
  const [domain, setDomain] = useState("");
  const [dr, setDr] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  async function load() {
    try {
      const res = await api.get(`/clients/${clientId}/domain-ratings`);
      setRows(res.data);
    } catch {
      // quiet — this panel is optional, the report just shows no DR if it fails to load
    }
  }

  async function loadLive() {
    setLoadingLive(true);
    setLiveError("");
    try {
      const res = await api.get(`/clients/${clientId}/domain-ratings/live`);
      setLiveRows(res.data);
    } catch (err: any) {
      setLiveError(err?.response?.data?.detail || "Couldn't fetch live Domain Rating");
    } finally {
      setLoadingLive(false);
    }
  }

  useEffect(() => {
    load();
    loadLive();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId]);

  async function save() {
    if (!domain.trim() || dr.trim() === "") return;
    const drNum = Number(dr);
    if (!Number.isFinite(drNum)) {
      setMsg("DR must be a number");
      return;
    }
    setSaving(true);
    setMsg("");
    try {
      await api.put(`/clients/${clientId}/domain-ratings`, { domain: domain.trim(), dr: drNum });
      setDomain("");
      setDr("");
      await Promise.all([load(), loadLive()]);
      onChanged?.();
    } catch (err: any) {
      setMsg(err?.response?.data?.detail || "Couldn't save");
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: string) {
    try {
      await api.delete(`/clients/${clientId}/domain-ratings/${id}`);
      setRows(rows.filter((r) => r.id !== id));
      await loadLive();
      onChanged?.();
    } catch (err: any) {
      setMsg(err?.response?.data?.detail || "Couldn't delete");
    }
  }

  const ownNorm = ownDomain ? normalizeDomain(ownDomain) : null;

  return (
    <div className="card">
      <div className="card-title-row">
        <div className="card-icon" aria-hidden>
          <svg viewBox="0 0 24 24" fill="none"><path d="M3 3v18h18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /><rect x="7" y="12" width="3" height="6" rx="1" fill="currentColor" /><rect x="12" y="8" width="3" height="10" rx="1" fill="currentColor" /><rect x="17" y="5" width="3" height="13" rx="1" fill="currentColor" /></svg>
        </div>
        <div className="card-title-text">
          <h3 className="card-title">Domain Rating</h3>
          <p className="card-desc">
            Pulled live from Ahrefs' free API for your own site and every competitor domain you've uploaded data
            for — this is exactly what the next report will use. A domain falls back to a manual entry below only
            when the live lookup fails (no Ahrefs key configured, rate limited, unknown domain).
          </p>
        </div>
        <button className="btn btn-secondary" onClick={loadLive} disabled={loadingLive} style={{ marginLeft: "auto" }}>
          {loadingLive ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {liveError && <p style={{ fontSize: 13, color: "var(--color-danger-text)", marginTop: "var(--sp-2)" }}>{liveError}</p>}

      {liveRows.length > 0 && (
        <ol className="leaderboard" aria-label="Domain Rating, highest first">
          {[...liveRows]
            .sort((a, b) => (b.dr ?? -1) - (a.dr ?? -1))
            .map((r, i) => {
              const isSelf = r.is_own || (ownNorm !== null && normalizeDomain(r.domain) === ownNorm);
              const rank = i + 1;
              return (
                <li key={r.domain} className={`lb-row${isSelf ? " self" : ""}`}>
                  <span className={`lb-rank${rank === 1 ? " gold" : rank <= 3 ? " top" : ""}`} aria-label={`Rank ${rank}`}>
                    {rank}
                  </span>
                  <span className={`lb-avatar${isSelf ? " self" : ""}`} aria-hidden>
                    {domainInitials(r.domain)}
                  </span>
                  <span className="lb-domain">
                    <span className="lb-domain-name">{r.domain}</span>
                    {isSelf && <span className="self-tag">Own site</span>}
                    <span style={{ fontSize: 11, marginLeft: 8, color: SOURCE_COLOR[r.source] }}>
                      {SOURCE_LABEL[r.source]}
                    </span>
                  </span>
                  <span className="lb-bar-wrap" aria-hidden>
                    <span
                      className={`lb-bar-fill${isSelf ? " self" : rank <= 3 ? " top" : ""}`}
                      style={{ width: `${Math.max(0, Math.min(100, r.dr ?? 0))}%` }}
                    />
                  </span>
                  <span className="lb-value">{r.dr ?? "—"}</span>
                </li>
              );
            })}
        </ol>
      )}

      <div className="card-title-row" style={{ marginTop: "var(--sp-4)" }}>
        <div className="card-title-text">
          <h3 className="card-title" style={{ fontSize: 15 }}>Manual override (fallback)</h3>
          <p className="card-desc">
            Only used for a domain where the live Ahrefs lookup fails. Look it up on Ahrefs' free Authority Checker
            and enter it here.
          </p>
        </div>
      </div>
      <div className="card-body" style={{ display: "flex", gap: "var(--sp-3)", alignItems: "center", flexWrap: "wrap" }}>
        <input
          type="text"
          placeholder="Domain (e.g. example.com)"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          style={{ width: 220 }}
        />
        <input
          type="number"
          placeholder="DR"
          value={dr}
          onChange={(e) => setDr(e.target.value)}
          style={{ width: 80 }}
        />
        <button className="btn btn-primary" onClick={save} disabled={saving || !domain.trim() || dr.trim() === ""}>
          {saving ? "Saving..." : "Add / Update"}
        </button>
      </div>
      {msg && <p style={{ fontSize: 13, color: "var(--color-danger-text)", marginTop: "var(--sp-2)" }}>{msg}</p>}

      {rows.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, marginTop: "var(--sp-3)", display: "flex", flexDirection: "column", gap: 6 }}>
          {rows.map((r) => (
            <li key={r.id} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13 }}>
              <span style={{ flex: 1 }}>{r.domain}</span>
              <span style={{ fontWeight: 600 }}>{r.dr}</span>
              <ConfirmDeleteButton label={r.domain} onConfirm={() => remove(r.id)} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
