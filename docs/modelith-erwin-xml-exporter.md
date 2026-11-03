# Modelith erwin XML exporter: instructions for an erwin 10.10 compatible export

Audience: maintainers of `modelith-dbt` (`mdl export erwin`, `mdl_emit_erd/erwin_export.py` and
`mdl_emit_erd/erwin_template.py`). Status: implemented on `fix-erwin-export-dialect`; the
**template** path (clone a native export) is the one proven to open in erwin Data Modeler 10.10.

## 1. Problem

`mdl export erwin` with the self-built envelope writes a schema-valid XML (`FileVersion="10.10…"`)
whose per-object dialect is now correct, but a from-scratch model-level envelope opens in erwin DM
10.10 as an empty `Model_1` and then freezes erwin's import engine (EAT) at 100% CPU. A native
erwin export differs from a from-scratch Modelith export in id format, enum encoding, foreign-key
structure, and — decisively — the model-level envelope (ModelProps, domains, themes, images,
naming options, page setup, data-vault objects) that erwin expects present and consistent.

The reliable path, proven against real erwin, is to **clone a native export of the target mart**
as a template, strip its content, and inject Modelith's entities into that envelope. That is what
`--template` does.

## 2. Reference dialect

Facts below were read from a native erwin export (`Logical -Combined Data Model - latest.xml`,
`FileVersion="10.10.38485"`, `Format="erwin"`). The published `ERwinSchema.xsd` is NOT the
compatibility oracle: it types the enums as free strings, and its FileVersion pattern rejects the
10.10 form erwin itself writes. A real erwin import is the only true test.

Document shape:

```
<erwin xmlns=".../dm" xmlns:UDP=".../dm/metadata" xmlns:EMX=".../dm/data" xmlns:EM2=".../dm/EM2data" FileVersion Format="erwin">
  UDP:UDP_Definition_Groups
  Model id name
    ModelEnvProps, ModelProps
    Entity_Groups, Domain_Groups, Subject_Area_Groups, Relationship_Groups,
    Name_Mapping_Groups, Naming_Options_Groups, Theme_Groups, Image_Groups,
    ER_Diagram_Groups, Annotation_Groups, Page_Setup_Groups, Data_Vault_Object_Groups
  Model_Proxy_Object
```

Every object element has an `id` and a `name` attribute, and a `<Kind>Props` child holding scalar
values as element text (`<Name>`, `<Long_Id>`, …). Properties flagged Derived, ReadOnly or Tool in
the native file (`Owner_Path`, `User_Formatted_Name`, `Dependent_Objects_Ref_Array`, fonts …) are
recomputed by erwin and can be omitted.

## 3. Required changes

### 3.1 Object ids

Format: `{UPPERCASE-GUID}+8hex`, e.g. `{0007EEEF-A625-4777-B5CB-6BCF63932FF4}+00000000`.

Minimum fix (implemented in `_guid`):

```python
h = hashlib.sha1(key.encode()).hexdigest().upper()
return f"{{{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}}}+00000000"
```

Model id suffix `+00000001`, persistence unit id suffix `+01000000`. Keep ids deterministic. Keep
preserved `erwin_id` values for objects that came from an erwin import (so a re-import matches and
UPDATES rather than duplicating).

### 3.2 Enums are numeric (or short codes), not words

| Property | Native value | Modelith (fixed) |
|---|---|---|
| Entity `Type` | `1` independent, `6` dependent (has an identifying parent) | 1/6 |
| Attribute `Type` | `0` primary-key member, `100` other | 0/100 |
| Attribute `Null_Option_Type` | `0` null, `1` not null | 0/1 |
| Relationship `Type` | `2` identifying, `7` non-identifying | 2/7 |
| Relationship `Cardinality` | `-3` one-to-zero-or-more, `-1` one-to-zero-or-one | -3/-1 |
| Relationship `Null_Option_Type` | `101` mandatory, `100` optional | 101/100 |
| `Key_Group_Type` | `PK`, `AK1`…`AKn`, `IF1`…`IFn` | PK / AKn / IFn |

Rules: a relationship is identifying when every foreign-key column is a member of the child's
primary key. It is mandatory when it is identifying or every FK column is not null. Cardinality is
`-1` when the FK columns equal the child's primary or a unique key (one to one), otherwise `-3`.

### 3.3 Write primary keys that are only inferred

Modelith infers a primary key from `role: business_key` attributes (key group "pk (from business
key)", inferred: true) but the exporter only emitted explicit key groups. Emit the inferred
primary key as an explicit `PK` key group (one `XPK<Entity>` per entity). Implemented in
`erwin_template._emit_key_groups`.

### 3.4 Foreign key structure

Native erwin does not just link two entities. Per relationship, write all of:

1. Relationship: `Key_Group_Ref` = id of the parent's primary (or alternate) key group that is
   migrated.
2. Child entity: an `IFn` key group (n = 1… per child) with `Relationship_Ref` = the relationship,
   whose members are the child's foreign-key attributes, each with `Parent_Key_Group_Member_Ref` =
   the matching member of the parent key group, and `Key_Group_Member_Order` 1…n.
3. Child attributes that are foreign-key columns: `Parent_Attribute_Ref` (parent attribute) and
   `Parent_Relationship_Ref` (the relationship). A column serving several relationships sets the
   references for the first one only.
4. Key groups carry `Key_Group_Members_Order_Ref_Array` (member ids in order); entities carry
   `Attributes_Order_Ref_Array` (primary-key attributes first).

Only emit a relationship when the referenced columns are a whole primary or unique key. erwin
migrates whole keys; a relationship to a partial key (e.g. one column of a composite primary key)
has to be dropped and reported as a warning.

### 3.5 Data types and domains

- Attributes: `Logical_Data_Type` with length, and `Parent_Domain_Ref` to a domain that exists in
  the file. Use the domains of the target mart (`String`, `Number`, `Datetime`, `d_date`, `d_bit`
  in the tested mart), not invented ones; the tested mart has no boolean type.
- Logical types seen in the native file: BINARY, BYTE, CHAR, DATE, DATETIME, FLOAT, INTEGER,
  INTERVAL, LARGE BINARY, NUMBER, NVARCHAR, TIMESTAMP, VARCHAR. BOOLEAN was not seen.

### 3.6 Names

- Entity `Name` is the logical display name (Title Case in the tested mart, "Account"); attribute
  `Name` is lowercase words ("account code"). The `ErwinNaming` config (`entity_case`,
  `attribute_case`, `replace_underscore_with_space`) already exists; apply it, and read the entity
  name from the conceptual entity.
- The tested mart is a logical model: do not write `Physical_Name` unless the target model is
  Logical/Physical (`--physical`, default off).

### 3.7 Subject area

`Subject_AreaProps`: `Name`, `Long_Id`, `Object_Order`, `Creation_Time`, `Modification_Time`,
`User_Attached_Objects_Ref_Array` (entities), `Auto_Attached_Objects_Ref_Array` (relationships).
The importer lists members with their home subject area.

## 4. Model-level scaffolding: clone, do not invent

Do not hand-build `ModelProps`, domains, themes, images, naming options, page setup and data-vault
objects. Clone them from a native export of the target mart (`--template <file.xml>`), then:

1. Empty `Entity_Groups`, `Relationship_Groups`, `Subject_Area_Groups`; remove `ER_Diagram_Groups`,
   `Annotation_Groups` and the root `Model_Proxy_Object`.
2. Remove every `Dependent_Objects_Ref_Array`.
3. In `ModelProps` remove `Proxy_Short_Id`, `Current_ER_Diagram_Ref`, `Template_Path`,
   `Template_Control_Value`, `Template_Objects`, `History_List_Array`; set `Name` and `Long_Id` to
   the new model.
4. In `ModelEnvProps` blank `Locator` (a Mart path) and set a new `Persistence_Unit_Id`.
5. Set a new `Model` id and name.
6. Drop any `*_Ref` whose id no longer exists in the file.
7. `Image_Ref` / `Icon_Image_Ref` on entities point at image ids in the template's `Image_Groups`
   (`…+00000016` / `…+00000015` in the tested mart); read them from the template instead of hard
   coding.

Implemented in `erwin_template._reset_envelope` + `_drop_dangling_refs`.

## 5. Known gaps

- No ER diagram is generated. After opening, erwin reports "This Model has no model-level diagrams"
  and the canvas shows the other open model. Workaround: save as `.erwin`, close the other model
  tab, reopen, create an ER diagram (auto populate), run auto layout. A proper fix writes
  `ER_Diagram_Groups` (entity and relationship shapes with positions from `.mdl/layout.json`) and
  sets `Current_ER_Diagram_Ref`.
- Relationship types not covered here (subtypes, many-to-many) were not exercised by the template
  path's tests.
- Text lengths are guesses (`VARCHAR(255)`): Modelith has no length in its domains.

## 6. Acceptance checks (automate these)

1. Every id matches `^\{[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}\}\+[0-9A-Fa-f]{8}$`, no duplicates.
2. Every `*_Ref` value resolves to an id or `Long_Id` in the file.
3. Every entity has a `PK` key group; every child has an `IFn` group with `Relationship_Ref`.
4. Property parity: per Entity/Attribute/Relationship/Key_Group/Key_Group_Member/Subject_Area,
   compare property names and enum values against a native export. Only deliberate differences
   (e.g. `Physical_Name`) may remain.
5. Open in erwin (File > Open > XML Files): the model loads within seconds; Entities, Relationships
   and Subject Areas counts match the Modelith subject area.

## 7. Reference implementation

`tools/ddl_to_erwin_xml.py` builds the same document from a PostgreSQL DDL script (sections 3 and
4 end to end) and produced a file that opened in erwin 10.10. Its XML for the tested mart's DDL is
structurally identical to the one Modelith generates from the model; `erwin_template.py` ports its
logic, driven by Modelith's IR instead of parsed DDL.
