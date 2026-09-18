import { createContext, useContext, useState } from "react";
import type { ReactNode } from "react";

interface Readiness {
  ready: number;
  total: number;
}

interface ReportReadinessContextValue {
  readiness: Readiness | null;
  setReadiness: (r: Readiness | null) => void;
}

const ReportReadinessContext = createContext<ReportReadinessContextValue | null>(null);

// Sidebar readiness (point 7) needs data ClientDetailPage owns (which
// report sections currently have real data) but Layout — a route-level
// wrapper rendered as ClientDetailPage's own ancestor, not its parent's
// sibling — has no direct channel to read. A small context avoids prop-
// drilling through routes that don't care about it: ClientDetailPage
// writes its current ready/total counts in, Layout's sidebar reads them
// back out and renders nothing when null (i.e. on any page that isn't a
// client's own report).
export function useReportReadiness() {
  const ctx = useContext(ReportReadinessContext);
  if (!ctx) throw new Error("useReportReadiness must be used within ReportReadinessProvider");
  return ctx;
}

export default function ReportReadinessProvider({ children }: { children: ReactNode }) {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  return (
    <ReportReadinessContext.Provider value={{ readiness, setReadiness }}>
      {children}
    </ReportReadinessContext.Provider>
  );
}
