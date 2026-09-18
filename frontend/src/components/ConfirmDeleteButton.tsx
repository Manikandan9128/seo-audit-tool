import { useState } from "react";

// Two-step inline delete confirm (spec point 4) — replaces window.confirm()
// dialogs across the app. Clicking the trash icon swaps it for a
// checkmark/x pair; only the checkmark actually calls onConfirm.
export default function ConfirmDeleteButton({
  label,
  onConfirm,
}: {
  label: string;
  onConfirm: () => void;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <span className={`delete-wrap${confirming ? " confirming" : ""}`}>
      <button
        type="button"
        className="btn-icon delete-trigger"
        aria-label={`Delete ${label}`}
        onClick={(e) => {
          e.stopPropagation();
          setConfirming(true);
        }}
      >
        <svg viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14z"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      <span className="delete-confirm">
        <button
          type="button"
          className="confirm-yes"
          aria-label={`Confirm delete ${label}`}
          onClick={(e) => {
            e.stopPropagation();
            setConfirming(false);
            onConfirm();
          }}
        >
          <svg viewBox="0 0 24 24" fill="none" aria-hidden>
            <path d="M20 6L9 17l-5-5" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <button
          type="button"
          className="confirm-no"
          aria-label="Cancel delete"
          onClick={(e) => {
            e.stopPropagation();
            setConfirming(false);
          }}
        >
          <svg viewBox="0 0 24 24" fill="none" aria-hidden>
            <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
          </svg>
        </button>
      </span>
    </span>
  );
}
