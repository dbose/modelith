import { useEffect, useState } from "react";
import { fetchGitContext } from "../api";
import type { GitContext } from "../types";

/** The git reality, stated plainly (plan §F).
 *
 * There is no lock file and no "claim this area" button: the branch IS the lock,
 * so the honest thing is to say where your edits go and what stands in the way.
 * The dirty-tree case matters most — propose 409s on it, and without this the SME
 * only finds out after filling in the whole submit form. */
export function GitBanner({ user }: { user: string }) {
  const [ctx, setCtx] = useState<GitContext | null>(null);

  useEffect(() => {
    fetchGitContext(user).then(setCtx).catch(() => undefined);
  }, [user]);

  if (!ctx?.ok || !ctx.git) return null;

  if (ctx.read_only) {
    return (
      <div className="git-banner">
        <code>{ctx.branch}</code>
        <span>browse only — editing is disabled on this deployment</span>
      </div>
    );
  }
  if (ctx.dirty) {
    return (
      <div className="git-banner warn">
        <code>{ctx.branch}</code>
        <span>
          uncommitted changes in the working tree — a proposal will be refused until they
          are committed or discarded
        </span>
      </div>
    );
  }
  if (ctx.on_base) {
    return (
      <div className="git-banner">
        <code>{ctx.branch}</code>
        <span>protected — your edits become a pull request</span>
      </div>
    );
  }
  return (
    <div className="git-banner">
      <code>{ctx.branch}</code>
      <span>your proposal branch</span>
      {ctx.behind ? <span className="dim">· {ctx.base_branch} is {ctx.behind} ahead</span> : null}
    </div>
  );
}
