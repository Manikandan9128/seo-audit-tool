import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";

interface TeamUser {
  id: string;
  email: string;
  full_name: string;
  role: "super_admin" | "admin" | "member";
  is_active: boolean;
  created_at: string;
}

const ROLE_LABEL: Record<TeamUser["role"], string> = {
  super_admin: "Super admin",
  admin: "Admin",
  member: "Team member",
};

export default function TeamPage() {
  const { user, isSuperAdmin } = useAuth();
  const [users, setUsers] = useState<TeamUser[]>([]);
  const [error, setError] = useState("");
  const [newName, setNewName] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newRole, setNewRole] = useState<"admin" | "member">("member");
  const [creating, setCreating] = useState(false);
  // Shown once: the server keeps only a hash, so this is the only time the
  // super admin can read the generated password.
  const [issued, setIssued] = useState<{ name: string; email: string; password: string; reset: boolean } | null>(null);

  async function createUser(e: FormEvent) {
    e.preventDefault();
    setError("");
    setCreating(true);
    try {
      const res = await api.post("/users", { full_name: newName, email: newEmail, role: newRole });
      setIssued({ name: res.data.full_name, email: res.data.email, password: res.data.temporary_password, reset: false });
      setNewName("");
      setNewEmail("");
      setNewRole("member");
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Could not create the account");
    } finally {
      setCreating(false);
    }
  }

  async function resetPassword(target: TeamUser) {
    if (!window.confirm(`Generate a new password for ${target.full_name}? Their current password stops working.`)) return;
    setError("");
    try {
      const res = await api.post(`/users/${target.id}/reset-password`);
      setIssued({ name: target.full_name, email: target.email, password: res.data.temporary_password, reset: true });
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Could not reset the password");
    }
  }

  async function load() {
    try {
      setUsers((await api.get("/users")).data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Could not load team");
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function changeRole(id: string, role: string) {
    setError("");
    try {
      await api.patch(`/users/${id}/role`, { role });
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Could not change role");
    }
  }

  async function setActive(target: TeamUser, active: boolean) {
    if (!active && !window.confirm(`Deactivate ${target.full_name}? They will be signed out and can't sign in until you reactivate them. Their uploads and history are kept.`)) {
      return;
    }
    setError("");
    try {
      await api.patch(`/users/${target.id}/active`, { active });
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Could not update the account");
    }
  }

  return (
    <div className="card">
      <h2 className="card-title">Team</h2>
      <p className="card-desc">
        Everyone signs in with their own account. New sign-ups start as team members
        {isSuperAdmin ? "; the super admin creates every account here." : ". Only the super admin can create accounts, change roles or deactivate accounts."}
      </p>
      {error && <p style={{ color: "var(--color-danger-text)", fontSize: 13 }}>{error}</p>}
      {isSuperAdmin && (
        <form onSubmit={createUser} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", margin: "12px 0 16px" }}>
          <input placeholder="Full name" value={newName} onChange={(e) => setNewName(e.target.value)} required />
          <input type="email" placeholder="Email" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} required />
          <select value={newRole} onChange={(e) => setNewRole(e.target.value as "admin" | "member")}>
            <option value="member">Team member</option>
            <option value="admin">Admin</option>
          </select>
          <button type="submit" className="btn-primary btn-sm" disabled={creating}>
            {creating ? "Creating..." : "Add user"}
          </button>
        </form>
      )}
      {issued && (
        <div className="card" style={{ margin: "0 0 16px", borderColor: "var(--color-accent, #f97316)" }}>
          <strong>{issued.reset ? "New password for" : "Account created for"} {issued.name} ({issued.email})</strong>
          <p style={{ margin: "6px 0", fontSize: 13 }}>
            Copy this password now and send it to them privately. It is shown only once and cannot be looked up later.
            They can change it themselves under Account after signing in.
          </p>
          <code style={{ fontSize: 16, userSelect: "all" }}>{issued.password}</code>{" "}
          <button type="button" className="btn-outline btn-sm" onClick={() => navigator.clipboard?.writeText(issued.password)}>
            Copy
          </button>{" "}
          <button type="button" className="btn-outline btn-sm" onClick={() => setIssued(null)}>
            Done
          </button>
        </div>
      )}
      <table className="data-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Role</th>
            <th>Joined</th>
            <th>Status</th>
            {isSuperAdmin && <th></th>}
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id}>
              <td>{u.full_name}{u.id === user?.id ? " (you)" : ""}</td>
              <td>{u.email}</td>
              <td>
                {isSuperAdmin && u.role !== "super_admin" ? (
                  <select value={u.role} onChange={(e) => changeRole(u.id, e.target.value)}>
                    <option value="admin">Admin</option>
                    <option value="member">Team member</option>
                  </select>
                ) : (
                  ROLE_LABEL[u.role]
                )}
              </td>
              <td>{new Date(u.created_at).toLocaleDateString()}</td>
              <td style={u.is_active ? undefined : { color: "var(--color-danger-text)" }}>
                {u.is_active ? "Active" : "Deactivated"}
              </td>
              {isSuperAdmin && (
                <td>
                  {u.id !== user?.id && u.role !== "super_admin" && (
                    <>
                      <button
                        type="button"
                        className="btn-outline btn-sm"
                        onClick={() => setActive(u, !u.is_active)}
                      >
                        {u.is_active ? "Deactivate" : "Reactivate"}
                      </button>{" "}
                      <button type="button" className="btn-outline btn-sm" onClick={() => resetPassword(u)}>
                        Reset password
                      </button>
                    </>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
