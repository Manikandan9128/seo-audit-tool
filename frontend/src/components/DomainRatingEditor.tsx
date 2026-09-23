import { useEffect, useState } from "react";
import { api } from "../api/client";
import ConfirmDeleteButton from "./ConfirmDeleteButton";

interface DomainRatingRow {
  id: string;
  domain: string;
  dr: number;
}

function domainInitials(d: string) {
  const name = normalizeDomain(d).split(".")[0] || d;
  return name.slice(0, 2).toUpperCase();
}

function normalizeDomain(d: string) {
  return d.replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/$/, "").toLowerCase();
}

export default function DomainRatingEditor({
  clientId, ownDomain, onChanged,
}: { clientId: string; ownDomain?: string; onChanged?: () => void }) {
  const [rows, setRows] = useState<DomainRatingRow[]>([]);
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

  useEffect(() => {
    load();
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
      await load();
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
            Manually entered — look up each domain on Ahrefs' free Authority Checker and enter it here.
            Covers the DR column in Competitor Analysis for your own site and any competitor domain.
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
        <ol className="leaderboard" aria-label="Domain Rating, highest first">
          {[...rows]
            .sort((a, b) => b.dr - a.dr)
            .map((r, i) => {
              const isSelf = ownNorm !== null && normalizeDomain(r.domain) === ownNorm;
              const rank = i + 1;
              return (
                <li key={r.id} className={`lb-row${isSelf ? " self" : ""}`}>
                  <span className={`lb-rank${rank === 1 ? " gold" : rank <= 3 ? " top" : ""}`} aria-label={`Rank ${rank}`}>
                    {rank}
                  </span>
                  <span className={`lb-avatar${isSelf ? " self" : ""}`} aria-hidden>
                    {domainInitials(r.domain)}
                  </span>
                  <span className="lb-domain">
                    <span className="lb-domain-name">{r.domain}</span>
                    {isSelf && <span className="self-tag">Own site</span>}
                  </span>
                  <span className="lb-bar-wrap" aria-hidden>
                    <span
                      className={`lb-bar-fill${isSelf ? " self" : rank <= 3 ? " top" : ""}`}
                      style={{ width: `${Math.max(0, Math.min(100, r.dr))}%` }}
                    />
                  </span>
                  <span className="lb-value">{r.dr}</span>
                  <span className="lb-actions">
                    <ConfirmDeleteButton label={r.domain} onConfirm={() => remove(r.id)} />
                  </span>
                </li>
              );
            })}
        </ol>
      )}
    </div>
  );
}
