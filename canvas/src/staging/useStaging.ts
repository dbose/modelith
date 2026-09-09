import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { previewChanges } from "../api";
import type { Exec } from "../exec";
import { newUlid } from "../ulid";
import type { Diagnostic, ModelDiffDoc, ModelDoc, PreviewDoc } from "../types";

/** One edit the user has made but not yet proposed. */
export interface PendingChange {
  op: string;
  payload: Record<string, unknown>;
  /** human-readable, for the review list */
  label: string;
  before: string;
  after: string;
  /** collapse identity. Two stagings with the same key replace each other —
   *  retyping a definition should be one change, not five. */
  key: string;
}

export interface Staging {
  pending: PendingChange[];
  /** the model as it would be AFTER the staged changes; null until first preview */
  previewDoc: ModelDoc | null;
  previewDiff: ModelDiffDoc | null;
  diagnostics: Diagnostic[];
  /** a staged change the server rejected, with its index */
  failure: { index: number; error: string } | null;
  busy: boolean;
  exec: Exec;
  drop: (key: string) => void;
  clear: () => void;
}

/** Ops that create something, and the payload keys their new ULIDs go in.
 *
 * `create_entity` mints two: a logical entity and the conceptual entity it
 * realises. `create_relationship` may also mint the foreign-key attribute it adds
 * to the child, so that gets an id too. */
const MINTS: Record<string, string[]> = {
  create_entity: ["id", "conceptual_id"],
  create_relationship: ["id", "from_fk_id"],
  create_subject_area: ["id"],
  create_term: ["id"],
  create_domain: ["id"],
  create_code_set: ["id"],
  create_key_group: ["id"],
  create_category: ["id"],
  add_attribute: ["id"],
};

export function withMintedIds(
  op: string,
  payload: Record<string, unknown>,
): Record<string, unknown> {
  const keys = MINTS[op];
  if (!keys) return payload;
  const next = { ...payload };
  for (const k of keys) {
    if (!next[k]) next[k] = newUlid();
  }
  return next;
}

/** Collapse identity for a change.
 *
 * The old rule was `op + label`, which silently DROPPED an edit: staging a
 * definition change on Trade and then on Counterparty collapsed into one. Keying on
 * the target ulid (and the attribute, where there is one) keeps them apart, while
 * still collapsing repeated edits of the same field.
 *
 * Creations never collapse — two new entities are two changes even before either
 * has a name.
 */
export function collapseKey(op: string, payload: Record<string, unknown>): string {
  if (op.startsWith("create_") || op === "add_attribute") {
    // the client minted the id, so it is already a stable per-creation identity
    return `${op}:${payload.id ?? payload.attribute_id}`;
  }
  const target = (payload.id ?? payload.entity_id ?? "") as string;
  const attr = (payload.attribute_id ?? "") as string;
  return attr ? `${op}:${target}:${attr}` : `${op}:${target}`;
}

/** Staging: edits are collected, previewed on the server, and proposed as one PR.
 *
 * `exec` has the same shape the architect canvas uses, so Inspector and the modals
 * work unchanged — they never learn whether their edit hit disk or a tray. */
export function useStaging({
  subjectArea,
  describe,
}: {
  subjectArea?: string;
  /** turn an op into review-list text; the caller knows the model, this hook does not */
  describe: (op: string, payload: Record<string, unknown>) => { label: string; before: string; after: string };
}): Staging {
  const [pending, setPending] = useState<PendingChange[]>([]);
  const [preview, setPreview] = useState<PreviewDoc | null>(null);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const exec: Exec = useCallback(
    async (op, payload) => {
      // Mint identities client-side for anything being created. Preview and propose
      // are two separate runs of the same change list, so a server-minted id would
      // differ between them: the entity the user sees would not be the entity in the
      // PR, and a create-then-edit batch would fail at submit. The server validates
      // whatever we send.
      payload = withMintedIds(op, payload);
      // An alignment made in a proposal is a PROPOSAL. Accepting one is an
      // architect's verdict (promote_alignment, which this surface cannot emit), so
      // an alignment staged here must not arrive pre-accepted — it would bypass the
      // proposed/accepted state machine entirely. The glossary editor already does
      // this; doing it in the staging layer covers the canvas AlignModal too.
      if (op === "set_alignment" && payload.status === undefined) {
        payload = { ...payload, status: "proposed" };
      }
      const key = collapseKey(op, payload);
      const { label, before, after } = describe(op, payload);
      setPending((prev) => {
        const next = prev.filter((c) => c.key !== key);
        // deleting something that was only ever staged cancels both
        if (op.startsWith("delete_")) {
          const target = (payload.id ?? payload.entity_id) as string | undefined;
          const wasCreated =
            target && prev.some((c) => c.op.startsWith("create_") && c.payload.id === target);
          if (wasCreated) return next.filter((c) => c.payload.id !== target);
        }
        return [...next, { op, payload, label, before, after, key }];
      });
      // the client minted any id, so hand it back the way /api/command would
      return { ok: true, fingerprint: "", created_id: (payload.id as string) ?? null, diagnostics: [] };
    },
    [describe],
  );

  // Preview after a short pause. Controls commit on blur or select-change rather
  // than per keystroke, so a debounce this short is invisible but still coalesces
  // a burst of edits into one round trip.
  useEffect(() => {
    if (!pending.length) {
      setPreview(null);
      return;
    }
    const t = setTimeout(() => {
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setBusy(true);
      previewChanges(
        pending.map((c) => ({ op: c.op, payload: c.payload })),
        subjectArea,
        ctrl.signal,
      )
        .then(setPreview)
        .catch((e) => {
          if ((e as Error).name !== "AbortError") setPreview(null);
        })
        .finally(() => setBusy(false));
    }, 250);
    return () => clearTimeout(t);
  }, [pending, subjectArea]);

  const drop = useCallback((key: string) => {
    setPending((prev) => prev.filter((c) => c.key !== key));
  }, []);

  const clear = useCallback(() => setPending([]), []);

  return useMemo(
    () => ({
      pending,
      previewDoc: preview?.model ?? null,
      previewDiff: preview?.diff ?? null,
      diagnostics: preview?.diagnostics ?? [],
      failure:
        preview && preview.failed_index !== null
          ? { index: preview.failed_index, error: preview.error ?? "invalid change" }
          : null,
      busy,
      exec,
      drop,
      clear,
    }),
    [pending, preview, busy, exec, drop, clear],
  );
}

/** Which staged changes cannot be unticked on their own.
 *
 * A change is *dependent* when it references a ULID that another staged change
 * created: unticking the create while keeping the edit would apply a subset that
 * fails at submit, after appearing fine in the app. The old rule disabled selective
 * proposal entirely whenever ANY create was staged, which was safe but blunt —
 * creating one entity blocked splitting a dozen unrelated definition edits.
 *
 * Returns the set of keys that must travel together with something else.
 */
export function dependentKeys(pending: PendingChange[]): Set<string> {
  const createdBy = new Map<string, string>(); // ulid -> key of the change that made it
  for (const c of pending) {
    for (const k of ["id", "conceptual_id", "from_fk_id"]) {
      const v = c.payload[k];
      if (c.op.startsWith("create_") && typeof v === "string") createdBy.set(v, c.key);
    }
  }
  if (!createdBy.size) return new Set();

  const locked = new Set<string>();
  for (const c of pending) {
    if (c.op.startsWith("create_")) continue;
    for (const v of Object.values(c.payload)) {
      if (typeof v === "string" && createdBy.has(v)) {
        locked.add(c.key); // the edit needs its creation
        locked.add(createdBy.get(v)!); // and the creation cannot leave without it
      }
    }
  }
  return locked;
}
