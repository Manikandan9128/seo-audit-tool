import { useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useGuidedTour } from "./GuidedTour";

const SIDEBAR_HELP_DISMISSED_KEY = "sidebarHelpDismissed";

// Small line icons shown before each menu label (stroke = the link's text colour).
const NAV_ICONS: Record<string, ReactNode> = {
  "/clients": (
    <svg viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="7" height="9" rx="1.5" stroke="currentColor" strokeWidth="2" /><rect x="14" y="3" width="7" height="5" rx="1.5" stroke="currentColor" strokeWidth="2" /><rect x="14" y="12" width="7" height="9" rx="1.5" stroke="currentColor" strokeWidth="2" /><rect x="3" y="16" width="7" height="5" rx="1.5" stroke="currentColor" strokeWidth="2" /></svg>
  ),
  "/downloaded-reports": (
    <svg viewBox="0 0 24 24" fill="none"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
  ),
  "/settings": (
    <svg viewBox="0 0 24 24" fill="none"><path d="M4 7h10M18 7h2M4 17h2M10 17h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /><circle cx="16" cy="7" r="2" stroke="currentColor" strokeWidth="2" /><circle cx="8" cy="17" r="2" stroke="currentColor" strokeWidth="2" /></svg>
  ),
  "/team": (
    <svg viewBox="0 0 24 24" fill="none"><circle cx="9" cy="8" r="3.2" stroke="currentColor" strokeWidth="2" /><path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /><path d="M16 5.2a3.2 3.2 0 010 5.6M18 14.8c1.9.6 3 2.3 3 5.2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
  ),
  "/activity": (
    <svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" /><path d="M12 7v5l3 2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
  ),
  "/account": (
    <svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="8" r="3.5" stroke="currentColor" strokeWidth="2" /><path d="M5 20c0-3.6 3.1-6 7-6s7 2.4 7 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
  ),
};

const NAV_ITEMS = [
  { to: "/clients", label: "Dashboard" },
  { to: "/downloaded-reports", label: "Downloaded Reports" },
  { to: "/settings", label: "Settings" },
];

function isNavActive(pathname: string, to: string) {
  return to === "/clients" ? pathname.startsWith("/clients") : pathname.startsWith(to);
}

export default function Layout({ children }: { children: ReactNode }) {
  const { logout, isAuthenticated, isAdmin } = useAuth();
  const navItems = [
    ...NAV_ITEMS.filter((item) => item.to !== "/settings" || isAdmin),
    ...(isAdmin ? [{ to: "/team", label: "Team" }, { to: "/activity", label: "Activity Log" }] : []),
    { to: "/account", label: "Account" },
  ];
  const { pathname } = useLocation();
  const { start: startTour } = useGuidedTour();
  const [helpDismissed, setHelpDismissed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_HELP_DISMISSED_KEY) === "1";
    } catch {
      return false;
    }
  });

  function dismissHelp() {
    setHelpDismissed(true);
    try {
      localStorage.setItem(SIDEBAR_HELP_DISMISSED_KEY, "1");
    } catch {
      // ignore — worst case it reappears next visit
    }
  }

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
          {navItems.map((item) => {
            const active = isNavActive(pathname, item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                className={`nav-item${active ? " active" : ""}`}
                aria-current={active ? "page" : undefined}
              >
                {NAV_ICONS[item.to] && (
                  <span className="nav-icon" aria-hidden="true">
                    {NAV_ICONS[item.to]}
                  </span>
                )}
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* The "Report readiness" widget removed here (2026-09-24) —
            duplicated the on-page "Sections ready" KPI tile on the client
            page (ClientDetailPage.tsx); that one stays, this one doesn't
            add anything the client page doesn't already show. */}

        {!helpDismissed && (
          <div className="sidebar-help">
            <div className="sidebar-help-head">
              <span className="sidebar-help-title">New here?</span>
              <button
                type="button"
                className="sidebar-help-dismiss"
                onClick={dismissHelp}
                aria-label="Dismiss"
              >
                ×
              </button>
            </div>
            <p className="sidebar-help-text">
              Add a client, then generate a report from their page — download it once it's ready.
            </p>
            <button type="button" className="sidebar-help-tour-btn" onClick={startTour}>
              Take a quick tour
            </button>
          </div>
        )}

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
