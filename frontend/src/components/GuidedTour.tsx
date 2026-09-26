import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";

/** 3rd of the 3 deferred New User Experience items (2026-09-23) — the other
 * two (sidebar "New here?" box, Dashboard Welcome panel) shipped 2026-09-25.
 * A 5-step spotlight overlay walking a new user through the same real flow
 * those two describe: Add a client -> open it -> Generate Report -> Preview
 * -> Download. Manually triggered only (a button in WelcomePanel/the sidebar
 * help box) — never auto-starts, so it can't surprise an existing user.
 *
 * Steps 3-5 live on one client's detail page and are found by a `data-tour`
 * attribute already on the real button — never a copy of the UI, so the
 * tour can never drift out of sync with what the button actually says or
 * does. Preview/Download only render once a report has been generated at
 * least once (ClientDetailPage's own `hasGenerated` gate) — the overlay
 * degrades to a floating card with no spotlight cutout when a step's target
 * isn't mounted yet, rather than assuming it's always there.
 */

interface TourStep {
  id: string;
  /** Navigate here first if not already on it — only steps 1-2 need this;
   * steps 3-5 stay on whichever client page step 2 navigated to. */
  route?: string;
  target: string;
  title: string;
  body: string;
}

const STEPS: TourStep[] = [
  {
    id: "add-client",
    route: "/clients",
    target: "tour-add-client",
    title: "Add your first client",
    body: "Every report starts here — just a name and their website URL.",
  },
  {
    id: "open-client",
    route: "/clients",
    target: "tour-client-tile",
    title: "Open their workspace",
    body: "Click a client to reach data sources, report sections, and generation — all on one page.",
  },
  {
    id: "generate-report",
    target: "tour-generate-report",
    title: "Generate the report",
    body: "Pick which sections to include, then Generate Report — it pulls together crawl, analytics, and keyword data.",
  },
  {
    id: "preview-report",
    target: "tour-preview-report",
    title: "Preview before sending",
    body: "Once it's generated, Preview Report lets you review the company overview and competitor details before the final file.",
  },
  {
    id: "download-report",
    target: "tour-download-report",
    title: "Download and send",
    body: "Download Report (PPTX) gives you the finished, branded deck — ready to send to your client.",
  },
];

interface GuidedTourContextValue {
  active: boolean;
  start: () => void;
}

const GuidedTourContext = createContext<GuidedTourContextValue>({
  active: false,
  start: () => {},
});

export function useGuidedTour() {
  return useContext(GuidedTourContext);
}

export default function GuidedTourProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [tick, setTick] = useState(0);
  const navigate = useNavigate();
  const location = useLocation();

  const step = STEPS[stepIndex];

  const start = useCallback(() => {
    setStepIndex(0);
    setActive(true);
  }, []);

  const stop = useCallback(() => {
    setActive(false);
  }, []);

  // One navigate per step transition, driven by the step itself — not a
  // reaction to every location change (that would fight the user's own
  // navigation, or loop). Intentionally excludes `location`/`navigate` from
  // deps: this must fire exactly once per (active, stepIndex) change.
  useEffect(() => {
    if (!active) return;
    if (step.route && location.pathname !== step.route) {
      navigate(step.route);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, stepIndex]);

  // Polls for the current step's target mounting — handles the route
  // transition above, async client-list loads, and the Preview/Download
  // buttons that only exist once a report's been generated. Also re-checks
  // on scroll/resize so the spotlight tracks a moved/resized target
  // immediately rather than waiting for the next poll tick.
  useEffect(() => {
    if (!active) return;
    const bump = () => setTick((n) => n + 1);
    const id = setInterval(bump, 350);
    window.addEventListener("scroll", bump, true);
    window.addEventListener("resize", bump);
    return () => {
      clearInterval(id);
      window.removeEventListener("scroll", bump, true);
      window.removeEventListener("resize", bump);
    };
  }, [active, stepIndex]);

  useEffect(() => {
    if (!active) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") stop();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, stop]);

  const targetEl = active
    ? document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`)
    : null;
  const waitingForClient = active && step.id === "open-client" && !targetEl;
  // Referenced only to keep `tick` a real dependency of this computation
  // (it drives re-renders via setTick; `targetEl`/rect below must be
  // recomputed on each one, not memoized away).
  void tick;
  const rect = targetEl?.getBoundingClientRect() ?? null;

  function goNext() {
    if (stepIndex === STEPS.length - 1) {
      stop();
      return;
    }
    if (step.id === "open-client") {
      const tile = document.querySelector<HTMLElement>('[data-tour="tour-client-tile"]');
      const clientId = tile?.getAttribute("data-client-id");
      if (!clientId) return; // Next is disabled in this state too — belt and suspenders
      navigate(`/clients/${clientId}`);
    }
    setStepIndex((i) => i + 1);
  }

  function goBack() {
    setStepIndex((i) => Math.max(0, i - 1));
  }

  return (
    <GuidedTourContext.Provider value={{ active, start }}>
      {children}
      {active && (
        <GuidedTourOverlay
          step={step}
          stepNumber={stepIndex + 1}
          totalSteps={STEPS.length}
          rect={rect}
          waitingForClient={waitingForClient}
          onNext={goNext}
          onBack={stepIndex > 0 ? goBack : undefined}
          onSkip={stop}
          isLast={stepIndex === STEPS.length - 1}
        />
      )}
    </GuidedTourContext.Provider>
  );
}

function GuidedTourOverlay({
  step,
  stepNumber,
  totalSteps,
  rect,
  waitingForClient,
  onNext,
  onBack,
  onSkip,
  isLast,
}: {
  step: TourStep;
  stepNumber: number;
  totalSteps: number;
  rect: DOMRect | null;
  waitingForClient: boolean;
  onNext: () => void;
  onBack?: () => void;
  onSkip: () => void;
  isLast: boolean;
}) {
  const pad = 8;
  const cardWidth = 320;

  const spotlightStyle: CSSProperties | undefined = rect
    ? {
        top: rect.top - pad,
        left: rect.left - pad,
        width: rect.width + pad * 2,
        height: rect.height + pad * 2,
      }
    : undefined;

  let cardStyle: CSSProperties | undefined;
  if (rect) {
    const roomBelow = window.innerHeight - rect.bottom - pad - 16;
    const top = roomBelow > 180 ? rect.bottom + pad + 12 : Math.max(12, rect.top - pad - 12 - 180);
    const left = Math.min(Math.max(12, rect.left), window.innerWidth - cardWidth - 12);
    cardStyle = { top, left, width: cardWidth };
  }

  return (
    <div className="guided-tour-layer" role="dialog" aria-modal="true" aria-label="Guided tour">
      {spotlightStyle && <div className="guided-tour-spotlight" style={spotlightStyle} />}
      <div
        className={`guided-tour-card${rect ? "" : " guided-tour-card-floating"}`}
        style={cardStyle}
      >
        <div className="guided-tour-card-head">
          <span className="guided-tour-step-count">
            Step {stepNumber} of {totalSteps}
          </span>
          <button type="button" className="guided-tour-skip" onClick={onSkip}>
            Skip tour
          </button>
        </div>
        <h3 className="guided-tour-title">{step.title}</h3>
        <p className="guided-tour-body">{step.body}</p>
        {waitingForClient && (
          <p className="guided-tour-hint">Add a client above, then this step continues on its own.</p>
        )}
        {!rect && !waitingForClient && (
          <p className="guided-tour-hint">You'll see this highlighted once you reach that point.</p>
        )}
        <div className="guided-tour-actions">
          {onBack && (
            <button type="button" className="btn btn-secondary" onClick={onBack}>
              Back
            </button>
          )}
          <button type="button" className="btn btn-primary" onClick={onNext} disabled={waitingForClient}>
            {isLast ? "Finish" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
