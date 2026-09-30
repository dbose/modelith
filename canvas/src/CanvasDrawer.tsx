import { IconClose, IconDomains } from "./icons";
import type { ModelDoc } from "./types";

/** Left slide-out drawer: turns the subject-area colour key into an interactive FILTER. Each
 *  row is a domain — colour chip, name, entity count, and a checkbox. Clicking a row focuses
 *  that single domain (canvas hides the rest and fits to it); the checkboxes multi-select a
 *  union. "Show all" clears the filter. This replaces the old static, wrapping legend that
 *  crammed the toolbar. The drawer stays mounted so it can slide in/out; `.open` drives the
 *  transform. */
export function CanvasDrawer({
  doc,
  saColors,
  countByArea,
  activeAreas,
  onSetActive,
  open,
  onClose,
}: {
  doc: ModelDoc;
  /** area id -> colour (shared with node colouring + minimap) */
  saColors: Map<string, string>;
  /** area id -> number of entities in it */
  countByArea: Map<string, number>;
  /** the set of area ids currently shown; empty = show all */
  activeAreas: Set<string>;
  /** replace the active set (focus = single, toggle = add/remove, clear = empty) */
  onSetActive: (next: Set<string>) => void;
  open: boolean;
  onClose: () => void;
}) {
  const areas = doc.subject_areas;
  const filtering = activeAreas.size > 0;

  const focus = (id: string) => onSetActive(new Set([id]));
  const toggle = (id: string) => {
    const next = new Set(activeAreas);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSetActive(next);
  };

  const unassigned = countByArea.get("") ?? 0;

  return (
    <aside className={"canvas-drawer" + (open ? " open" : "")} aria-hidden={!open}>
      <div className="drawer-header">
        <h2>
          <IconDomains size={16} /> Subject areas
        </h2>
        <button className="icon-btn" onClick={onClose} title="Close" aria-label="Close drawer">
          <IconClose size={16} />
        </button>
      </div>

      <div className="drawer-hint">
        {filtering
          ? `Showing ${activeAreas.size} of ${areas.length} — click a domain to focus, tick to add.`
          : "Click a domain to show only it. Tick several to compare."}
      </div>

      <button
        className={"sa-all" + (filtering ? "" : " active")}
        onClick={() => onSetActive(new Set())}
        disabled={!filtering}
      >
        Show all
      </button>

      <div className="sa-list">
        {areas.map((sa) => {
          const on = activeAreas.has(sa.id);
          const count = countByArea.get(sa.id) ?? sa.member_count ?? 0;
          return (
            <div
              key={sa.id}
              className={"sa-row" + (on ? " active" : "")}
              onClick={() => focus(sa.id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  focus(sa.id);
                }
              }}
              title={`Focus ${sa.name}`}
            >
              <input
                type="checkbox"
                checked={on}
                onClick={(e) => e.stopPropagation()}
                onChange={() => toggle(sa.id)}
                aria-label={`Include ${sa.name}`}
              />
              <span className="sa-chip" style={{ background: saColors.get(sa.id) }} />
              <span className="sa-name">{sa.name}</span>
              <span className="sa-count">{count}</span>
            </div>
          );
        })}
        {unassigned > 0 && (
          <div className="sa-row muted" title="Entities with no subject area">
            <span className="sa-chip none" />
            <span className="sa-name">No subject area</span>
            <span className="sa-count">{unassigned}</span>
          </div>
        )}
      </div>
    </aside>
  );
}
