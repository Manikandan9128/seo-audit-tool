import { useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api/client";
import { useAuth } from "./AuthContext";

const MIN_LENGTH = 8;

export default function AccountPage() {
  const { user } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setDone(false);
    if (next.length < MIN_LENGTH) {
      setError(`The new password must be at least ${MIN_LENGTH} characters.`);
      return;
    }
    if (next !== confirm) {
      setError("The two new passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/auth/change-password", { current_password: current, new_password: next });
      setDone(true);
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Could not change the password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ maxWidth: 480 }}>
      <h2 className="card-title">Account</h2>
      <p className="card-desc">
        {user ? `${user.full_name} · ${user.email}` : ""}. Change the password your super admin gave you.
      </p>
      <form onSubmit={submit}>
        <div style={{ marginBottom: 12 }}>
          <input type="password" placeholder="Current password" value={current} onChange={(e) => setCurrent(e.target.value)} required style={{ width: "100%" }} />
        </div>
        <div style={{ marginBottom: 12 }}>
          <input type="password" placeholder={`New password (at least ${MIN_LENGTH} characters)`} value={next} onChange={(e) => setNext(e.target.value)} required style={{ width: "100%" }} />
        </div>
        <div style={{ marginBottom: 16 }}>
          <input type="password" placeholder="Repeat new password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required style={{ width: "100%" }} />
        </div>
        {error && <p style={{ color: "var(--color-danger-text)", fontSize: 13 }}>{error}</p>}
        {done && <p style={{ color: "var(--color-success-text, #15803d)", fontSize: 13 }}>Password changed.</p>}
        <button type="submit" disabled={busy}>{busy ? "Saving..." : "Change password"}</button>
      </form>
    </div>
  );
}
