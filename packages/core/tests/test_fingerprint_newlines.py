"""The staleness fingerprint must be newline-insensitive.

On Git-for-Windows (core.autocrlf=true) model YAMLs check out with CRLF. The old
size-based fingerprint differed between an LF and a CRLF checkout, so the client's
staleness check fired on every edit and the canvas/LSP was effectively read-only on
Windows. dir_fingerprint now hashes newline-normalised content.
"""

from __future__ import annotations

from pathlib import Path

from mdl_core.commands import dir_fingerprint


def _write(root: Path, rel: str, text: str, *, crlf: bool) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\n", "\r\n") if crlf else text
    # write bytes so the newline style is preserved exactly (no translation)
    p.write_bytes(data.encode("utf-8"))


_MODEL = "id: 01J\nkind: logical_entity\nname: counterparty\nattributes:\n  - name: id\n"


def test_fingerprint_identical_for_lf_and_crlf(tmp_path):
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    _write(lf, "logical/entities/counterparty.yaml", _MODEL, crlf=False)
    _write(crlf, "logical/entities/counterparty.yaml", _MODEL, crlf=True)

    # same content, different line endings -> the fingerprint must MATCH
    assert dir_fingerprint(lf) == dir_fingerprint(crlf)


def test_fingerprint_changes_on_real_content_edit(tmp_path):
    d = tmp_path / "m"
    _write(d, "logical/entities/a.yaml", _MODEL, crlf=False)
    fp1 = dir_fingerprint(d)
    _write(d, "logical/entities/a.yaml", _MODEL + "definition: a party\n", crlf=False)
    assert dir_fingerprint(d) != fp1, "a real content change must change the fingerprint"


def test_fingerprint_changes_when_a_file_is_added(tmp_path):
    d = tmp_path / "m"
    _write(d, "logical/entities/a.yaml", _MODEL, crlf=False)
    fp1 = dir_fingerprint(d)
    _write(d, "logical/entities/b.yaml", _MODEL, crlf=False)
    assert dir_fingerprint(d) != fp1
