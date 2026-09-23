import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useReportReadiness } from "./ReportReadinessProvider";

const NAV_ICONS: Record<string, ReactNode> = {
  dashboard: (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="3" y="3" width="7" height="9" rx="1.5" stroke="currentColor" strokeWidth="2" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" stroke="currentColor" strokeWidth="2" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" stroke="currentColor" strokeWidth="2" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" stroke="currentColor" strokeWidth="2" />
    </svg>
  ),
  settings: (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2" />
      <path
        d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"
        stroke="currentColor"
        strokeWidth="2"
      />
    </svg>
  ),
};

const NAV_ITEMS = [
  { to: "/clients", label: "Dashboard", icon: "dashboard" },
  { to: "/settings", label: "Settings", icon: "settings" },
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
    <div className="app-shell">
      <aside className="sidebar">
        <Link to="/clients" className="brand">
          <span className="brand-mark" aria-hidden>SA</span>
          <span className="brand-name">SEO Audit Tool</span>
        </Link>

        <nav className="sidebar-nav">
          {NAV_ITEMS.map((item) => {
            const active = isNavActive(pathname, item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                className={`nav-item${active ? " active" : ""}`}
                aria-current={active ? "page" : undefined}
              >
                {NAV_ICONS[item.icon]}
                {item.label}
              </Link>
            );
          })}
        </nav>

        {readiness && (
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
        )}

        <div className="sidebar-footer">
          <button className="btn-logout" onClick={logout} aria-label="Log out">
            <svg viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              <path d="M16 17l5-5-5-5M21 12H9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Log out
          </button>
        </div>
      </aside>
      <main className="app-main">
        <div className="ambient-wash" aria-hidden />
        <div className="app-main-content">{children}</div>
      </main>
    </div>
  );
}
