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

export function layoutGraph(nodes: Node[], edges: Edge[], entities: Map<string, Entity>): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 48, ranksep: 110, marginx: 40, marginy: 40 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of nodes) {
    const ent = entities.get(n.id);
    g.setNode(n.id, { width: NODE_WIDTH, height: ent ? nodeHeight(ent) : 120 });
  }
  for (const e of edges) g.setEdge(e.source, e.target);

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
