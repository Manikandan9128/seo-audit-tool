import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useReportReadiness } from "./ReportReadinessProvider";

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
  const { readiness } = useReportReadiness();

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

        {readiness && (
          <div style={{ padding: "0 12px" }}>
            <div className="sidebar-progress">
              <div className="sidebar-progress-top">
                <span className="sidebar-progress-label">Report readiness</span>
                <span className="sidebar-progress-frac">
                  {readiness.ready} / {readiness.total}
                </span>
              </div>
              <div className="sidebar-progress-track">
                <div
                  className="sidebar-progress-fill"
                  style={{ width: `${readiness.total > 0 ? (readiness.ready / readiness.total) * 100 : 0}%` }}
                />
              </div>
            </div>
          </div>
        )}

        <div style={{ padding: 12, borderTop: "1px solid var(--border)" }}>
          <button className="btn-logout" onClick={logout} aria-label="Log out">
            <svg viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              <path d="M16 17l5-5-5-5M21 12H9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Log out
          </button>
        </div>
      </aside>
      <main style={{ flex: 1, padding: "32px 24px", minWidth: 0 }}>{children}</main>
    </div>
  );
}
