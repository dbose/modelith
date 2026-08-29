"""pygls wiring — a thin transport over the pure feature builders.

One server binary (`mdl lsp`, stdio) serves VS Code, Cursor, Windsurf, JetBrains
and anything else speaking LSP. All intelligence lives in the shared Python
libraries; this module only translates workspace events into publishes.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import pathname2url

from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

from mdl_lsp import commands as cmds
from mdl_lsp import features
from mdl_lsp.workspace import ModelWorkspace

server = LanguageServer("modelith-lsp", "0.1.0")
_ws: ModelWorkspace | None = None
_published: set[str] = set()


def _uri(path: Path) -> str:
    return "file://" + pathname2url(str(path))


def _path(uri: str) -> Path:
    return Path(unquote(urlparse(uri).path))


def _publish_all() -> None:
    if _ws is None:
        return
    all_diags: dict[Path, list[lsp.Diagnostic]] = {}
    for m in (features.model_diagnostics(_ws), features.dbt_diagnostics(_ws)):
        for path, diags in m.items():
            all_diags.setdefault(path, []).extend(diags)

    current = {_uri(p) for p in all_diags}
    for stale in _published - current:  # clear files that became clean
        server.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=stale, diagnostics=[])
        )
    for path, diags in all_diags.items():
        server.text_document_publish_diagnostics(
            lsp.PublishDiagnosticsParams(uri=_uri(path), diagnostics=diags)
        )
    _published.clear()
    _published.update(current)


@server.feature(lsp.INITIALIZE)
def on_initialize(params: lsp.InitializeParams) -> None:
    global _ws
    root = params.root_uri or (
        params.workspace_folders[0].uri if params.workspace_folders else None
    )
    if root:
        _ws = ModelWorkspace(_path(root))


@server.feature(lsp.INITIALIZED)
def on_initialized(_params: lsp.InitializedParams) -> None:
    _publish_all()


@server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def on_open(_params: lsp.DidOpenTextDocumentParams) -> None:
    _publish_all()


@server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def on_save(_params: lsp.DidSaveTextDocumentParams) -> None:
    _publish_all()


@server.feature(lsp.TEXT_DOCUMENT_HOVER)
def on_hover(params: lsp.HoverParams) -> lsp.Hover | None:
    if _ws is None:
        return None
    return features.hover(
        _ws, _path(params.text_document.uri), params.position.line, params.position.character
    )


@server.feature(
    lsp.TEXT_DOCUMENT_COMPLETION,
    lsp.CompletionOptions(trigger_characters=[":", " "]),
)
def on_completion(params: lsp.CompletionParams) -> lsp.CompletionList:
    if _ws is None:
        return lsp.CompletionList(is_incomplete=False, items=[])
    items = features.completion(
        _ws,
        _path(params.text_document.uri),
        params.position.line,
        params.position.character,
    )
    # incomplete when a remote resolver is configured — re-query as the user narrows
    return lsp.CompletionList(is_incomplete=bool(items), items=items)


@server.feature(lsp.TEXT_DOCUMENT_CODE_LENS)
def on_code_lens(params: lsp.CodeLensParams) -> list[lsp.CodeLens]:
    if _ws is None:
        return []
    return features.code_lens(_ws, _path(params.text_document.uri))


@server.feature(lsp.TEXT_DOCUMENT_CODE_ACTION)
def on_code_action(params: lsp.CodeActionParams) -> list[lsp.CodeAction]:
    if _ws is None:
        return []
    return features.code_actions(
        _ws, _path(params.text_document.uri), list(params.context.diagnostics)
    )


def _run(fn, *args) -> None:
    try:
        msg = fn(_ws, *args)
        server.window_show_message(lsp.ShowMessageParams(lsp.MessageType.Info, f"Modelith: {msg}"))
    except Exception as e:  # noqa: BLE001 — surface to the user, never crash the server
        server.window_show_message(lsp.ShowMessageParams(lsp.MessageType.Error, f"Modelith: {e}"))
    _publish_all()


@server.command("mdl.lift")
def cmd_lift(args: list) -> None:
    _run(cmds.lift_model, args[0])


@server.command("mdl.adoptColumn")
def cmd_adopt(args: list) -> None:
    _run(cmds.adopt_column, args[0], args[1], args[2] if len(args) > 2 else None)


@server.command("mdl.unmanage")
def cmd_unmanage(args: list) -> None:
    _run(cmds.unmanage, args[0])


@server.command("mdl.declareRelationship")
def cmd_declare(args: list) -> None:
    _run(cmds.declare_relationship, args[0], args[1], args[2])


def main() -> None:
    server.start_io()


if __name__ == "__main__":
    main()
