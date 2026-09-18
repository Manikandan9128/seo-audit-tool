import { useRef, useState } from "react";
import type { ChangeEvent, DragEvent, KeyboardEvent } from "react";

// Replaces the bare native <input type="file"> with a drag-and-drop
// surface (point 10 of the redesign) — the native input is still what
// actually receives the files (kept functional underneath, just visually
// hidden), so the parent's existing onFiles/upload logic is untouched.
export default function Dropzone({
  onFiles,
  accept,
  multiple = true,
  hint,
  compact = false,
}: {
  onFiles: (files: File[]) => void;
  accept?: string;
  multiple?: boolean;
  hint?: string;
  compact?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  function openPicker() {
    inputRef.current?.click();
  }

  function handleChange(e: ChangeEvent<HTMLInputElement>) {
    onFiles(Array.from(e.target.files || []));
    // Allow re-selecting the exact same file(s) a second time.
    e.target.value = "";
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    onFiles(Array.from(e.dataTransfer.files || []));
  }

  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openPicker();
    }
  }

  return (
    <div
      className={`dropzone${compact ? " dropzone-compact" : ""}${dragOver ? " dragover" : ""}`}
      onClick={openPicker}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      role="button"
      tabIndex={0}
      aria-label={multiple ? "Drag files here, or browse to upload" : "Drag a file here, or browse to upload"}
      onKeyDown={handleKeyDown}
    >
      <div className="dropzone-left">
        <div className="dropzone-icon" aria-hidden>
          <svg viewBox="0 0 24 24" fill="none">
            <path d="M12 16V4m0 0L7 9m5-5l5 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M3 16v3a2 2 0 002 2h14a2 2 0 002-2v-3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </div>
        <div>
          <div className="dropzone-title">Drag {multiple ? "files" : "a file"} here, or browse</div>
          {hint && <div className="dropzone-sub">{hint}</div>}
        </div>
      </div>
      <div className="dropzone-actions" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="btn btn-secondary btn-sm" onClick={openPicker}>
          Browse {multiple ? "files" : ""}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          multiple={multiple}
          onChange={handleChange}
          style={{ display: "none" }}
          aria-hidden
          tabIndex={-1}
        />
      </div>
    </div>
  );
}
