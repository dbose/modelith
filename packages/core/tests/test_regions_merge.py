from mdl_core.fingerprint import content_hash
from mdl_core.merge import MergeOutcome, merge_file
from mdl_core.regions import RegionKind, parse, render

_FILE = """\
-- mdl:generated-begin id=01ABC fingerprint=sha256:aaa spec=v1
select 1 as x
-- mdl:generated-end

-- mdl:user-begin
select 2 as y
-- mdl:user-end
"""


def test_parse_render_roundtrip():
    parsed = parse(_FILE, "--")
    assert render(parsed, "--") == _FILE


def test_parse_identifies_regions():
    parsed = parse(_FILE, "--")
    kinds = [r.kind for r in parsed.regions]
    assert RegionKind.generated in kinds
    assert RegionKind.user in kinds
    gen = parsed.generated_regions()[0]
    assert gen.obj_id == "01ABC"
    assert gen.fingerprint == "sha256:aaa"


def test_merge_new_file_created():
    r = merge_file(path="m.sql", base_hash=None, ours=None, theirs=_FILE)
    assert r.outcome == MergeOutcome.created
    assert r.content == _FILE


def test_merge_clean_when_ours_equals_base():
    base = content_hash(_FILE)
    theirs = _FILE.replace("select 1 as x", "select 1 as x, 3 as z")
    r = merge_file(path="m.sql", base_hash=base, ours=_FILE, theirs=theirs)
    assert r.outcome == MergeOutcome.clean_written
    assert r.content == theirs  # took regenerated


def test_merge_keeps_ours_when_model_unchanged():
    base = content_hash(_FILE)
    ours = _FILE.replace("select 2 as y", "select 2 as y -- edited user region")
    r = merge_file(path="m.sql", base_hash=base, ours=ours, theirs=_FILE)
    assert r.outcome == MergeOutcome.unchanged
    assert r.content == ours


def test_user_edit_of_generated_block_flags_e201():
    # ours edits the *generated* body; theirs (fresh gen) has same fingerprint.
    base_content = _FILE
    ours = _FILE.replace("select 1 as x", "select 1 as x -- HAND EDIT")
    # base_hash != ours (so we go to region merge); theirs == original generation
    r = merge_file(path="m.sql", base_hash="sha256:different", ours=ours, theirs=base_content)
    codes = {d.code for d in r.diagnostics}
    assert "MDL-E201" in codes
    # user's edit is preserved, not clobbered
    assert "HAND EDIT" in r.content


def test_fingerprint_change_with_user_edit_conflicts():
    ours = _FILE.replace("select 1 as x", "select 1 as x -- HAND EDIT")
    theirs = _FILE.replace(
        "fingerprint=sha256:aaa", "fingerprint=sha256:bbb"
    ).replace("select 1 as x", "select 1 as x, 99 as w")
    r = merge_file(path="m.sql", base_hash="sha256:orig", ours=ours, theirs=theirs)
    assert r.outcome == MergeOutcome.conflict
    assert "MDL-C301" in {d.code for d in r.diagnostics}
    assert "<<<<<<<" in r.content and ">>>>>>>" in r.content
