import { RAW_GIT_DIFF } from "./fixtures";

/** M4 — the same rename, both ways.
 *
 * Modelith keys objects by ULID, and `rename_entity` mutates `name:` in place
 * while `id:` never moves — so a rename is ONE cosmetic field change. Because
 * filenames are slugged from names the file also moves, which is why the raw git
 * diff is a whole-file delete + add. Erwin compares by NAME, so it shows the
 * same rename as an entity removed plus an entity added, with every attribute
 * duplicated on both sides. */
export function RenameCompare() {
  return (
    <div className="sme" style={{ height: "100%" }}>
      <header className="sme-top">
        <div className="sme-brand">
          <span className="sme-logo">◮</span>
          <span>Glossary</span>
          <span className="sme-project">pension_ibor</span>
        </div>
      </header>

      <div className="rv-head">
        <div>
          <h1 className="rv-title">Renaming Counterparty → Legal Entity</h1>
          <div className="rv-sub">the same change, seen two ways</div>
        </div>
      </div>

      <div className="cmp">
        <div className="cmp-col win">
          <h3>Modelith — semantic, ULID-keyed</h3>
          <div className="rv-obj active" style={{ cursor: "default" }}>
            <div className="rv-obj-head">
              <span className="rv-obj-name">Counterparty</span>
              <span className="rv-obj-kind">conceptual entity</span>
              <span className="sev renamed">renamed</span>
            </div>
            <ul className="rv-obj-fields">
              <li>Renamed → Legal Entity</li>
            </ul>
          </div>
          <div className="cmp-verdict">
            <strong>1 change. 0 other differences.</strong>
            <br />
            All 9 attributes, both relationships and the stewardship block are unchanged —
            the ULID <code style={{ fontFamily: "var(--mono)", fontSize: 11 }}>01KZ2659…QA7P</code>{" "}
            never moved.
          </div>
        </div>

        <div className="cmp-col">
          <h3>Git — textual, filename-keyed</h3>
          <pre className="cmp-raw">
            {RAW_GIT_DIFF.split("\n").map((l, i) => {
              const cls = l.startsWith("+")
                ? "add"
                : l.startsWith("-")
                  ? "del"
                  : l.startsWith("diff ") || l.startsWith("similarity") || l.startsWith("rename")
                    ? "meta"
                    : "";
              return (
                <span key={i} className={cls}>
                  {l}
                  {"\n"}
                </span>
              );
            })}
          </pre>
        </div>
      </div>

      <div className="cmp-foot">
        Erwin's Complete Compare matches objects by <strong>name</strong>, so it reports this as
        an entity removed and a different entity added — every attribute listed twice, and a
        reviewer has to work out they are the same thing. Because Modelith's ULIDs are immutable
        and file-borne, the rename is a single cosmetic field change and everything beneath it
        stays quiet.
      </div>
    </div>
  );
}
