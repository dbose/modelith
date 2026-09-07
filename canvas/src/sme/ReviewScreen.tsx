import { useCallback, useEffect, useState } from "react";
import { fetchClassification, fetchConflicts, fetchGitContext, fetchModelDiff } from "../api";
import type { ClassificationDoc, ConflictDoc, GitContext, ModelDiffDoc } from "../types";
import { DiffView } from "./DiffView";

/** The review step (plan §M1/M2/M6): what changed, who reviews it, does it still
 * merge, and only then submit. Replaces the old flow where the SME's only view of
 * their work was a flat before/after list in the submit dialog. */
export function ReviewScreen({
  user,
  onBack,
  onSubmit,
  /** ULIDs the SME has excluded from this proposal (selective proposal) */
  excluded,
  onToggleObject,
  selectable,
}: {
  user: string;
  onBack: () => void;
  onSubmit: (cl: ClassificationDoc | null) => void;
  excluded: Set<string>;
  onToggleObject: (ulid: string) => void;
  selectable: boolean;
}) {
  const [diff, setDiff] = useState<ModelDiffDoc | null>(null);
  const [cl, setCl] = useState<ClassificationDoc | null>(null);
  const [conf, setConf] = useState<ConflictDoc | null>(null);
  const [ctx, setCtx] = useState<GitContext | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchGitContext(user)
      .then((c) => {
        setCtx(c);
        const base = c.base_branch ?? "main";
        fetchModelDiff("HEAD")
          .then((d) => (d.ok ? setDiff(d) : setError(d.error ?? "could not read the model")))
          .catch((e) => setError(String(e)));
        fetchClassification("HEAD").then(setCl).catch(() => undefined);
        fetchConflicts(base).then(setConf).catch(() => undefined);
      })
      .catch((e) => setError(String(e)));
  }, [user]);

  useEffect(load, [load]);

  if (error) {
    return (
      <div className="rv-shell">
        <Head onBack={onBack} diff={null} />
        <p className="sme-splash error">
          <pre>{error}</pre>
        </p>
      </div>
    );
  }
  if (!diff) return <div className="sme-splash">◮ reading your changes…</div>;

  const selected = new Set(diff.objects.map((o) => o.ulid).filter((u) => !excluded.has(u)));

  return (
    <div className="rv-shell">
      <Head onBack={onBack} diff={diff} />
      <DiffView
        diff={diff}
        selectable={selectable}
        selected={selected}
        onToggle={onToggleObject}
      />
      <div className="rv-foot">
        <RoutePanel cl={cl} />
        <ConflictBanner conf={conf} ctx={ctx} />
        <div className="rv-actions">
          <button className="sme-secondary" onClick={onBack}>
            Keep editing
          </button>
          <button
            className="sme-primary"
            disabled={!diff.objects.length || (ctx ? !ctx.can_propose : false)}
            title={ctx && !ctx.can_propose ? "the working tree has uncommitted changes" : ""}
            onClick={() => onSubmit(cl)}
          >
            Submit for review →
          </button>
        </div>
      </div>
    </div>
  );
}

function Head({ onBack, diff }: { onBack: () => void; diff: ModelDiffDoc | null }) {
  const c = diff?.counts;
  return (
    <div className="rv-head">
      <div>
        <button className="rv-back" onClick={onBack}>
          ‹ Back to terms
        </button>
        <h1 className="rv-title">Review your changes</h1>
        {c && (
          <div className="rv-sub">
            {c.objects} object{c.objects === 1 ? "" : "s"} ·{" "}
            {(c.breaking ?? 0) + (c.additive ?? 0) + (c.cosmetic ?? 0)} changes
            {diff?.has_breaking && <span className="warn"> · ⚠ {c.breaking} breaking</span>} ·
            comparing to <code>{diff?.base.label}</code>
          </div>
        )}
      </div>
    </div>
  );
}

export function RoutePanel({ cl }: { cl: ClassificationDoc | null }) {
  if (!cl?.primary) return null;
  const reviewers = cl.reviewers_actual ?? cl.reviewers;
  return (
    <>
      <div className="rv-route">
        <span className="rv-route-badge">
          Route {cl.primary} · {cl.primary_name}
        </span>
        <span className="k">Reviewers</span>
        <span>{reviewers.join(", ")}</span>
        {cl.reviewers_actual && <span className="rv-src">from .github/CODEOWNERS</span>}
      </div>
      <div className="rv-gates">
        CI gates{" "}
        {cl.gates.map((g) => (
          <code key={g}>{g}</code>
        ))}
      </div>
    </>
  );
}

export function ConflictBanner({
  conf,
  ctx,
}: {
  conf: ConflictDoc | null;
  ctx: GitContext | null;
}) {
  if (!conf?.ok) return null;
  if (conf.clean) {
    return (
      <div className="rv-conflict ok">
        <span>✓</span>
        <span>Merges cleanly onto {conf.base}</span>
        {conf.behind > 0 && (
          <span className="detail">
            · {conf.base} is {conf.behind} commit{conf.behind === 1 ? "" : "s"} ahead
          </span>
        )}
        {ctx?.dirty && <span className="detail">· uncommitted changes in the working tree</span>}
      </div>
    );
  }
  return (
    <div className="rv-conflict bad">
      <span>⚠</span>
      <span>
        {conf.base} has changed and {conf.files.length} file
        {conf.files.length === 1 ? "" : "s"} now conflict
      </span>
      <span className="detail">{conf.files.map((f) => f.path).join(", ")}</span>
    </div>
  );
}
