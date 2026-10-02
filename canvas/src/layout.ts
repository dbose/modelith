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

/** Layout style. "hierarchical" is the dagre layered layout (good for small/medium star
 *  schemas). "clustered" groups entities by subject area, lays out each group with dagre
 *  (relationship-aware within a domain), then packs the groups — the readable choice for a
 *  large, domain-organised model where a single dagre stacks a constellation into a column
 *  and a blind grid ignores the FKs. "grid" is the plain packer. "auto" picks by size and
 *  whether the model actually has subject areas. */
export type LayoutMode = "auto" | "hierarchical" | "clustered" | "grid";

/** Flow direction for the dagre-based layouts (hierarchical + clustered): which way ranks
 *  advance. LR = left→right (default), TB = top→bottom, RL = right→left, BT = bottom→top.
 *  The grid packer and grid-packed clusters have no meaningful direction — the toggle is a
 *  no-op there. */
export type LayoutDir = "LR" | "TB" | "RL" | "BT";

// Above this many entities, "auto" leaves the plain dagre layout — a single layered pass
// stops paying its way (a constellation stacks into a tall column). With subject areas it
// switches to clustered; without them, to the grid packer.
const LARGE_THRESHOLD = 40;

/** Maps an entity id to its subject area (id + name) — resolved by the caller, which knows
 *  both the direct `conceptual.subject_area` pointer AND the inverted `subject_areas[].members`
 *  list (a reversed/erwin model carries membership only on the area, so the pointer is null
 *  there). The layout groups by `name`; entities absent from the map are "unassigned". */
export type AreaByEntity = Map<string, { id: string; name: string }>;

/** Route to the chosen layout. "auto" keeps dagre for small models (no regression) and,
 *  past the threshold, clusters by subject area when the model has them (the common case
 *  for a reversed/erwin model), else falls back to the grid packer. */
export function layoutGraph(
  nodes: Node[],
  edges: Edge[],
  entities: Map<string, Entity>,
  mode: LayoutMode = "auto",
  areaByEntity?: AreaByEntity,
  dir: LayoutDir = "LR",
): Node[] {
  const areaOf = makeAreaOf(entities, areaByEntity);
  if (mode === "grid") return gridLayout(nodes, entities, areaOf);
  if (mode === "clustered") return clusteredLayout(nodes, edges, entities, areaOf, dir);
  if (mode === "hierarchical") return hierarchicalLayout(nodes, edges, entities, dir);
  // auto
  if (nodes.length <= LARGE_THRESHOLD) return hierarchicalLayout(nodes, edges, entities, dir);
  const areas = new Set(nodes.map((n) => areaOf(n.id)).filter(Boolean));
  // clustering only helps when there is real domain structure (>1 area); otherwise grid.
  return areas.size > 1
    ? clusteredLayout(nodes, edges, entities, areaOf, dir)
    : gridLayout(nodes, entities, areaOf);
}

/** Build the entity→area-name lookup. Prefer a caller-supplied map (which resolves the
 *  inverted `members` membership); fall back to the entity's own `subject_area` pointer so
 *  callers that don't pass a map still group correctly. Returns "" for unassigned. */
function makeAreaOf(
  entities: Map<string, Entity>,
  areaByEntity?: AreaByEntity,
): (id: string) => string {
  return (id: string) => {
    if (areaByEntity) {
      const a = areaByEntity.get(id);
      if (a) return a.name;
    }
    return entities.get(id)?.conceptual?.subject_area?.name ?? "";
  };
}

/** Run one dagre layered pass over a set of nodes + the edges internal to them. Returns
 *  the nodes with top-left positions plus the laid-out bounding box (width/height), so a
 *  caller can pack several such boxes (clustered layout). `compact` tightens the spacing
 *  for a big graph. */
function dagrePass(
  nodes: Node[],
  edges: Edge[],
  entities: Map<string, Entity>,
  compact = false,
  dir: LayoutDir = "LR",
): { nodes: Node[]; width: number; height: number } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: dir,
    nodesep: compact ? 45 : 70,
    ranksep: compact ? 110 : 150,
    edgesep: compact ? 20 : 30,
    marginx: 24,
    marginy: 24,
    ranker: "network-simplex",
  });
  g.setDefaultEdgeLabel(() => ({}));

  const ids = new Set(nodes.map((n) => n.id));
  for (const n of nodes) {
    const ent = entities.get(n.id);
    g.setNode(n.id, { width: NODE_WIDTH, height: ent ? nodeHeight(ent) : 120 });
  }
  // only edges whose BOTH ends are in this set (a cluster's internal FKs)
  for (const e of edges) {
    if (ids.has(e.source) && ids.has(e.target)) g.setEdge(e.source, e.target, { minlen: 1, weight: 2 });
  }

  dagre.layout(g);

  let maxX = 0;
  let maxY = 0;
  const out = nodes.map((n) => {
    const pos = g.node(n.id);
    const ent = entities.get(n.id);
    const h = ent ? nodeHeight(ent) : 120;
    const x = pos.x - NODE_WIDTH / 2;
    const y = pos.y - h / 2;
    maxX = Math.max(maxX, x + NODE_WIDTH);
    maxY = Math.max(maxY, y + h);
    return { ...n, position: { x, y } };
  });
  return { nodes: out, width: maxX, height: maxY };
}

function hierarchicalLayout(
  nodes: Node[],
  edges: Edge[],
  entities: Map<string, Entity>,
  dir: LayoutDir = "LR",
): Node[] {
  return dagrePass(nodes, edges, entities, false, dir).nodes;
}

/** Pack a set of nodes into a compact grid (rows of ~sqrt columns), returning the nodes at
 *  top-left positions plus the bounding box — same contract as `dagrePass`, so a cluster can
 *  choose either. Used for a cluster whose entities have few internal FKs, where dagre would
 *  drop them all into one rank and stack a tall column. */
function gridPass(
  nodes: Node[],
  entities: Map<string, Entity>,
): { nodes: Node[]; width: number; height: number } {
  const heightOf = (id: string) => {
    const ent = entities.get(id);
    return ent ? nodeHeight(ent) : 120;
  };
  const cols = Math.max(1, Math.round(Math.sqrt(nodes.length)));
  const out: Node[] = [];
  let y = 0;
  let maxX = 0;
  for (let i = 0; i < nodes.length; i += cols) {
    const row = nodes.slice(i, i + cols);
    row.forEach((n, c) => {
      const x = c * (NODE_WIDTH + GRID_GAP_X);
      out.push({ ...n, position: { x, y } });
      maxX = Math.max(maxX, x + NODE_WIDTH);
    });
    y += Math.max(...row.map((n) => heightOf(n.id))) + GRID_GAP_Y;
  }
  return { nodes: out, width: maxX, height: y };
}

const CLUSTER_GAP = 120; // space between subject-area clusters

/** Group entities by subject area, lay each group out with dagre (so FK-connected entities
 *  within a domain read as a proper sub-diagram), then pack the group boxes into a grid of
 *  clusters. This keeps a domain's entities together AND relationship-aware, instead of
 *  stacking the whole constellation into one dagre column or scattering it across a blind
 *  grid. Entities with no subject area form a trailing "(unassigned)" cluster. */
function clusteredLayout(
  nodes: Node[],
  edges: Edge[],
  entities: Map<string, Entity>,
  areaOf: (id: string) => string,
  dir: LayoutDir = "LR",
): Node[] {
  // bucket nodes by area name (empty string = unassigned, sorted last)
  const byArea = new Map<string, Node[]>();
  for (const n of nodes) {
    const a = areaOf(n.id);
    (byArea.get(a) ?? byArea.set(a, []).get(a)!).push(n);
  }
  const areaNames = [...byArea.keys()].sort((a, b) => {
    if (a === "") return 1; // unassigned last
    if (b === "") return -1;
    return a.localeCompare(b);
  });

  // Lay out each cluster internally, capturing its box size. A small, well-connected cluster
  // reads best as a dagre sub-diagram (its FK chain is legible); but a larger domain, or one
  // whose relationships are mostly cross-domain, collapses into one dagre rank — a tall
  // column — so pack it as a compact grid instead. Use dagre only for a small cluster with a
  // real internal chain (few nodes, edges ≳ nodes); everything else grids.
  const clusters = areaNames.map((name) => {
    const members = byArea.get(name)!;
    const ids = new Set(members.map((n) => n.id));
    let internal = 0;
    for (const e of edges) if (ids.has(e.source) && ids.has(e.target)) internal++;
    const dagreWorthwhile = members.length <= 8 && internal >= members.length - 1;
    const laid = dagreWorthwhile
      ? dagrePass(members, edges, entities, true, dir)
      : gridPass(members, entities);
    return { name, ...laid };
  });

  // Pack cluster boxes into rows, advancing Y by the tallest box in a row. Bias toward more
  // columns than a plain sqrt so a handful of clusters spread across the canvas width rather
  // than stacking into a tall strip.
  const cols = Math.max(1, Math.ceil(Math.sqrt(clusters.length * 1.6)));
  const positioned: Node[] = [];
  let rowY = 0;
  for (let i = 0; i < clusters.length; i += cols) {
    const row = clusters.slice(i, i + cols);
    let colX = 0;
    let rowH = 0;
    for (const cl of row) {
      // shift the cluster's already-relative node positions to (colX, rowY)
      for (const n of cl.nodes) {
        positioned.push({ ...n, position: { x: n.position.x + colX, y: n.position.y + rowY } });
      }
      colX += cl.width + CLUSTER_GAP;
      rowH = Math.max(rowH, cl.height);
    }
    rowY += rowH + CLUSTER_GAP;
  }

  // preserve input order in the returned array (React Flow keys by id anyway)
  const posById = new Map(positioned.map((n) => [n.id, n.position]));
  return nodes.map((n) => ({ ...n, position: posById.get(n.id) ?? n.position }));
}

const GRID_GAP_X = 40;
const GRID_GAP_Y = 40;

/** Pack entities into a compact grid of roughly-square aspect. Cards have variable
 *  height (one row per attribute), so this packs row by row: fill a row up to the
 *  column count, then advance Y by the tallest card in that row. Entities are ordered
 *  by subject area (so an area's entities sit together) then by name, giving a stable,
 *  scannable arrangement where the layered layout would stack a dense schema. */
function gridLayout(
  nodes: Node[],
  entities: Map<string, Entity>,
  areaOf: (id: string) => string,
): Node[] {
  const heightOf = (id: string) => {
    const ent = entities.get(id);
    return ent ? nodeHeight(ent) : 120;
  };

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
