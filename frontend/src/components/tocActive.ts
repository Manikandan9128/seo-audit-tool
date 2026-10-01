import type { MouseEvent } from "react";

// "On this page" nav: marks the clicked link active (cosmetic only). The
// link's own #anchor navigation is left alone.
export function markTocActive(e: MouseEvent<HTMLElement>) {
  const link = (e.target as HTMLElement).closest("a");
  if (!link) return;
  e.currentTarget.querySelectorAll("a").forEach((a) => a.classList.toggle("active", a === link));
}
