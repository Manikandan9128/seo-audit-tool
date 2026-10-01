import { useEffect, useState } from "react";
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
        {isSuperAdmin ? "; change roles or deactivate an account here." : ". Only a super admin can change roles or deactivate accounts."}
      </p>
      {error && <p style={{ color: "var(--color-danger-text)", fontSize: 13 }}>{error}</p>}
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
                {isSuperAdmin ? (
                  <select value={u.role} onChange={(e) => changeRole(u.id, e.target.value)}>
                    <option value="super_admin">Super admin</option>
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
                    <button
                      type="button"
                      className="btn-outline btn-sm"
                      onClick={() => setActive(u, !u.is_active)}
                    >
                      {u.is_active ? "Deactivate" : "Reactivate"}
                    </button>
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
