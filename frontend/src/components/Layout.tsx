import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export default function Layout({ children }: { children: ReactNode }) {
  const { logout, isAuthenticated } = useAuth();

  return (
    <div style={{ minHeight: "100vh" }}>
      {isAuthenticated && (
        <header
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "14px 32px",
            background: "#fff",
            borderBottom: "1px solid #e3e6ea",
          }}
        >
          <Link to="/clients" style={{ fontWeight: 700, fontSize: 16, color: "#1a1d21" }}>
            SEO Audit Tool
          </Link>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <Link to="/settings" style={{ fontSize: 14, color: "#1a1d21" }}>
              Settings
            </Link>
            <button className="secondary" onClick={logout}>
              Log out
            </button>
          </div>
        </header>
      )}
      <main style={{ padding: "32px 24px" }}>{children}</main>
    </div>
  );
}
