import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "reactflow";
import type { Entity } from "./types";

// Node size must be estimated before layout: header + one row per attribute
// (erwin-style cards). Kept in sync with EntityNode.tsx CSS.
export const NODE_WIDTH = 264;
const HEADER_H = 44;
const ROW_H = 26;
const SECTION_PAD = 14;

export function nodeHeight(entity: Entity): number {
  const rows = Math.max(entity.attributes.length, 1);
  return HEADER_H + rows * ROW_H + SECTION_PAD;
}

/** Layout style. "hierarchical" is the dagre layered layout (the default, good for
 *  small/medium star schemas). "grid" packs entities into compact rows — far more
 *  scannable once a model has dozens of entities, where the layered layout collapses
 *  a dense constellation schema into a tall stack. "auto" picks by entity count. */
export type LayoutMode = "auto" | "hierarchical" | "grid";

// Above this many entities, "auto" uses the grid packer. A layered layout stops
// paying its way here: a reversed warehouse is a constellation (many facts each
// linking several dims), which dagre stacks rather than spreads.
const GRID_THRESHOLD = 40;

/** Route to the chosen layout. Default "auto" keeps today's dagre for small models
 *  (no regression) and switches to grid past the threshold. */
export function layoutGraph(
  nodes: Node[],
  edges: Edge[],
  entities: Map<string, Entity>,
  mode: LayoutMode = "auto",
): Node[] {
  const useGrid = mode === "grid" || (mode === "auto" && nodes.length > GRID_THRESHOLD);
  return useGrid ? gridLayout(nodes, entities) : hierarchicalLayout(nodes, edges, entities);
}

function hierarchicalLayout(
  nodes: Node[],
  edges: Edge[],
  entities: Map<string, Entity>,
): Node[] {
  const g = new dagre.graphlib.Graph();
  // Wider ranksep = clear vertical lanes between columns for edges to travel
  // through; larger nodesep + edgesep keeps parallel edges from overlapping the
  // cards. network-simplex gives tidier rank assignment for star schemas.
  g.setGraph({
    rankdir: "LR",
    nodesep: 70,
    ranksep: 150,
    edgesep: 30,
    marginx: 48,
    marginy: 48,
    ranker: "network-simplex",
  });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of nodes) {
    const ent = entities.get(n.id);
    g.setNode(n.id, { width: NODE_WIDTH, height: ent ? nodeHeight(ent) : 120 });
  }
  // minlen 2 pushes related entities at least two ranks apart, so the edge has a
  // full lane to route in rather than hugging a neighbouring card.
  for (const e of edges) g.setEdge(e.source, e.target, { minlen: 1, weight: 2 });

  dagre.layout(g);

  return nodes.map((n) => {
    const pos = g.node(n.id);
    const ent = entities.get(n.id);
    const h = ent ? nodeHeight(ent) : 120;
    return {
      ...n,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - h / 2 },
    };
  });
}

const GRID_GAP_X = 40;
const GRID_GAP_Y = 40;

/** Pack entities into a compact grid of roughly-square aspect. Cards have variable
 *  height (one row per attribute), so this packs row by row: fill a row up to the
 *  column count, then advance Y by the tallest card in that row. Entities are ordered
 *  by subject area (so an area's entities sit together) then by name, giving a stable,
 *  scannable arrangement where the layered layout would stack a dense schema. */
function gridLayout(nodes: Node[], entities: Map<string, Entity>): Node[] {
  const heightOf = (id: string) => {
    const ent = entities.get(id);
    return ent ? nodeHeight(ent) : 120;
  };
  const areaOf = (id: string) => entities.get(id)?.conceptual?.subject_area?.name ?? "";

  const ordered = [...nodes].sort((a, b) => {
    const aa = areaOf(a.id);
    const ba = areaOf(b.id);
    if (aa !== ba) return aa.localeCompare(ba);
    const an = entities.get(a.id)?.name ?? a.id;
    const bn = entities.get(b.id)?.name ?? b.id;
    return an.localeCompare(bn);
  });

  // Aim for a roughly square canvas: cols ≈ sqrt(n), which keeps a big model from
  // becoming a very wide strip or a very tall column.
  const cols = Math.max(1, Math.round(Math.sqrt(ordered.length)));
  const colX = Array.from({ length: cols }, (_, c) => c * (NODE_WIDTH + GRID_GAP_X));

  const positioned = new Map<string, { x: number; y: number }>();
  let y = 0;
  for (let i = 0; i < ordered.length; i += cols) {
    const row = ordered.slice(i, i + cols);
    row.forEach((n, c) => positioned.set(n.id, { x: colX[c], y }));
    const rowH = Math.max(...row.map((n) => heightOf(n.id)));
    y += rowH + GRID_GAP_Y;
  }

  return nodes.map((n) => ({ ...n, position: positioned.get(n.id) ?? n.position }));
}
