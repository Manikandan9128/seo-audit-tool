import { useEffect, useState } from "react";
import { api } from "../api/client";

interface DomainRatingRow {
  id: string;
  domain: string;
  dr: number;
}

export default function DomainRatingEditor({ clientId }: { clientId: string }) {
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
    } catch (err: any) {
      setMsg(err?.response?.data?.detail || "Couldn't delete");
    }
  }

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Domain Rating</h3>
      <p style={{ color: "#6b7280", fontSize: 13 }}>
        Manually entered — look up each domain on Ahrefs' free Authority Checker and enter it here.
        Covers the DR column in Competitor Analysis for your own site and any competitor domain.
      </p>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
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
        <button onClick={save} disabled={saving || !domain.trim() || dr.trim() === ""}>
          {saving ? "Saving..." : "Add / Update"}
        </button>
      </div>
      {msg && <p style={{ fontSize: 13, color: "#991b1b", marginTop: 8 }}>{msg}</p>}

      {rows.length > 0 && (
        <table style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>Domain</th>
              <th style={{ textAlign: "left" }}>DR</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.domain}</td>
                <td>{r.dr}</td>
                <td>
                  <button className="secondary" onClick={() => remove(r.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
