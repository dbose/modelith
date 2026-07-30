import { useCallback, useEffect, useState } from "react";
import {
  fetchDecisions,
  gitCommit,
  gitDiff,
  gitDiscard,
  gitStatus,
  ontologyCoverage,
  ontologySearch,
  ontologyStack,
  ontologyTerm,
  setDecisionVerdict,
} from "./api";
import type {
  CoverageDoc,
  Decision,
  GitStatus,
  StackDoc,
  TermCard,
  TermDetail,
} from "./types";

export type PanelTab = "ontology" | "layers" | "changes" | "decisions";

export function SidePanel({
  tab,
  onClose,
  readOnly,
  refreshKey,
  onModelChanged,
  onFocusEntity,
}: {
  tab: PanelTab;
  onClose: () => void;
  readOnly: boolean;
  refreshKey: number;
  onModelChanged: () => void;
  onFocusEntity: (id: string) => void;
}) {
  return (
    <aside className="side-panel">
      <div className="detail-header">
        <h2>
          {tab === "ontology" && "Ontology"}
          {tab === "layers" && "Ontology layers"}
          {tab === "changes" && "Uncommitted changes"}
          {tab === "decisions" && "Decision ledger"}
        </h2>
        <button className="icon-btn" onClick={onClose}>
          {"✕"}
        </button>
      </div>
      {tab === "ontology" && <OntologyBrowser />}
      {tab === "layers" && <LayersView refreshKey={refreshKey} onFocusEntity={onFocusEntity} />}
      {tab === "changes" && (
        <ChangesView readOnly={readOnly} refreshKey={refreshKey} onModelChanged={onModelChanged} />
      )}
      {tab === "decisions" && <DecisionsView readOnly={readOnly} />}
    </aside>
  );
}

// --- Ontology browser (E1) -----------------------------------------------------

function OntologyBrowser() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<TermCard[]>([]);
  const [meta, setMeta] = useState<{ loaded: number; vocabs: string[] }>({
    loaded: 0,
    vocabs: [],
  });
  const [detail, setDetail] = useState<TermDetail | null>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      ontologySearch(q || "a")
        .then((d) => {
          setResults(q ? d.results : []);
          setMeta({ loaded: d.loaded_terms, vocabs: d.vocabularies });
        })
        .catch(() => undefined);
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  const open = useCallback((ref: string) => {
    ontologyTerm(ref).then(setDetail).catch(() => undefined);
  }, []);

  if (detail) {
    return (
      <div className="panel-body">
        <button className="mini-btn" onClick={() => setDetail(null)}>
          {"← back"}
        </button>
        <h3 style={{ marginTop: 12 }}>{detail.label}</h3>
        <code className="term-iri">{detail.prefixed}</code>
        {detail.definition && <p className="term-def">{detail.definition}</p>}
        <div className="kv">
          <span className="k">source</span>
          <span className="v">{detail.source}</span>
        </div>
        {detail.broader.length > 0 && (
          <>
            <h4>broader</h4>
            {detail.broader.map((b) => (
              <button key={b.iri} className="rel-link" onClick={() => open(b.iri)}>
                {"↑ "}
                {b.label}
              </button>
            ))}
          </>
        )}
        {detail.narrower.length > 0 && (
          <>
            <h4>narrower</h4>
            {detail.narrower.map((n) => (
              <button key={n.iri} className="rel-link" onClick={() => open(n.iri)}>
                {"↓ "}
                {n.label}
              </button>
            ))}
          </>
        )}
      </div>
    );
  }

  return (
    <div className="panel-body">
      <input
        className="edit-input"
        placeholder="Search vocabulary terms…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <p className="meta-line">
        {meta.loaded > 0
          ? `${meta.loaded} terms loaded from: ${meta.vocabs.join(", ")}`
          : "no vocabulary loaded — declare ontology_stack in mdl-project.yaml or run `mdl ontology vendor fibo`"}
      </p>
      <div className="term-results">
        {results.map((t) => (
          <button key={t.iri} className="term-card" onClick={() => open(t.prefixed)}>
            <div className="term-head">
              <span className="term-label">{t.label}</span>
              <span className="term-source">{t.source}</span>
            </div>
            <code className="term-iri">{t.prefixed}</code>
            {t.definition && <p className="term-def">{t.definition}</p>}
          </button>
        ))}
      </div>
    </div>
  );
}

// --- Layers view (E1): the four-layer stack -------------------------------------

const LAYER_ORDER = ["industry", "core", "domain", "specialised"] as const;

function LayersView({
  refreshKey,
  onFocusEntity,
}: {
  refreshKey: number;
  onFocusEntity: (id: string) => void;
}) {
  const [stack, setStack] = useState<StackDoc | null>(null);
  const [coverage, setCoverage] = useState<CoverageDoc | null>(null);
  useEffect(() => {
    ontologyStack().then(setStack).catch(() => undefined);
    ontologyCoverage().then(setCoverage).catch(() => undefined);
  }, [refreshKey]);

  if (!stack) return <div className="panel-body">loading…</div>;
  const uncovered = new Set(coverage?.core_uncovered ?? []);

  return (
    <div className="panel-body">
      {coverage && (
        <div className={"coverage-banner" + (coverage.coverage_pct < 100 ? " warn" : "")}>
          industry alignment coverage: <b>{coverage.coverage_pct}%</b> (
          {coverage.core_with_industry}+{coverage.core_exempt}/{coverage.total_core} core terms)
        </div>
      )}
      <div className="layer-band external">
        <h4>industry (external vocabularies)</h4>
        {stack.external_terms.length === 0 && <p className="empty-hint">no aligned terms yet</p>}
        {stack.external_terms.map((t) => (
          <div key={t.iri} className="stack-term external" title={t.definition ?? undefined}>
            {t.label}
            <span className="term-source">{t.source}</span>
          </div>
        ))}
      </div>
      {LAYER_ORDER.slice(1).map((layer) => (
        <div key={layer} className="layer-band">
          <h4>{layer}</h4>
          {(stack.layers[layer] ?? []).length === 0 && <p className="empty-hint">—</p>}
          {(stack.layers[layer] ?? []).map((t) => (
            <button
              key={t.id}
              className={"stack-term" + (uncovered.has(t.name) ? " uncovered" : "")}
              title={t.definition ?? undefined}
              onClick={() => onFocusEntity(t.id)}
            >
              {t.name}
              {t.aligned_to && (
                <span className={"align-arrow" + (t.aligned_to.resolved ? "" : " unresolved")}>
                  {"↑ "}
                  {t.aligned_to.label}
                </span>
              )}
              {!t.aligned_to && !t.no_industry_equivalent && (
                <span className="align-arrow missing">unaligned</span>
              )}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

// --- Changes (git diff + commit panel) --------------------------------------------

function ChangesView({
  readOnly,
  refreshKey,
  onModelChanged,
}: {
  readOnly: boolean;
  refreshKey: number;
  onModelChanged: () => void;
}) {
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [diff, setDiff] = useState<string>("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    gitStatus().then(setStatus).catch(() => setStatus(null));
    gitDiff()
      .then((d) => setDiff([d.diff, ...d.untracked].filter(Boolean).join("\n\n")))
      .catch(() => setDiff(""));
  }, []);
  useEffect(reload, [reload, refreshKey]);

  if (!status?.git) return <div className="panel-body">model directory is not in a git repo</div>;
  if (status.clean) return <div className="panel-body ok">✓ working tree clean</div>;

  return (
    <div className="panel-body">
      <ul className="dirty-list">
        {status.dirty.map((f) => (
          <li key={f.path}>
            <code className="dirty-state">{f.state}</code> {f.path}
          </li>
        ))}
      </ul>
      <pre className="diff-view">
        {diff.split("\n").map((line, i) => (
          <span
            key={i}
            className={
              line.startsWith("+") && !line.startsWith("+++")
                ? "diff-add"
                : line.startsWith("-") && !line.startsWith("---")
                  ? "diff-del"
                  : line.startsWith("@@")
                    ? "diff-hunk"
                    : ""
            }
          >
            {line + "\n"}
          </span>
        ))}
      </pre>
      {!readOnly && (
        <>
          <input
            className="edit-input"
            placeholder="commit message…"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
          <div className="modal-footer">
            <button
              className="mini-btn danger"
              disabled={busy}
              onClick={() => {
                if (confirm("Discard ALL uncommitted model changes?")) {
                  setBusy(true);
                  gitDiscard().then(() => {
                    setBusy(false);
                    reload();
                    onModelChanged();
                  });
                }
              }}
            >
              Discard all
            </button>
            <button
              className="primary-btn"
              disabled={busy || !message.trim()}
              onClick={() => {
                setBusy(true);
                gitCommit(message).then(() => {
                  setBusy(false);
                  setMessage("");
                  reload();
                });
              }}
            >
              Commit
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// --- Decisions ledger ---------------------------------------------------------------

function DecisionsView({ readOnly }: { readOnly: boolean }) {
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const reload = useCallback(() => {
    fetchDecisions().then((d) => setDecisions(d.decisions)).catch(() => setDecisions([]));
  }, []);
  useEffect(reload, [reload]);

  if (decisions.length === 0)
    return <div className="panel-body">no recorded decisions (run `mdl reverse` to propose)</div>;

  const mark = { accepted: "✓", rejected: "✗", proposed: "?" } as const;
  return (
    <div className="panel-body">
      {decisions.map((d) => (
        <div key={d.signal_key} className={"decision " + d.verdict}>
          <div className="decision-head">
            <span className="decision-mark">{mark[d.verdict]}</span>
            <span className="chip">{d.confidence}</span>
            <span className="chip">{d.signal}</span>
          </div>
          <p className="decision-subject">{d.subject}</p>
          {!readOnly && d.verdict === "proposed" && (
            <div className="decision-actions">
              <button
                className="mini-btn"
                onClick={() => setDecisionVerdict(d.signal_key, "accepted").then(reload)}
              >
                Accept
              </button>
              <button
                className="mini-btn danger"
                onClick={() => setDecisionVerdict(d.signal_key, "rejected").then(reload)}
              >
                Reject
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
