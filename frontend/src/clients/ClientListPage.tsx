import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";

interface Client {
  id: string;
  name: string;
  website_url: string;
  google_connected: boolean;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

const AVATAR_PALETTE = ["#ff0000", "#0f766e", "#7c3aed", "#b45309", "#1d4ed8", "#be185d"];

function avatarColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return AVATAR_PALETTE[hash % AVATAR_PALETTE.length];
}

export default function ClientListPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [name, setName] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [query, setQuery] = useState("");

  async function load() {
    const res = await api.get("/clients");
    setClients(res.data);
  }

  useEffect(() => {
    load();
  }, []);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    await api.post("/clients", { name, website_url: websiteUrl });
    setName("");
    setWebsiteUrl("");
    setShowForm(false);
    load();
  }

  const filtered = clients.filter(
    (c) =>
      c.name.toLowerCase().includes(query.toLowerCase()) ||
      c.website_url.toLowerCase().includes(query.toLowerCase())
  );
  const connectedCount = clients.filter((c) => c.google_connected).length;

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          flexWrap: "wrap",
          gap: 16,
          marginBottom: 24,
        }}
      >
        <div>
          <p className="eyebrow" style={{ margin: "0 0 4px" }}>
            Portfolio
          </p>
          <h2 style={{ margin: 0 }}>Clients</h2>
          <p style={{ color: "var(--text-muted)", margin: "6px 0 0", fontSize: 13.5 }}>
            {clients.length} client{clients.length === 1 ? "" : "s"} · {connectedCount} with Google connected
          </p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {clients.length > 0 && (
            <input
              placeholder="Search clients..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ width: 220 }}
            />
          )}
          <button onClick={() => setShowForm(!showForm)}>{showForm ? "Cancel" : "+ Add client"}</button>
        </div>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="card" style={{ marginBottom: 24, display: "flex", gap: 10 }}>
          <input
            placeholder="Client name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            style={{ flex: 1 }}
          />
          <input
            placeholder="Website URL (https://...)"
            value={websiteUrl}
            onChange={(e) => setWebsiteUrl(e.target.value)}
            required
            style={{ flex: 1 }}
          />
          <button type="submit">Add</button>
        </form>
      )}

      {clients.length === 0 && !showForm && (
        <div className="card" style={{ textAlign: "center", color: "var(--text-muted)", padding: 48 }}>
          No clients yet. Add your first client to get started.
        </div>
      )}

      {clients.length > 0 && filtered.length === 0 && (
        <div className="card" style={{ textAlign: "center", color: "var(--text-muted)", padding: 32 }}>
          No clients match "{query}".
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
          gap: 16,
        }}
      >
        {filtered.map((c) => (
          <Link
            key={c.id}
            to={`/clients/${c.id}`}
            className="card client-tile"
            style={{ display: "flex", flexDirection: "column", gap: 16, color: "inherit" }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div
                style={{
                  width: 44,
                  height: 44,
                  borderRadius: 10,
                  background: avatarColor(c.name),
                  color: "#fff",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontFamily: "var(--font-display)",
                  fontWeight: 700,
                  fontSize: 16,
                  flexShrink: 0,
                }}
              >
                {initials(c.name)}
              </div>
              <span className={`badge ${c.google_connected ? "success" : "muted"}`}>
                {c.google_connected ? "Connected" : "Not connected"}
              </span>
            </div>
            <div>
              <div style={{ fontWeight: 600, fontSize: 16, fontFamily: "var(--font-display)" }}>{c.name}</div>
              <div style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 2 }}>{hostname(c.website_url)}</div>
            </div>
            <div
              style={{
                marginTop: "auto",
                paddingTop: 12,
                borderTop: "1px solid var(--border)",
                fontSize: 12.5,
                color: "var(--accent)",
                fontWeight: 600,
              }}
            >
              Open report →
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
