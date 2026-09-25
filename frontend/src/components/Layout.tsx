import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV_ITEMS = [
  { to: "/clients", label: "Dashboard" },
  { to: "/downloaded-reports", label: "Downloaded Reports" },
  { to: "/settings", label: "Settings" },
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
    <div className="app-shell">
      <aside className="sidebar">
        <Link to="/clients" className="brand">
          <span className="brand-mark" aria-hidden>SA</span>
          <span className="brand-name">
            SEO Audit
            <span className="brand-sub">by Cyces</span>
          </span>
        </Link>

        {/* Text-only nav (2026-09-24 Cyces reskin) — the active link is
            marked by a 2px orange left border + white text, not an icon. */}
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
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* The "Report readiness" widget removed here (2026-09-24) —
            duplicated the on-page "Sections ready" KPI tile on the client
            page (ClientDetailPage.tsx); that one stays, this one doesn't
            add anything the client page doesn't already show. */}

        {/* .btn-logout is pinned to its pre-v4 look on purpose (user rule,
            2026-09-23 redesign) — not restyled here even though this
            reskin's brief asks for an underlined "Sign out" link; see
            index.css's own comment on this class. */}
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
