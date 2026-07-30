"""Naming standards lint (spec §2.4).

First-class, enforceable config: casing rules per layer, abbreviation dictionary,
with a --fix mode. This is the feature erwin has and OSS competitors skip.

The lint returns diagnostics *and*, when --fix is requested, a map of
{ulid_or_attr_path: corrected_name} that the caller applies to the raw YAML nodes
so comments survive.
"""

from __future__ import annotations

import re

from mdl_core.diagnostics import Diagnostic, DiagnosticSet, Severity
from mdl_core.ir import LogicalEntity, Model, PhysicalTable

_SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
_UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")


def _matches_case(name: str, case: str) -> bool:
    if case == "any":
        return True
    if case == "snake":
        return bool(_SNAKE.match(name))
    if case == "upper_snake":
        return bool(_UPPER_SNAKE.match(name))
    if case == "camel":
        return bool(re.match(r"^[a-z][a-zA-Z0-9]*$", name))
    if case == "pascal":
        return bool(re.match(r"^[A-Z][a-zA-Z0-9]*$", name))
    return True


def _to_case(name: str, case: str) -> str:
    # Split on any non-alphanumeric boundary and camelCase boundaries.
    parts = re.split(r"[_\s\-]+", name)
    words: list[str] = []
    for p in parts:
        if not p:
            continue
        # split camelCase / PascalCase into words
        words.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", p) or [p])
    words = [w.lower() for w in words if w]
    if case == "snake":
        return "_".join(words)
    if case == "upper_snake":
        return "_".join(w.upper() for w in words)
    if case == "camel":
        return words[0] + "".join(w.capitalize() for w in words[1:]) if words else name
    if case == "pascal":
        return "".join(w.capitalize() for w in words)
    return name


def _apply_abbreviations(name: str, abbrevs: dict[str, str]) -> str:
    if not abbrevs:
        return name
    tokens = name.split("_")
    return "_".join(abbrevs.get(t, t) for t in tokens)


class NamingFix:
    """Corrections to apply, keyed for the caller to locate the raw node.

    - entities: {ulid: new_name}
    - attributes: {(entity_ulid, attr_ulid): new_name}
    """

    def __init__(self) -> None:
        self.entities: dict[str, str] = {}
        self.attributes: dict[tuple[str, str], str] = {}

    def empty(self) -> bool:
        return not self.entities and not self.attributes


def lint(model: Model) -> tuple[DiagnosticSet, NamingFix]:
    diags = DiagnosticSet()
    fix = NamingFix()
    naming = model.config.naming

    for le in model.logical_entities.values():
        _check_name(le, le.name, naming.logical_case, naming.abbreviations, diags, fix)
        for attr in le.attributes:
            corrected = _corrected(attr.name, naming.logical_case, naming.abbreviations)
            if corrected != attr.name:
                diags.add(
                    Diagnostic(
                        code="MDL-W301",
                        severity=Severity.warning,
                        message=(
                            f"attribute {attr.name!r} on {le.name!r} violates "
                            f"{naming.logical_case} casing; suggest {corrected!r}"
                        ),
                        path=attr.id,
                        fix_available=True,
                    )
                )
                fix.attributes[(le.id, attr.id)] = corrected

    for pt in model.physical_tables.values():
        _check_name(pt, pt.name, naming.physical_case, naming.abbreviations, diags, fix)

    return diags, fix


def _corrected(name: str, case: str, abbrevs: dict[str, str]) -> str:
    corrected = _to_case(name, case)
    corrected = _apply_abbreviations(corrected, abbrevs)
    return corrected


def _check_name(
    obj: LogicalEntity | PhysicalTable,
    name: str,
    case: str,
    abbrevs: dict[str, str],
    diags: DiagnosticSet,
    fix: NamingFix,
) -> None:
    corrected = _corrected(name, case, abbrevs)
    if corrected != name:
        diags.add(
            Diagnostic(
                code="MDL-W300",
                severity=Severity.warning,
                message=(
                    f"{obj.kind.value} {name!r} violates {case} casing "
                    f"or abbreviation rules; suggest {corrected!r}"
                ),
                path=obj.id,
                fix_available=True,
            )
        )
        fix.entities[obj.id] = corrected
