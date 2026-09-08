"""Preview a set of staged changes without touching the model on disk.

The modeler app stages edits and proposes them as one PR, so nothing is written
until submit. But every editing control is driven by the projected model — so
without a preview, an edit would visually revert on the next render, new attributes
would never appear, and deleted rows would stay.

Rather than reimplement the mutation handlers in the browser (a second engine that
would drift silently from core), the same handlers run here against a throwaway
copy of the repo. One request returns the projected model, the diff against disk,
and the validation diagnostics.
"""

from __future__ import annotations

import copy
import shutil
import tempfile
from pathlib import Path
from typing import Any

from mdl_core.commands import _HANDLERS, CommandError
from mdl_core.diff import diff_models
from mdl_core.diff_render import render_json
from mdl_core.repo import ModelRepo
from mdl_core.validate import validate
from mdl_server.projection import project


def preview_model(
    repo: ModelRepo, changes: list[dict], *, subject_area: str | None = None
) -> dict[str, Any]:
    """Apply `changes` to a copy of `repo` and project the result.

    `repo` must be the server's already-loaded, cached repo: deep-copying it is
    ~15x cheaper than re-reading the model from disk (51ms vs 684ms on a 428-file
    model), and preview runs on every staged edit.
    """
    scratch = copy.deepcopy(repo)

    # Point the copy at a throwaway directory BEFORE running any handler.
    # ModelRepo.remove_file and rename_file unlink from disk immediately, so a
    # preview of `delete_entity` against the real root deletes the user's files —
    # this was reproduced, and it destroyed three of them. The temp dir must also
    # live outside the model dir, or the server's fingerprint (an rglob over
    # *.yaml) would see the preview's files and bust its cache on every keystroke.
    tmp = Path(tempfile.mkdtemp(prefix="mdl-preview-"))
    try:
        scratch.root = tmp

        created_ids: dict[int, str] = {}
        failed_index: int | None = None
        error: str | None = None

        for i, change in enumerate(changes):
            op = change.get("op", "")
            payload = change.get("payload") or {}
            handler = _HANDLERS.get(op)
            if handler is None:
                failed_index, error = i, f"unknown command {op!r}"
                break
            try:
                made = handler(scratch, payload)
            except CommandError as e:
                # Stop, but still project what succeeded: "change #4 is invalid" is
                # far more useful to the user than a blank screen.
                failed_index, error = i, str(e)
                break
            if made:
                created_ids[i] = made

            # Handlers mutate `raw` (the YAML nodes) but not `model` (the parsed
            # object graph the NEXT handler resolves ids against), and
            # apply_command only ever runs one op per load, so it never had to.
            # A batch does: create_entity then add_attribute on it would fail to
            # find the entity.
            #
            # Re-parsing costs ~35ms, so only do it when this op actually created
            # something a later one could reference — otherwise a 20-change tray
            # would take 700ms instead of ~70ms.
            if made and i + 1 < len(changes):
                scratch.save()
                scratch = ModelRepo.load(tmp)

        scratch.save()
        previewed = ModelRepo.load(tmp).model

        diags = validate(previewed)
        return {
            "ok": failed_index is None,
            "model": project(previewed, subject_area=subject_area),
            "diff": render_json(
                diff_models(
                    repo.model,
                    previewed,
                    base_label="on disk",
                    head_label="your staged changes",
                )
            ),
            "diagnostics": [
                {
                    "code": d.code,
                    "severity": d.severity.value,
                    "message": d.message,
                    "path": d.path,
                }
                for d in diags.items
            ],
            "created_ids": {str(k): v for k, v in created_ids.items()},
            "failed_index": failed_index,
            "error": error,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
