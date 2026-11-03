#!/usr/bin/env python
"""Convert a physical PostgreSQL DDL script into an Erwin Data Modeler 10.10 XML model (File > Open > XML).

Tables become entities, columns become attributes, PRIMARY KEY / UNIQUE / FOREIGN KEY constraints become
key groups and relationships. Logical names are derived from the physical names (snake_case -> words).

Model-level scaffolding (domains, themes, images, naming options ...) is cloned from a native Erwin XML
export, so the result carries your own mart's settings. Only the content tables/relationships/subject area
is generated.

    py ddl_to_erwin_xml.py model.sql --scaffold "Logical -Combined Data Model - latest.xml" -o model.erwin.xml

Requires: pip install lxml pglast

Reference implementation for the erwin-template export (see docs/modelith-erwin-xml-exporter.md §7).
`packages/emit-erd/src/mdl_emit_erd/erwin_template.py` ports this logic, driven by Modelith's IR
instead of a parsed DDL script. This file is kept verbatim as the erwin-verified ground truth; it
is a standalone tool and is not imported by the package.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lxml import etree as ET
from pglast import ast, enums, parse_sql

NS = "http://www.erwin.com/dm/data"
N = lambda t: f"{{{NS}}}{t}"
ln = lambda e: ET.QName(e).localname


# ------------------------------------------------------------------ schema model
@dataclass
class Col:
    name: str
    ptype: str
    notnull: bool = False
    comment: str | None = None


@dataclass
class FK:
    name: str | None
    cols: list[str]
    ref: str  # referenced table key
    ref_cols: list[str]


@dataclass
class Table:
    key: str
    name: str
    comment: str | None = None
    cols: list[Col] = field(default_factory=list)
    pk: list[str] = field(default_factory=list)
    uniques: list[list[str]] = field(default_factory=list)
    fks: list[FK] = field(default_factory=list)


# ------------------------------------------------------------------ DDL parsing
def _s(node):
    return node.sval if hasattr(node, "sval") else str(node)


def _names(items):
    items = getattr(items, "items", items)
    return [_s(i) for i in items]


def _typename(t) -> tuple[str, list[int]]:
    names = [_s(n) for n in t.names if _s(n) != "pg_catalog"]
    mods = []
    for m in t.typmods or ():
        v = getattr(m, "val", m)
        v = getattr(v, "ival", getattr(v, "value", v))
        if isinstance(v, int):
            mods.append(v)
    return names[-1].lower(), mods


def _ptype(t) -> str:
    base, mods = _typename(t)
    return f"{base}({','.join(map(str, mods))})" if mods else base


def parse_ddl(sql: str, schema: str | None) -> dict[str, Table]:
    tables: dict[str, Table] = {}
    key_of = lambda rv: f"{rv.schemaname or ''}.{rv.relname}".lower()

    def wanted(rv):
        return schema is None or (rv.schemaname or "").lower() == schema.lower()

    def constraint(t: Table, c, own_col: str | None = None):
        keys = [_s(k) for k in (c.keys or ())] or ([own_col] if own_col else [])
        if c.contype == enums.ConstrType.CONSTR_PRIMARY:
            t.pk = keys
        elif c.contype == enums.ConstrType.CONSTR_UNIQUE:
            t.uniques.append(keys)
        elif c.contype == enums.ConstrType.CONSTR_FOREIGN:
            cols = [_s(k) for k in (c.fk_attrs or ())] or ([own_col] if own_col else [])
            ref = [_s(k) for k in (c.pk_attrs or ())]
            t.fks.append(FK(c.conname, cols, key_of(c.pktable), ref))

    stmts = parse_sql(sql)
    for raw in stmts:
        st = raw.stmt
        if isinstance(st, ast.CreateStmt) and wanted(st.relation):
            t = Table(key_of(st.relation), st.relation.relname)
            tables[t.key] = t
            for el in st.tableElts or ():
                if isinstance(el, ast.ColumnDef):
                    col = Col(el.colname, _ptype(el.typeName), bool(el.is_not_null))
                    for c in el.constraints or ():
                        if c.contype == enums.ConstrType.CONSTR_NOTNULL:
                            col.notnull = True
                        else:
                            constraint(t, c, el.colname)
                            if c.contype == enums.ConstrType.CONSTR_PRIMARY:
                                col.notnull = True
                    t.cols.append(col)
                elif isinstance(el, ast.Constraint):
                    constraint(t, el)
            for c in t.cols:
                if c.name in t.pk:
                    c.notnull = True
    for raw in stmts:  # second pass: ALTER TABLE ... ADD CONSTRAINT and COMMENT ON
        st = raw.stmt
        if isinstance(st, ast.AlterTableStmt) and key_of(st.relation) in tables:
            for cmd in st.cmds or ():
                if cmd.subtype == enums.AlterTableType.AT_AddConstraint:
                    constraint(tables[key_of(st.relation)], cmd.def_)
        elif isinstance(st, ast.CommentStmt) and st.comment:
            names = _names(st.object)
            if st.objtype == enums.ObjectType.OBJECT_TABLE:
                k = ".".join(names[-2:] if len(names) > 1 else ["", names[0]]).lower()
                if k in tables:
                    tables[k].comment = st.comment
            elif st.objtype == enums.ObjectType.OBJECT_COLUMN:
                k = ".".join(names[-3:-1] if len(names) > 2 else ["", names[-2]]).lower()
                for c in tables.get(k, Table("", "")).cols:
                    if c.name == names[-1]:
                        c.comment = st.comment
    return tables


# ------------------------------------------------------------------ naming and types
def words(s: str) -> str:
    return re.sub(r"[_\s]+", " ", s).strip().lower()


def display(s: str, case: str) -> str:
    w = words(s)
    return {"title": w.title(), "upper": w.upper(), "lower": w, "verbatim": s}[case]


def erwin_type(ptype: str, text_len: int):
    m = re.match(r"(\w+)(?:\((.*)\))?$", ptype)
    base, mods = m.group(1), [int(x) for x in (m.group(2) or "").split(",") if x.strip()]
    if base in ("int2", "smallint"):
        return "NUMBER(5,0)", "Number"
    if base in ("int4", "int", "integer", "serial"):
        return "NUMBER(10,0)", "Number"
    if base in ("int8", "bigint", "bigserial"):
        return "NUMBER(19,0)", "Number"
    if base in ("numeric", "decimal"):
        return (f"NUMBER({mods[0]},{mods[1] if len(mods) > 1 else 0})" if mods else "NUMBER(38,6)"), "Number"
    if base in ("float4", "float8", "real", "double"):
        return "FLOAT", "Number"
    if base in ("bool", "boolean"):
        return "BYTE", "Boolean"
    if base == "date":
        return "DATE", "Date"
    if base in ("timestamp", "timestamptz", "time", "timetz"):
        return "TIMESTAMP", "Datetime"
    if base in ("varchar", "character varying"):
        return (f"VARCHAR({mods[0]})" if mods else f"VARCHAR({text_len})"), "String"
    if base in ("bpchar", "char", "character"):
        return f"CHAR({mods[0] if mods else 1})", "String"
    if base == "uuid":
        return "CHAR(36)", "String"
    if base == "bytea":
        return "LARGE BINARY", "String"
    return f"VARCHAR({text_len})", "String"  # text, json, jsonb, enums ...


# ------------------------------------------------------------------ Erwin XML
def guid(key: str, suffix: str = "+00000000") -> str:
    h = hashlib.sha1(key.encode()).hexdigest().upper()
    return f"{{{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}}}{suffix}"


def sub(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, N(tag))
    for k, v in attrs.items():
        e.set(k, v)
    if text is not None:
        e.text = text
    return e


def order_array(parent, tag, item, ids):
    arr = sub(parent, tag)
    for i, x in enumerate(ids):
        sub(arr, item, x, index=str(i))


def build(tables: dict[str, Table], a) -> tuple[ET._ElementTree, list[str]]:
    warnings: list[str] = []
    tree = ET.parse(str(a.scaffold))
    root = tree.getroot()
    model = next(c for c in root if ln(c) == "Model")
    groups = {ln(c): c for c in model}
    domains = {}
    for d in groups["Domain_Groups"]:
        p = d.find(N("DomainProps"))
        domains[p.findtext(N("Name"))] = d.get("id")
    default_dom = domains.get("String") or domains.get("<default>")
    dom_name = {"Number": "Number", "String": "String", "Date": a.date_domain, "Datetime": "Datetime",
                "Boolean": a.bool_domain}
    dom_id = lambda k: domains.get(dom_name[k]) or default_dom

    # strip the scaffold's content; keep its settings
    for k in ("Entity_Groups", "Relationship_Groups", "Subject_Area_Groups"):
        for c in list(groups[k]):
            groups[k].remove(c)
    for k in ("ER_Diagram_Groups", "Annotation_Groups"):
        if k in groups:
            model.remove(groups[k])
    for c in list(root):
        if ln(c) == "Model_Proxy_Object":
            root.remove(c)
    for el in list(root.iter("{*}Dependent_Objects_Ref_Array")):
        if el.getparent() is not None:
            el.getparent().remove(el)
    mid = guid("model:" + a.name, "+00000001")
    model.set("id", mid)
    model.set("name", a.name)
    mp, mev = groups["ModelProps"], groups["ModelEnvProps"]
    for tag in ("Proxy_Short_Id", "Current_ER_Diagram_Ref", "Template_Path", "Template_Control_Value",
                "Template_Objects", "History_List_Array"):
        for el in mp.findall(N(tag)):
            mp.remove(el)
    mp.find(N("Name")).text = a.name
    mp.find(N("Long_Id")).text = mid
    mev.find(N("Locator")).text = ""
    mev.find(N("Persistence_Unit_Id")).text = guid("unit:" + a.name, "+01000000")

    # --- resolve relationships
    by_key = tables
    ent_label = {k: display(t.name, a.entity_case) for k, t in tables.items()}
    attr_label = lambda c: display(c, a.attr_case) if a.attr_case != "verbatim" else c
    keygroups: dict[str, list[tuple[str, list[str]]]] = {}  # table -> [(kind, cols)] kind PK|AK
    for k, t in tables.items():
        kg = []
        if t.pk:
            kg.append(("PK", t.pk))
        for u in t.uniques:
            if u != t.pk:
                kg.append(("AK", u))
        keygroups[k] = kg

    rels = []  # (name, child, parent, child_cols, parent_cols, parent_kg_index)
    for k, t in tables.items():
        for i, fk in enumerate(t.fks, 1):
            p = by_key.get(fk.ref)
            if p is None:
                warnings.append(f"{t.name}: FK to {fk.ref} skipped (table not in script)")
                continue
            rc = fk.ref_cols or p.pk
            idx = next((n for n, (_, c) in enumerate(keygroups[fk.ref]) if c == rc), None)
            if idx is None or len(rc) != len(fk.cols):
                warnings.append(f"{t.name}({','.join(fk.cols)}) -> {p.name}({','.join(rc)}) skipped: "
                                f"referenced columns are not a whole primary or unique key")
                continue
            rels.append((fk.name or f"{t.name}_{p.name}_fk{i}", k, fk.ref, fk.cols, rc, idx))
    rels.sort(key=lambda r: r[0])

    def identifying(r):
        return set(r[3]) <= set(by_key[r[1]].pk)

    child_rels = defaultdict(list)
    for r in rels:
        child_rels[r[1]].append(r)
    for lst in child_rels.values():
        lst.sort(key=lambda r: (not identifying(r), r[0]))
    fk_first = {}
    for lst in child_rels.values():
        for r in lst:
            for fc, pc in zip(r[3], r[4]):
                fk_first.setdefault((r[1], fc), (r, pc))

    kg_id = lambda tk, i: guid(f"kg:{tk}:{i}")
    rel_id = lambda r: guid("rel:" + r[0] + ":" + r[1])
    attr_id = lambda tk, c: guid(f"attr:{tk}:{c}")
    ent_id = lambda tk: guid("ent:" + tk)

    deps = defaultdict(set)
    for r in rels:
        if r[1] != r[2]:
            deps[r[1]].add(r[2])
    order, done, rest = [], set(), sorted(tables)
    while rest:
        nxt = [k for k in rest if deps[k] <= done] or rest[:1]
        for k in nxt:
            order.append(k); done.add(k); rest.remove(k)

    IMG, ICON = a.image_ref, a.icon_ref
    eg = groups["Entity_Groups"]
    for tk in order:
        t = tables[tk]
        pk = t.pk
        dep = any(identifying(r) for r in child_rels[tk])
        name = ent_label[tk]
        el = sub(eg, "Entity", id=ent_id(tk), name=name)
        p = sub(el, "EntityProps")
        if t.comment:
            sub(p, "Definition", t.comment)
        sub(p, "Name", name)
        if a.physical:
            sub(p, "Physical_Name", t.name)
        sub(p, "Long_Id", ent_id(tk))
        sub(p, "Type", "6" if dep else "1")
        cols = {c.name: c for c in t.cols}
        ordered = [cols[c] for c in pk if c in cols] + [c for c in t.cols if c.name not in pk]
        order_array(p, "Attributes_Order_Ref_Array", "Attributes_Order_Ref", [attr_id(tk, c.name) for c in ordered])
        if IMG:
            sub(p, "Image_Ref", IMG)
        if ICON:
            sub(p, "Icon_Image_Ref", ICON)
        ag = sub(el, "Attribute_Groups")
        for c in ordered:
            lt, dk = erwin_type(c.ptype, a.text_length)
            x = sub(ag, "Attribute", id=attr_id(tk, c.name), name=attr_label(c.name))
            ap = sub(x, "AttributeProps")
            if c.comment:
                sub(ap, "Definition", c.comment)
            sub(ap, "Name", attr_label(c.name))
            if a.physical:
                sub(ap, "Physical_Name", c.name)
            sub(ap, "Long_Id", attr_id(tk, c.name))
            sub(ap, "Type", "0" if c.name in pk else "100")
            sub(ap, "Null_Option_Type", "1" if (c.notnull or c.name in pk) else "0")
            if (tk, c.name) in fk_first:
                r, pc = fk_first[(tk, c.name)]
                sub(ap, "Parent_Attribute_Ref", attr_id(r[2], pc))
                sub(ap, "Parent_Relationship_Ref", rel_id(r))
            sub(ap, "Logical_Data_Type", lt)
            sub(ap, "Parent_Domain_Ref", dom_id(dk))

        kgs = sub(el, "Key_Group_Groups")

        def key_group(kid, kname, ktype, cs, rel=None, parents=None):
            k = sub(kgs, "Key_Group", id=kid, name=kname)
            kp = sub(k, "Key_GroupProps")
            sub(kp, "Name", kname)
            sub(kp, "Long_Id", kid)
            sub(kp, "Key_Group_Type", ktype)
            if rel is not None:
                sub(kp, "Relationship_Ref", rel_id(rel))
            mids = [guid(f"{kid}:{c}") for c in cs]
            order_array(kp, "Key_Group_Members_Order_Ref_Array", "Key_Group_Members_Order_Ref", mids)
            mg = sub(k, "Key_Group_Member_Groups")
            for i, (c, mid_) in enumerate(zip(cs, mids), 1):
                mm = sub(mg, "Key_Group_Member", id=mid_, name=attr_label(c))
                mpp = sub(mm, "Key_Group_MemberProps")
                sub(mpp, "Name", attr_label(c))
                sub(mpp, "Long_Id", mid_)
                sub(mpp, "Attribute_Ref", attr_id(tk, c))
                if parents:
                    sub(mpp, "Parent_Key_Group_Member_Ref", parents[c])
                sub(mpp, "Key_Group_Member_Order", str(i))

        ak = 0
        for i, (kind, cs) in enumerate(keygroups[tk]):
            if kind == "PK":
                key_group(kg_id(tk, i), f"XPK{name}", "PK", cs)
            else:
                ak += 1
                key_group(kg_id(tk, i), f"XAK{ak}{name}", f"AK{ak}", cs)
        for n, r in enumerate(child_rels[tk], 1):
            pk_id = kg_id(r[2], r[5])
            parents = {fc: guid(f"{pk_id}:{pc}") for fc, pc in zip(r[3], r[4])}
            key_group(guid("if:" + rel_id(r)), f"XIF{n}{name}", f"IF{n}", r[3], rel=r, parents=parents)

    rg = groups["Relationship_Groups"]
    for r in rels:
        ident = identifying(r)
        child = by_key[r[1]]
        ccols = {c.name: c for c in child.cols}
        mandatory = ident or all(ccols[c].notnull for c in r[3])
        one = set(r[3]) == set(child.pk) or any(set(r[3]) == set(u) for u in child.uniques)
        el = sub(rg, "Relationship", id=rel_id(r), name=r[0])
        p = sub(el, "RelationshipProps")
        sub(p, "Name", r[0])
        sub(p, "Long_Id", rel_id(r))
        sub(p, "Type", "2" if ident else "7")
        sub(p, "Null_Option_Type", "101" if mandatory else "100")
        sub(p, "Cardinality", "-1" if one else "-3")
        sub(p, "Key_Group_Ref", kg_id(r[2], r[5]))
        sub(p, "Parent_Entity_Ref", ent_id(r[2]))
        sub(p, "Child_Entity_Ref", ent_id(r[1]))

    now = datetime.now()
    stamp = f"{now.day}/{now.month}/{now.year} {int(now.strftime('%I'))}:{now:%M:%S %p}"
    sg = groups["Subject_Area_Groups"]
    sid = guid("sa:" + a.subject_area)
    s = sub(sg, "Subject_Area", id=sid, name=a.subject_area)
    sp = sub(s, "Subject_AreaProps")
    sub(sp, "Name", a.subject_area)
    sub(sp, "Long_Id", sid)
    sub(sp, "Object_Order", "0")
    sub(sp, "Creation_Time", stamp)
    sub(sp, "Modification_Time", stamp)
    order_array(sp, "Auto_Attached_Objects_Ref_Array", "Auto_Attached_Objects_Ref", [rel_id(r) for r in rels])
    order_array(sp, "User_Attached_Objects_Ref_Array", "User_Attached_Objects_Ref", [ent_id(k) for k in order])

    # drop references to objects that only existed in the scaffold model
    known = {e.get("id") for e in root.iter() if e.get("id")} | {e.text for e in root.iter(N("Long_Id"))}
    gre = re.compile(r"^\{[0-9A-Fa-f-]{36}\}\+[0-9A-Fa-f]{8}$")
    for e in list(root.iter()):
        if isinstance(e.tag, str) and "Ref" in ln(e) and e.text and gre.match(e.text.strip()):
            if e.text.strip() not in known and e.getparent() is not None:
                e.getparent().remove(e)
    return tree, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ddl", type=Path, help="PostgreSQL DDL script")
    ap.add_argument("--scaffold", type=Path, required=True, help="native Erwin 10.10 XML export to clone settings from")
    ap.add_argument("-o", "--out", type=Path, help="output XML (default: <ddl>.erwin.xml)")
    ap.add_argument("--schema", help="only tables of this schema")
    ap.add_argument("--name", help="model name (default: DDL file name)")
    ap.add_argument("--subject-area", help="subject area name (default: model name)")
    ap.add_argument("--entity-case", choices=["title", "lower", "upper", "verbatim"], default="title")
    ap.add_argument("--attr-case", choices=["title", "lower", "upper", "verbatim"], default="lower")
    ap.add_argument("--text-length", type=int, default=255, help="VARCHAR length for text/unbounded columns")
    ap.add_argument("--date-domain", default="d_date", help="scaffold domain for date columns")
    ap.add_argument("--bool-domain", default="d_bit", help="scaffold domain for boolean columns")
    ap.add_argument("--image-ref", default="{D1F75789-86EE-4187-BF2D-5F05AB9963EF}+00000016")
    ap.add_argument("--icon-ref", default="{D1F75789-86EE-4187-BF2D-5F05AB9963EF}+00000015")
    ap.add_argument("--physical", action="store_true", help="also write Physical_Name (Logical/Physical models)")
    a = ap.parse_args(argv)
    a.name = a.name or a.ddl.stem
    a.subject_area = a.subject_area or a.name
    out = a.out or a.ddl.with_suffix(".erwin.xml")

    tables = parse_ddl(a.ddl.read_text(encoding="utf-8"), a.schema)
    if not tables:
        sys.exit("no CREATE TABLE statements found" + (f" in schema {a.schema}" if a.schema else ""))
    tree, warnings = build(tables, a)
    tree.write(str(out), xml_declaration=True, encoding="UTF-8")
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    n_rel = len(list(tree.getroot().iter(N("Relationship"))))
    print(f"wrote {out}: {len(tables)} entities, {n_rel} relationships, {len(warnings)} warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
