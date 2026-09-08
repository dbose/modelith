import { useCallback, useEffect, useMemo, useState } from "react";
import { expandSubjectArea, fetchSubjectArea, fetchSubjectAreas } from "../api";
import type { Exec } from "../exec";
import type { ClosureHop, GlossaryTerm, SubjectAreaDetail, SubjectAreaRow } from "../types";

/** erwin's Subject Area Editor: assemble a scoped view of the model.
 *
 * Two panes and an expansion preview. Every membership change restages a single
 * `set_subject_area_members` carrying the whole list — the handler is a whole-list
 * replace, so one idempotent, order-independent change covers any number of clicks
 * rather than N add/remove ops the reviewer has to read. */
export function SubjectAreaEditor({
  exec,
  canEdit,
  /** members staged but not yet on disk, so the panes reflect the tray */
  stagedMembers,
}: {
  exec: Exec;
  canEdit: boolean;
  stagedMembers: Record<string, string[]>;
}) {
  const [areas, setAreas] = useState<SubjectAreaRow[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [detail, setDetail] = useState<SubjectAreaDetail | null>(null);
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [direction, setDirection] = useState("ancestors");
  const [levels, setLevels] = useState(1);
  const [hops, setHops] = useState<ClosureHop[] | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchSubjectAreas()
      .then((d) => {
        setAreas(d.subject_areas);
        setActiveId((cur) => cur || d.subject_areas[0]?.id || "");
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!activeId) return;
    setHops(null);
    setPicked(new Set());
    fetchSubjectArea(activeId).then(setDetail).catch((e) => setError(String(e)));
  }, [activeId]);

  /** The live member list: what is staged if anything, else what is on disk. */
  const members = useMemo(
    () => stagedMembers[activeId] ?? detail?.members ?? [],
    [stagedMembers, activeId, detail],
  );

  const byId = useMemo(() => {
    const m = new Map<string, GlossaryTerm>();
    for (const t of [...(detail?.included ?? []), ...(detail?.available ?? [])]) m.set(t.id, t);
    return m;
  }, [detail]);

  const q = filter.trim().toLowerCase();
  const match = (t: GlossaryTerm) => !q || t.name.toLowerCase().includes(q);
  const memberSet = useMemo(() => new Set(members), [members]);
  const included = useMemo(
    () => members.map((id) => byId.get(id)).filter((t): t is GlossaryTerm => !!t && match(t)),
    [members, byId, q],
  );
  const available = useMemo(
    () => [...byId.values()].filter((t) => !memberSet.has(t.id) && match(t)),
    [byId, memberSet, q],
  );

  const restage = useCallback(
    (next: string[]) => {
      if (!detail) return;
      const added = next.filter((m) => !(detail.members ?? []).includes(m)).length;
      const removed = (detail.members ?? []).filter((m) => !next.includes(m)).length;
      exec("set_subject_area_members", {
        id: detail.id,
        members: next,
        // carried for the review list; the handler ignores extras
        __label: `${detail.name}: ${next.length} objects (+${added} −${removed})`,
      });
    },
    [detail, exec],
  );

  const move = (ids: string[], into: boolean) => {
    const next = into
      ? [...members, ...ids.filter((i) => !memberSet.has(i))]
      : members.filter((m) => !ids.includes(m));
    restage(next);
    setPicked(new Set());
  };

  const toggle = (set: Set<string>, id: string, setter: (s: Set<string>) => void) => {
    const n = new Set(set);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    setter(n);
  };

  const runExpand = () => {
    if (!detail) return;
    const seeds = picked.size ? [...picked] : members;
    if (!seeds.length) {
      setError("add an object first, or select one to expand from");
      return;
    }
    setError(null);
    expandSubjectArea(detail.id, { seeds, direction, levels })
      .then((d) => {
        setHops(d.hops);
        setChosen(new Set(d.added));
      })
      .catch((e) => setError(String(e)));
  };

  const confirmExpand = () => {
    move([...chosen], true);
    setHops(null);
  };

  if (error && !detail) return <p className="sme-splash error">{error}</p>;
  if (!areas.length) return <p className="sme-placeholder">No subject areas yet.</p>;

  const inconsistent = (detail?.inconsistent ?? []).filter((id) => !memberSet.has(id));

  return (
    <div className="sa-editor">
      <nav className="sme-nav">
        {areas.map((a) => (
          <button
            key={a.id}
            className={"sme-sa" + (a.id === activeId ? " active" : "")}
            onClick={() => setActiveId(a.id)}
            title={a.definition ?? undefined}
          >
            {a.name}
            <span className="sme-sa-count">
              {(stagedMembers[a.id] ?? a.members).length}
            </span>
          </button>
        ))}
      </nav>

      <div className="sa-main">
        <div className="sa-head">
          <h2>{detail?.name ?? "…"}</h2>
          {detail?.definition && <p className="sme-muted">{detail.definition}</p>}
          <input
            className="sme-search sa-filter"
            type="search"
            placeholder="Filter both lists…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>

        {inconsistent.length > 0 && canEdit && (
          <div className="sa-nudge">
            {inconsistent.length} object{inconsistent.length > 1 ? "s are" : " is"} homed in
            this area but not included.
            <button className="sme-link" onClick={() => move(inconsistent, true)}>
              Add {inconsistent.length > 1 ? "them all" : "it"}
            </button>
          </div>
        )}

        <div className="sa-panes">
          <Pane
            title={`Available (${available.length})`}
            rows={available}
            picked={picked}
            onToggle={(id) => toggle(picked, id, setPicked)}
            homedHere={detail?.homed_here ?? []}
          />
          <div className="sa-controls">
            <button
              className="sme-secondary"
              disabled={!canEdit || !picked.size}
              onClick={() => move([...picked].filter((i) => !memberSet.has(i)), true)}
              title="Add the selected objects"
            >
              →
            </button>
            <button
              className="sme-secondary"
              disabled={!canEdit || !available.length}
              onClick={() => move(available.map((t) => t.id), true)}
              title="Add everything matching the filter"
            >
              »
            </button>
            <button
              className="sme-secondary"
              disabled={!canEdit || !picked.size}
              onClick={() => move([...picked].filter((i) => memberSet.has(i)), false)}
              title="Remove the selected objects"
            >
              ←
            </button>
            <button
              className="sme-secondary"
              disabled={!canEdit || !included.length}
              onClick={() => move(included.map((t) => t.id), false)}
              title="Remove everything matching the filter"
            >
              «
            </button>
          </div>
          <Pane
            title={`Included (${included.length})`}
            rows={included}
            picked={picked}
            onToggle={(id) => toggle(picked, id, setPicked)}
            homedHere={detail?.homed_here ?? []}
          />
        </div>

        <div className="sa-expand">
          <h3>Add related objects</h3>
          <div className="sa-expand-row">
            <select value={direction} onChange={(e) => setDirection(e.target.value)}>
              <option value="ancestors">ancestors — the parents its keys point at</option>
              <option value="descendants">descendants — what points at it</option>
              <option value="both">both</option>
            </select>
            <label>
              levels
              <input
                type="number"
                min={1}
                max={10}
                value={levels}
                onChange={(e) => setLevels(Math.max(1, Math.min(10, Number(e.target.value))))}
              />
            </label>
            <button className="sme-secondary" onClick={runExpand} disabled={!canEdit}>
              Preview
            </button>
          </div>
          {error && <p className="sme-error-line">{error}</p>}

          {hops && (
            <div className="sa-hops">
              {hops.length === 0 && <p className="sme-muted">nothing new to add</p>}
              {hops.map((h) => (
                <label key={h.id} className="sa-hop">
                  <input
                    type="checkbox"
                    checked={chosen.has(h.id)}
                    onChange={() => toggle(chosen, h.id, setChosen)}
                  />
                  <span className="sa-hop-name">{h.name}</span>
                  {/* WHY it was reached — the difference between a picker you can
                      reason about and a black box */}
                  <span className="sa-hop-why">
                    via {h.via_name} · {h.direction.replace(/s$/, "")}, level {h.level}
                  </span>
                </label>
              ))}
              {hops.length > 0 && (
                <div className="sa-hop-actions">
                  <button className="sme-secondary" onClick={() => setHops(null)}>
                    Cancel
                  </button>
                  <button className="sme-primary" onClick={confirmExpand} disabled={!chosen.size}>
                    Add {chosen.size} object{chosen.size === 1 ? "" : "s"}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Pane({
  title,
  rows,
  picked,
  onToggle,
  homedHere,
}: {
  title: string;
  rows: GlossaryTerm[];
  picked: Set<string>;
  onToggle: (id: string) => void;
  homedHere: string[];
}) {
  const homed = useMemo(() => new Set(homedHere), [homedHere]);
  return (
    <div className="sa-pane">
      <h3>{title}</h3>
      <ul>
        {rows.map((t) => (
          <li
            key={t.id}
            className={"sa-row" + (picked.has(t.id) ? " picked" : "")}
            onClick={() => onToggle(t.id)}
          >
            <input type="checkbox" readOnly checked={picked.has(t.id)} />
            <span className="sa-row-name">{t.name}</span>
            {/* an object may be borrowed from another area — say which are native */}
            {homed.has(t.id) ? (
              <span className="sme-badge">home</span>
            ) : (
              <span className="sme-badge borrowed">borrowed</span>
            )}
          </li>
        ))}
        {!rows.length && <li className="sme-empty">nothing here</li>}
      </ul>
    </div>
  );
}
