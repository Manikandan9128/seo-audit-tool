import { useEffect, useState } from "react";
import { api } from "../api/client";

interface LiveDomainRatingRow {
  domain: string;
  dr: number | null;
  source: "ahrefs" | "unavailable";
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
  unavailable: "Ahrefs lookup failed",
};
const SOURCE_COLOR: Record<LiveDomainRatingRow["source"], string> = {
  ahrefs: "var(--color-success-text, #15803d)",
  unavailable: "var(--color-danger-text, #b91c1c)",
};

export default function DomainRatingEditor({
  clientId, ownDomain,
}: { clientId: string; ownDomain?: string }) {
  const [liveRows, setLiveRows] = useState<LiveDomainRatingRow[]>([]);
  const [loadingLive, setLoadingLive] = useState(false);
  const [liveError, setLiveError] = useState("");

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
    loadLive();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId]);

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
            for. This is exactly what the next report will use. Ahrefs is the only source; if a lookup fails the
            report shows no DR for that domain and says why.
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

    </div>
  );
}
