"""Protocol-level smoke: spawn the real `mdl lsp` process, speak LSP over stdio,
assert initialize succeeds and diagnostics get published for the workspace.
This is the transport-layer complement to the pure-function feature tests."""

from __future__ import annotations

import json
import subprocess
import sys
import time


def _frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def _read_messages(stream, deadline: float) -> list[dict]:
    msgs = []
    buf = b""
    while time.time() < deadline:
        chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
        if not chunk:
            time.sleep(0.05)
            continue
        buf += chunk
        while True:
            head_end = buf.find(b"\r\n\r\n")
            if head_end < 0:
                break
            head = buf[:head_end].decode()
            length = int(head.split("Content-Length:")[1].split("\r\n")[0].strip())
            start = head_end + 4
            if len(buf) < start + length:
                break
            msgs.append(json.loads(buf[start : start + length]))
            buf = buf[start + length :]
        if any(m.get("method") == "textDocument/publishDiagnostics" for m in msgs) and any(
            "result" in m and m.get("id") == 1 for m in msgs
        ):
            break
    return msgs


def test_lsp_stdio_handshake_and_publish(workspace):
    # break the model so there is something to publish
    f = workspace.model_dir / "logical" / "entities" / "counterparty.yaml"
    f.write_text(f.read_text().replace("realises:", "realises: 01BROKEN00000000000000000X\n#"))

    proc = subprocess.Popen(
        [sys.executable, "-m", "mdl_lsp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        root_uri = workspace.root.as_uri()
        proc.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"processId": None, "rootUri": root_uri, "capabilities": {}},
                }
            )
        )
        proc.stdin.write(_frame({"jsonrpc": "2.0", "method": "initialized", "params": {}}))
        proc.stdin.flush()

        msgs = _read_messages(proc.stdout, time.time() + 20)
        init_result = next(m for m in msgs if m.get("id") == 1)
        caps = init_result["result"]["capabilities"]
        assert caps.get("hoverProvider")
        assert caps.get("codeActionProvider")
        assert caps.get("codeLensProvider") is not None
        cmds = caps.get("executeCommandProvider", {}).get("commands", [])
        assert "mdl.lift" in cmds and "mdl.unmanage" in cmds

        published = [
            m for m in msgs if m.get("method") == "textDocument/publishDiagnostics"
        ]
        assert published, "server never published diagnostics"
        all_diags = [d for m in published for d in m["params"]["diagnostics"]]
        assert any(d.get("code") == "MDL-E102" for d in all_diags)
    finally:
        proc.kill()
