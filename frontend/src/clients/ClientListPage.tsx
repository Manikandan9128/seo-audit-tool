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

export default function ClientListPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [name, setName] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [showForm, setShowForm] = useState(false);

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

  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <h2 style={{ margin: 0 }}>Clients</h2>
        <button onClick={() => setShowForm(!showForm)}>{showForm ? "Cancel" : "+ Add client"}</button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="card" style={{ marginBottom: 20, display: "flex", gap: 10 }}>
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
        <div className="card" style={{ textAlign: "center", color: "#6b7280" }}>
          No clients yet. Add your first client to get started.
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {clients.map((c) => (
          <Link key={c.id} to={`/clients/${c.id}`} className="card" style={{ display: "block", color: "inherit" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontWeight: 600 }}>{c.name}</div>
                <div style={{ color: "#6b7280", fontSize: 13 }}>{c.website_url}</div>
              </div>
              <span className={`badge ${c.google_connected ? "success" : "muted"}`}>
                {c.google_connected ? "Google connected" : "Not connected"}
              </span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
