import { useEffect, useMemo, useState } from "react";
import { expandSubjectArea, fetchSubjectArea, fetchSubjectAreas } from "../api";
import type { ClosureHop, GlossaryTerm } from "../types";
import { Pane } from "./SubjectAreaEditor";

/** Create a subject area and populate it in one gesture.
 *
 * A subject area is a filtered view, so an empty one is not useful — making the
 * user create it here and then go somewhere else to fill it is two steps for one
 * intention. This is the same Available/Included picker the Subject areas tab
 * uses, with the expansion preview, wrapped around the create. */
export function NewSubjectAreaModal({
  onCreate,
  onClose,
}: {
  /** name plus the members to seed it with; both staged as one proposal */
  onCreate: (name: string, definition: string, members: string[]) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState("");
  const [all, setAll] = useState<GlossaryTerm[]>([]);
  const [members, setMembers] = useState<string[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [direction, setDirection] = useState("ancestors");
  const [levels, setLevels] = useState(1);
  const [hops, setHops] = useState<ClosureHop[] | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [seedAreaId, setSeedAreaId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  // Every conceptual object in the model, from any existing area's detail — the
  // two panes are a partition of that set. Expansion needs a real area id to POST
  // against, so borrow the first one; it only reads the graph.
  useEffect(() => {
    fetchSubjectAreas()
      .then((d) => {
        const first = d.subject_areas[0];
        if (!first) return;
        setSeedAreaId(first.id);
        return fetchSubjectArea(first.id).then((detail) =>
          setAll([...detail.included, ...detail.available]),
        );
      })
      .catch((e) => setError(String(e)));
  }, []);

  const memberSet = useMemo(() => new Set(members), [members]);
  const q = filter.trim().toLowerCase();
  const match = (t: GlossaryTerm) => !q || t.name.toLowerCase().includes(q);
  const included = useMemo(
    () => members.map((id) => all.find((t) => t.id === id)).filter((t): t is GlossaryTerm => !!t && match(t)),
    [members, all, q],
  );
  const available = useMemo(
    () => all.filter((t) => !memberSet.has(t.id) && match(t)),
    [all, memberSet, q],
  );

  const move = (ids: string[], into: boolean) => {
    setMembers((cur) =>
      into ? [...cur, ...ids.filter((i) => !cur.includes(i))] : cur.filter((m) => !ids.includes(m)),
    );
    setPicked(new Set());
  };

  const toggle = (set: Set<string>, id: string, setter: (s: Set<string>) => void) => {
    const n = new Set(set);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    setter(n);
  };

  const runExpand = () => {
    const seeds = picked.size ? [...picked] : members;
    if (!seeds.length) {
      setError("pick an object to expand from first");
      return;
    }
    if (!seedAreaId) return;
    setError(null);
    expandSubjectArea(seedAreaId, { seeds, direction, levels })
      .then((d) => {
        const fresh = d.hops.filter((h) => !memberSet.has(h.id));
        setHops(fresh);
        setChosen(new Set(fresh.map((h) => h.id)));
      })
      .catch((e) => setError(String(e)));
  };

  const submit = () => {
    if (!name.trim()) return;
    onCreate(name.trim(), definition.trim(), members);
  };

  return (
    <div className="sme-modal-backdrop" onClick={onClose}>
      <div className="sme-modal sa-modal" onClick={(e) => e.stopPropagation()}>
        <h2>New subject area</h2>
        <p className="sme-hint">
          A subject area is a filtered view of the model. Name it and choose what it
          contains — both land as one proposal.
        </p>

        <div className="sme-field-row">
          <label className="sme-field">
            <span>Name</span>
            <input
              autoFocus
              value={name}
              placeholder="e.g. UseCase-ApraStressTesting"
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && members.length === 0 && submit()}
            />
          </label>
          <label className="sme-field">
            <span>Definition (optional)</span>
            <input
              value={definition}
              placeholder="what this view is for"
              onChange={(e) => setDefinition(e.target.value)}
            />
          </label>
        </div>

        <input
          className="sme-search sa-filter"
          type="search"
          placeholder="Filter both lists…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />

        <div className="sa-panes">
          <Pane
            title={`Available (${available.length})`}
            rows={available}
            picked={picked}
            onToggle={(id) => toggle(picked, id, setPicked)}
            homedHere={[]}
          />
          <div className="sa-controls">
            <button
              className="sme-secondary"
              disabled={!picked.size}
              onClick={() => move([...picked].filter((i) => !memberSet.has(i)), true)}
              title="Add the selected objects"
            >
              →
            </button>
            <button
              className="sme-secondary"
              disabled={!available.length}
              onClick={() => move(available.map((t) => t.id), true)}
              title="Add everything matching the filter"
            >
              »
            </button>
            <button
              className="sme-secondary"
              disabled={!picked.size}
              onClick={() => move([...picked].filter((i) => memberSet.has(i)), false)}
              title="Remove the selected objects"
            >
              ←
            </button>
            <button
              className="sme-secondary"
              disabled={!included.length}
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
            homedHere={[]}
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
            <button className="sme-secondary" onClick={runExpand}>
              Preview
            </button>
          </div>

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
                  <button
                    className="sme-primary"
                    disabled={!chosen.size}
                    onClick={() => {
                      move([...chosen], true);
                      setHops(null);
                    }}
                  >
                    Add {chosen.size}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {error && <p className="sme-error-line">{error}</p>}

        <div className="sme-modal-foot">
          <button className="sme-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="sme-primary" disabled={!name.trim()} onClick={submit}>
            Create{members.length > 0 && ` with ${members.length} object${members.length === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </div>
  );
}
