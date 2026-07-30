"""Protected regions (spec §5.1).

Generated files interleave Modelith-owned blocks and user-owned blocks:

    -- mdl:generated-begin id=<ULID> fingerprint=sha256:... spec=v1
    ...generated content...
    -- mdl:generated-end

    -- mdl:user-begin
    ...survives regeneration forever...
    -- mdl:user-end

Rules enforced here:
- Content between generated-begin/end is owned by Modelith and replaced on
  regeneration only if the fingerprint no longer matches (§5.1).
- If the fingerprint matches but content changed, the user edited generated code:
  MDL-E201 (§5.1). Callers surface it; `adopt`/`unmanage` handle resolution.
- User regions are never rewritten.

The marker comment prefix is configurable per file type (SQL uses `--`, YAML uses
`#`) so the same region model serves both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class RegionKind(str, Enum):
    generated = "generated"
    user = "user"
    literal = "literal"  # untracked text between/around regions


@dataclass
class Region:
    kind: RegionKind
    content: str  # inner content (without the marker lines), or raw text for literal
    obj_id: str | None = None
    fingerprint: str | None = None
    spec: str | None = None


@dataclass
class ParsedFile:
    regions: list[Region] = field(default_factory=list)

    def generated_regions(self) -> list[Region]:
        return [r for r in self.regions if r.kind == RegionKind.generated]


def _begin_re(prefix: str) -> re.Pattern[str]:
    p = re.escape(prefix)
    return re.compile(
        rf"^\s*{p}\s*mdl:generated-begin\s+id=(?P<id>\S+)\s+"
        rf"fingerprint=(?P<fp>\S+)\s+spec=(?P<spec>\S+)\s*$"
    )


def _simple_marker(prefix: str, marker: str) -> re.Pattern[str]:
    p = re.escape(prefix)
    return re.compile(rf"^\s*{p}\s*mdl:{marker}\s*$")


def parse(text: str, prefix: str = "--") -> ParsedFile:
    begin_re = _begin_re(prefix)
    gen_end = _simple_marker(prefix, "generated-end")
    user_begin = _simple_marker(prefix, "user-begin")
    user_end = _simple_marker(prefix, "user-end")

    lines = text.splitlines(keepends=True)
    regions: list[Region] = []
    i = 0
    literal_buf: list[str] = []

    def flush_literal() -> None:
        if literal_buf:
            regions.append(Region(RegionKind.literal, "".join(literal_buf)))
            literal_buf.clear()

    while i < len(lines):
        line = lines[i]
        m = begin_re.match(line)
        if m:
            flush_literal()
            body: list[str] = []
            i += 1
            while i < len(lines) and not gen_end.match(lines[i]):
                body.append(lines[i])
                i += 1
            if i >= len(lines):
                raise ValueError("unterminated mdl:generated-begin region")
            i += 1  # consume end marker
            regions.append(
                Region(
                    kind=RegionKind.generated,
                    content="".join(body),
                    obj_id=m.group("id"),
                    fingerprint=m.group("fp"),
                    spec=m.group("spec"),
                )
            )
            continue
        if user_begin.match(line):
            flush_literal()
            body = []
            i += 1
            while i < len(lines) and not user_end.match(lines[i]):
                body.append(lines[i])
                i += 1
            if i >= len(lines):
                raise ValueError("unterminated mdl:user-begin region")
            i += 1
            regions.append(Region(kind=RegionKind.user, content="".join(body)))
            continue
        literal_buf.append(line)
        i += 1
    flush_literal()
    return ParsedFile(regions=regions)


def render(parsed: ParsedFile, prefix: str = "--") -> str:
    out: list[str] = []
    for r in parsed.regions:
        if r.kind == RegionKind.literal:
            out.append(r.content)
        elif r.kind == RegionKind.generated:
            out.append(
                f"{prefix} mdl:generated-begin id={r.obj_id} "
                f"fingerprint={r.fingerprint} spec={r.spec}\n"
            )
            out.append(r.content)
            if not r.content.endswith("\n") and r.content:
                out.append("\n")
            out.append(f"{prefix} mdl:generated-end\n")
        elif r.kind == RegionKind.user:
            out.append(f"{prefix} mdl:user-begin\n")
            out.append(r.content)
            if not r.content.endswith("\n") and r.content:
                out.append("\n")
            out.append(f"{prefix} mdl:user-end\n")
    return "".join(out)


def make_generated_region(obj_id: str, fingerprint: str, content: str, spec: str = "v1") -> Region:
    return Region(
        kind=RegionKind.generated,
        content=content if content.endswith("\n") or not content else content,
        obj_id=obj_id,
        fingerprint=fingerprint,
        spec=spec,
    )
