import { useState } from "react";

const WELCOME_PANEL_DISMISSED_KEY = "welcomePanelDismissed";

const STEPS = [
  { title: "Add a client", detail: "Their name and website URL is all you need to start." },
  { title: "Generate their report", detail: "Pick sections, connect data sources, run the build." },
  { title: "Download and send it", detail: "A ready PPTX, branded and structured for the client." },
];

export default function WelcomePanel() {
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(WELCOME_PANEL_DISMISSED_KEY) === "1";
    } catch {
      return false;
    }
  });

  if (dismissed) return null;

  function dismiss() {
    setDismissed(true);
    try {
      localStorage.setItem(WELCOME_PANEL_DISMISSED_KEY, "1");
    } catch {
      // ignore — worst case it reappears next visit
    }
  }

  return (
    <div className="card welcome-panel">
      <div className="welcome-panel-head">
        <div>
          <p className="eyebrow" style={{ margin: "0 0 4px" }}>
            Welcome
          </p>
          <h2 style={{ margin: 0 }}>Get your first report out in three steps</h2>
        </div>
        <button type="button" className="welcome-panel-dismiss" onClick={dismiss} aria-label="Dismiss">
          Got it
        </button>
      </div>
      <div className="welcome-panel-steps">
        {STEPS.map((s, i) => (
          <div key={s.title} className="welcome-panel-step">
            <span className="welcome-panel-step-num">{i + 1}</span>
            <div>
              <div className="welcome-panel-step-title">{s.title}</div>
              <div className="welcome-panel-step-detail">{s.detail}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
