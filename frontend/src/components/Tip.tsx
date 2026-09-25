import type { ReactNode } from "react";

// Plain-English hint for a jargon label (Cyces editorial spec, NUE §4):
// dotted underline, a dark (#151517) tooltip on hover/focus. CSS-only
// (no JS positioning) so it works the same on hover and keyboard focus —
// see .jargon-tip / .jargon-tip-bubble in index.css.
export default function Tip({ text, children }: { text: string; children: ReactNode }) {
  return (
    <span className="jargon-tip" tabIndex={0}>
      {children}
      <span className="jargon-tip-bubble" role="tooltip">
        {text}
      </span>
    </span>
  );
}
