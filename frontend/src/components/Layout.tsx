import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV_ITEMS = [
  { to: "/clients", label: "Dashboard", icon: "⌂" },
  { to: "/settings", label: "Settings", icon: "⚙" },
];

function isNavActive(pathname: string, to: string) {
  return to === "/clients" ? pathname.startsWith("/clients") : pathname.startsWith(to);
}

export default function Layout({ children }: { children: ReactNode }) {
  const { logout, isAuthenticated } = useAuth();
  const { pathname } = useLocation();

  if (!isAuthenticated) {
    return <main style={{ padding: "32px 24px" }}>{children}</main>;
  }

  return (
    <div style={{ minHeight: "100vh", display: "flex" }}>
      <aside
        style={{
          width: 220,
          flexShrink: 0,
          display: "flex",
          flexDirection: "column",
          background: "var(--surface)",
          borderRight: "1px solid var(--border)",
          position: "sticky",
          top: 0,
          height: "100vh",
        }}
      >
        <div style={{ padding: "22px 20px 18px" }}>
          <Link
            to="/clients"
            style={{
              fontWeight: 700,
              fontSize: 17,
              color: "var(--text)",
              fontFamily: "var(--font-display)",
              letterSpacing: "-0.01em",
              textDecoration: "none",
            }}
          >
            SEO Audit Tool
          </Link>
        </div>

        <nav style={{ flex: 1, padding: "4px 12px", display: "flex", flexDirection: "column", gap: 2 }}>
          {NAV_ITEMS.map((item) => {
            const active = isNavActive(pathname, item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "9px 12px",
                  borderRadius: 8,
                  fontSize: 14,
                  fontWeight: active ? 600 : 500,
                  color: active ? "var(--accent)" : "var(--text)",
                  background: active ? "var(--accent-soft)" : "transparent",
                  textDecoration: "none",
                }}
              >
                <span aria-hidden style={{ width: 16, textAlign: "center" }}>{item.icon}</span>
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div style={{ padding: 12, borderTop: "1px solid var(--border)" }}>
          <button
            className="secondary"
            onClick={logout}
            style={{ width: "100%", justifyContent: "center", display: "flex" }}
          >
            Log out
          </button>
        </div>
      </aside>
      <main style={{ flex: 1, padding: "32px 24px", minWidth: 0 }}>{children}</main>
    </div>
  );
}
