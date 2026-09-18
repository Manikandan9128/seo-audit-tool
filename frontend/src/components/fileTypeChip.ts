export function fileTypeChip(filename: string): { label: string; cls: string } {
  const ext = (filename.split(".").pop() || "").toLowerCase();
  if (ext === "csv" || ext === "tsv") return { label: ext.toUpperCase(), cls: "csv" };
  if (ext === "pdf") return { label: "PDF", cls: "pdf" };
  if (ext === "xls" || ext === "xlsx") return { label: "XLS", cls: "xls" };
  return { label: ext ? ext.slice(0, 4).toUpperCase() : "FILE", cls: "other" };
}
