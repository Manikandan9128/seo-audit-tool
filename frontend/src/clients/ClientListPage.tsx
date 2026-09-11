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
    <div className="clients-page">
      <div className="clients-header">
        <div>
          <p className="eyebrow" style={{ margin: "0 0 4px" }}>
            Portfolio
          </p>
          <h2 style={{ margin: 0 }}>Clients</h2>
          <div className="clients-stats">
            <span className="stat-pill">
              <strong>{clients.length}</strong> client{clients.length === 1 ? "" : "s"}
            </span>
            <span className="stat-pill accent">
              <strong>{connectedCount}</strong> connected
            </span>
          </div>
        </div>
        <div className="clients-toolbar">
          {clients.length > 0 && (
            <label className="clients-search">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="7" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                placeholder="Search clients..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search clients"
              />
            </label>
          )}
          <button onClick={() => setShowForm(!showForm)}>{showForm ? "Cancel" : "+ Add client"}</button>
        </div>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="card add-client-form">
          <input
            placeholder="Client name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <input
            placeholder="Website URL (https://...)"
            value={websiteUrl}
            onChange={(e) => setWebsiteUrl(e.target.value)}
            required
          />
          <button type="submit">Add</button>
        </form>
      )}

      {clients.length === 0 && !showForm && (
        <div className="card clients-empty">
          <div className="icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
          </div>
          <p>No clients yet. Add your first client to get started.</p>
        </div>
      )}

      {clients.length > 0 && filtered.length === 0 && (
        <div className="card clients-empty" style={{ padding: 32 }}>
          <p>No clients match "{query}".</p>
        </div>
      )}

      <div className="clients-grid">
        {filtered.map((c) => (
          <Link key={c.id} to={`/clients/${c.id}`} className="card client-tile">
            <div className="client-tile-top">
              <div className="client-avatar" style={{ background: avatarColor(c.name) }}>
                {initials(c.name)}
              </div>
              <span className={`badge ${c.google_connected ? "success" : "muted"}`}>
                {c.google_connected ? "Connected" : "Not connected"}
              </span>
            </div>
            <div>
              <div className="client-name">{c.name}</div>
              <div className="client-host">{hostname(c.website_url)}</div>
            </div>
            <div className="client-tile-footer">
              <span>Open report</span>
              <span aria-hidden="true">→</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
