interface TreemapNode {
  name: string;
  resource_bytes: number;
  unused_bytes: number;
  children: TreemapNode[];
}

interface LaidOutNode extends TreemapNode {
  x: number;
  y: number;
  w: number;
  h: number;
}

// Squarified treemap, same algorithm Lighthouse's own treemap viewer uses —
// keeps tiles closer to square (easier to read/label) than a naive slice-and-dice.
function squarify(nodes: TreemapNode[], x: number, y: number, w: number, h: number): LaidOutNode[] {
  const total = nodes.reduce((s, n) => s + n.resource_bytes, 0) || 1;
  const area = w * h;
  const out: LaidOutNode[] = [];
  let remaining = nodes.slice();
  let rx = x, ry = y, rw = w, rh = h;

  while (remaining.length) {
    const vertical = rw >= rh;
    const side = vertical ? rh : rw;
    let row: TreemapNode[] = [];
    let rowSum = 0;
    let bestWorst = Infinity;

    for (let i = 0; i < remaining.length; i++) {
      const candidate = [...row, remaining[i]];
      const sum = rowSum + remaining[i].resource_bytes;
      const rowArea = (sum / total) * area;
      const rowLen = rowArea / side;
      const worst = Math.max(
        ...candidate.map((n) => {
          const a = (n.resource_bytes / total) * area;
          const len = a / rowLen || 0;
          return Math.max(rowLen / len, len / rowLen);
        })
      );
      if (worst <= bestWorst || row.length === 0) {
        row = candidate;
        rowSum = sum;
        bestWorst = worst;
      } else {
        break;
      }
    }

    remaining = remaining.slice(row.length);
    const rowArea = (rowSum / total) * area;
    const rowLen = side ? rowArea / side : 0;

    let offset = 0;
    for (const n of row) {
      const a = (n.resource_bytes / total) * area;
      const len = rowLen ? a / rowLen : 0;
      if (vertical) {
        out.push({ ...n, x: rx, y: ry + offset, w: rowLen, h: len });
      } else {
        out.push({ ...n, x: rx + offset, y: ry, w: len, h: rowLen });
      }
      offset += len;
    }

    if (vertical) {
      rx += rowLen;
      rw -= rowLen;
    } else {
      ry += rowLen;
      rh -= rowLen;
    }
  }
  return out;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function unusedPct(node: TreemapNode) {
  return node.resource_bytes ? node.unused_bytes / node.resource_bytes : 0;
}

function tileColor(node: TreemapNode) {
  const pct = unusedPct(node);
  if (pct >= 0.5) return "#c0392b";
  if (pct >= 0.2) return "#e0972d";
  return "#2f6fed";
}

export default function ScriptTreemap({ nodes }: { nodes: TreemapNode[] }) {
  if (!nodes || nodes.length === 0) return null;

  const width = 960;
  const height = 480;
  const laidOut = squarify(nodes, 0, 0, width, height);
  const totalBytes = nodes.reduce((s, n) => s + n.resource_bytes, 0);

  return (
    <div style={{ marginTop: 8 }}>
      <p className="eyebrow" style={{ marginBottom: 8 }}>
        JS bundle treemap — {formatBytes(totalBytes)} total
      </p>
      <div style={{ position: "relative", width: "100%", maxWidth: width, aspectRatio: `${width} / ${height}` }}>
        {laidOut.map((n, i) => (
          <div
            key={`${n.name}-${i}`}
            title={`${n.name}\n${formatBytes(n.resource_bytes)} (${formatBytes(n.unused_bytes)} unused)`}
            style={{
              position: "absolute",
              left: `${(n.x / width) * 100}%`,
              top: `${(n.y / height) * 100}%`,
              width: `${(n.w / width) * 100}%`,
              height: `${(n.h / height) * 100}%`,
              background: tileColor(n),
              border: "1px solid rgba(255,255,255,0.4)",
              boxSizing: "border-box",
              overflow: "hidden",
              padding: 4,
              color: "#fff",
              fontSize: 11,
              lineHeight: 1.3,
            }}
          >
            {n.w > 60 && n.h > 24 && (
              <>
                <div style={{ fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {n.name.split("/").pop()}
                </div>
                <div style={{ opacity: 0.85 }}>{formatBytes(n.resource_bytes)}</div>
              </>
            )}
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 16, marginTop: 8, fontSize: 12, color: "var(--text-muted, #667)" }}>
        <span><span style={{ display: "inline-block", width: 10, height: 10, background: "#2f6fed", marginRight: 4 }} />&lt;20% unused</span>
        <span><span style={{ display: "inline-block", width: 10, height: 10, background: "#e0972d", marginRight: 4 }} />20–50% unused</span>
        <span><span style={{ display: "inline-block", width: 10, height: 10, background: "#c0392b", marginRight: 4 }} />&gt;50% unused</span>
      </div>
    </div>
  );
}
