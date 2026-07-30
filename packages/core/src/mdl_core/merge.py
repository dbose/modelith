"""Three-way merge (spec §5.3).

    base   = state.content_hash          # what we last emitted
    ours   = working tree (current file on disk, if any)
    theirs = freshly generated

    if hash(ours) == base:      write theirs                # clean
    elif theirs == base:        keep ours                   # model unchanged
    else:                       three-way merge per region  # conflict-aware

Region-level merge preserves user regions unconditionally and only rewrites a
generated region when its fingerprint changed. Conflicts are written with git
conflict markers plus an MDL-C3xx code and never silently resolved (§5.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mdl_core.diagnostics import Diagnostic, Severity
from mdl_core.fingerprint import content_hash
from mdl_core.regions import ParsedFile, Region, RegionKind, parse, render


class MergeOutcome(str, Enum):
    clean_written = "clean_written"  # ours == base: took theirs
    unchanged = "unchanged"  # model unchanged: kept ours
    merged = "merged"  # per-region merge, no conflict
    conflict = "conflict"  # per-region merge produced conflicts
    created = "created"  # no existing file


@dataclass
class MergeResult:
    path: str
    outcome: MergeOutcome
    content: str
    diagnostics: list[Diagnostic]


def merge_file(
    *,
    path: str,
    base_hash: str | None,
    ours: str | None,
    theirs: str,
    prefix: str = "--",
) -> MergeResult:
    diags: list[Diagnostic] = []

    if ours is None:
        return MergeResult(path, MergeOutcome.created, theirs, diags)

    ours_hash = content_hash(ours)
    theirs_hash = content_hash(theirs)

    if base_hash is not None and ours_hash == base_hash:
        # user hasn't touched the file since last generation -> take theirs
        return MergeResult(path, MergeOutcome.clean_written, theirs, diags)

    if base_hash is not None and theirs_hash == base_hash:
        # model unchanged since last generation -> keep the user's working tree
        return MergeResult(path, MergeOutcome.unchanged, ours, diags)

    # Both changed (or no base): merge region-by-region.
    return _region_merge(path, ours, theirs, prefix, diags)


def _region_merge(
    path: str, ours: str, theirs: str, prefix: str, diags: list[Diagnostic]
) -> MergeResult:
    ours_parsed = parse(ours, prefix)
    theirs_parsed = parse(theirs, prefix)

    # Index generated regions by obj_id for alignment.
    ours_gen = {r.obj_id: r for r in ours_parsed.generated_regions()}

    out_regions: list[Region] = []
    conflicted = False

    # Walk theirs as the structural spine (it reflects the current model), but
    # graft user regions and honour "user edited generated content" rules.
    for r in theirs_parsed.regions:
        if r.kind == RegionKind.generated:
            prev = ours_gen.get(r.obj_id)
            if prev is None:
                out_regions.append(r)  # new generated block
                continue
            if prev.fingerprint == r.fingerprint:
                # Fingerprint unchanged. Did the user edit the generated body?
                if _norm(prev.content) != _norm(r.content):
                    # MDL-E201: user edited generated code, model unchanged.
                    diags.append(
                        Diagnostic(
                            code="MDL-E201",
                            severity=Severity.error,
                            message=(
                                f"{path}: generated block id={r.obj_id} was modified by "
                                f"hand; run `mdl adopt` or `mdl unmanage`"
                            ),
                            path=path,
                        )
                    )
                    # Preserve the user's edit rather than clobber (§5.1).
                    out_regions.append(prev)
                else:
                    out_regions.append(r)
            else:
                # Fingerprint changed -> model changed.
                if _norm(prev.content) == _norm(r.content):
                    out_regions.append(r)  # bodies coincide, no conflict
                elif _is_unedited_since_generation(prev):
                    out_regions.append(r)  # user didn't touch it; take new gen
                else:
                    # True three-way conflict.
                    conflicted = True
                    diags.append(
                        Diagnostic(
                            code="MDL-C301",
                            severity=Severity.error,
                            message=(
                                f"{path}: generated block id={r.obj_id} changed in both "
                                f"the model and by hand"
                            ),
                            path=path,
                        )
                    )
                    out_regions.append(_conflict_region(prev, r, prefix))
        elif r.kind == RegionKind.user:
            # User regions come from ours (never rewritten). theirs's user region
            # is a fresh scaffold only used when ours has none.
            matched = _matching_user_region(ours_parsed, r)
            out_regions.append(matched if matched is not None else r)
        else:
            out_regions.append(r)

    # Append any user regions from ours that theirs didn't scaffold.
    _append_orphan_user_regions(ours_parsed, out_regions)

    merged = render(ParsedFile(out_regions), prefix)
    outcome = MergeOutcome.conflict if conflicted else MergeOutcome.merged
    return MergeResult(path, outcome, merged, diags)


def _norm(text: str) -> str:
    return text.strip("\n")


def _is_unedited_since_generation(_prev: Region) -> bool:
    # Without a per-region base we treat a matching-fingerprint edit as the signal;
    # here (fingerprint differs) we conservatively assume the user may have edited.
    # The file-level base_hash check upstream already handled the clean case.
    return False


def _conflict_region(ours: Region, theirs: Region, prefix: str) -> Region:
    body = (
        f"{prefix} <<<<<<< ours (hand-edited)\n"
        f"{ours.content}"
        f"{'' if ours.content.endswith(chr(10)) else chr(10)}"
        f"{prefix} ======= MDL-C301\n"
        f"{theirs.content}"
        f"{'' if theirs.content.endswith(chr(10)) else chr(10)}"
        f"{prefix} >>>>>>> theirs (regenerated)\n"
    )
    return Region(
        kind=RegionKind.generated,
        content=body,
        obj_id=theirs.obj_id,
        fingerprint=theirs.fingerprint,
        spec=theirs.spec,
    )


def _matching_user_region(ours_parsed: ParsedFile, _theirs_user: Region) -> Region | None:
    users = [r for r in ours_parsed.regions if r.kind == RegionKind.user]
    return users[0] if users else None


def _append_orphan_user_regions(ours_parsed: ParsedFile, out_regions: list[Region]) -> None:
    used = sum(1 for r in out_regions if r.kind == RegionKind.user)
    ours_users = [r for r in ours_parsed.regions if r.kind == RegionKind.user]
    for extra in ours_users[used:]:
        out_regions.append(extra)
