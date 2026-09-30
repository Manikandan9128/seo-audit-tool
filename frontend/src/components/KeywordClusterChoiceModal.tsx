import { useState } from "react";

// Shown before Preview/Download when no manual keyword cluster file is
// uploaded for the client (2026-09-30, user-defined choice, no fallback):
// the user decides between their own file (asked to upload it first) and AI
// clusters built from Keyword Gap, Search Console / GA4 and Organic Positions.
export default function KeywordClusterChoiceModal({
  onUseAi,
  onGoUpload,
  onCancel,
}: {
  onUseAi: () => void;
  onGoUpload: () => void;
  onCancel: () => void;
}) {
  const [needsUpload, setNeedsUpload] = useState(false);

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 110, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}
      onClick={onCancel}
    >
      <div className="card" style={{ maxWidth: 520, width: "100%", display: "flex", flexDirection: "column", gap: 12 }} onClick={(e) => e.stopPropagation()}>
        {!needsUpload ? (
          <>
            <h3 style={{ margin: 0, fontSize: 18 }}>No keyword cluster file uploaded</h3>
            <p style={{ margin: 0, fontSize: 13 }}>
              How should the keyword clusters in this report be built? Only the source you choose is used. There is no
              automatic fallback.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <button className="btn btn-secondary" style={{ textAlign: "left" }} onClick={() => setNeedsUpload(true)}>
                <strong>Manual keyword cluster file</strong>
                <div className="muted" style={{ fontSize: 12 }}>Use my own uploaded cluster file.</div>
              </button>
              <button className="btn btn-primary" style={{ textAlign: "left" }} onClick={onUseAi}>
                <strong>AI generated clusters</strong>
                <div style={{ fontSize: 12 }}>Built from Keyword Gap, Search Console / GA4 and Organic Positions data.</div>
              </button>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button className="btn btn-secondary" onClick={onCancel}>Cancel</button>
            </div>
          </>
        ) : (
          <>
            <h3 style={{ margin: 0, fontSize: 18 }}>Upload your manual keyword cluster file</h3>
            <p style={{ margin: 0, fontSize: 13 }}>
              Upload the manual keyword cluster file (a sheet with a Keyword column and a Cluster column) on the
              Keyword Clusters tab, then generate the report again.
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button className="btn btn-secondary" onClick={() => setNeedsUpload(false)}>Back</button>
              <button className="btn btn-primary" onClick={onGoUpload}>Go to Keyword Clusters</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
