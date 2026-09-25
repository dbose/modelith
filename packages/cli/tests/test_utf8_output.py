"""Windows cp1252 console must not crash the CLI on non-ASCII output.

A real Windows box defaults stdout to the legacy cp1252 code page, whose charmap codec
cannot encode the CLI's ✓/✗ verdict marks, em-dashes, ellipses, or the · separator. The
reverse summary printed exactly those, so a SUCCESSFUL reverse died with a
UnicodeEncodeError and the caller saw a spurious failure. main() calls _force_utf8_output()
to reconfigure the streams to UTF-8 first.
"""

from __future__ import annotations

import io
import sys

from mdl_cli.main import _force_utf8_output

# the glyphs the CLI actually prints (verdict marks + typography)
_GLYPHS = "✓ ✗ — … ·"


def test_cp1252_stream_reconfigured_to_utf8(monkeypatch):
    # a stream that starts as cp1252, like a Windows console
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)

    _force_utf8_output()

    assert sys.stdout.encoding.lower() == "utf-8"
    # the glyphs that used to raise now encode cleanly
    print(_GLYPHS)
    sys.stdout.flush()
    assert "✓".encode() in raw.getvalue()


def test_missing_reconfigure_is_not_fatal(monkeypatch):
    # a stream without reconfigure() (e.g. some wrappers) must be tolerated, not crash
    class NoReconfigure(io.StringIO):
        reconfigure = None  # explicitly absent

    monkeypatch.setattr(sys, "stdout", NoReconfigure())
    monkeypatch.setattr(sys, "stderr", NoReconfigure())
    _force_utf8_output()  # must simply return without raising


def test_glyphs_are_the_ones_the_cli_prints():
    # guard: if someone adds a new mark, it should be covered here too
    from mdl_cli import main

    src = main.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    # the verdict marks are still present in the summary code
    assert '"accepted": "✓"' in text
    assert '"rejected": "✗"' in text
