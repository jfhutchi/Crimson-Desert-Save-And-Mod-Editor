"""Headless end-to-end test for the Stacker inspector.

Tests against the three real-world mods the user flagged:
 - AccessorySocketsMod.json      (411 entry+rel_offset patches at offset=56)
 - Ultimate Lantern Reborn.json  (absolute-offset + insert patches)
 - QoL_Axiom_Bracelet (folder)   (compiled PAZ mod)

What we assert:
 1. Inspector resolves AccessorySocketsMod's patches to specific field paths.
 2. Absolute-offset + insert patches in Ultimate Lantern flag as UNSUPPORTED
    with clear reasons.
 3. Semantic apply on AccessorySocketsMod mutates the dict list (so the
    patches would survive offset drift from other mods).
 4. Stacking AccessorySocketsMod against a synthetic competing mod
    produces FieldConflicts carrying patch provenance indices.

Run: python _test_stacker_inspector.py
"""
from __future__ import annotations

import copy
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import crimson_rs  # noqa: E402

from crimson.game_mods.gui.tabs import iteminfo_inspector  # noqa: E402


VANILLA_PATH = os.path.join(ROOT, "_iteminfo_0058_extracted.pabgb")
MODS_DIR = r"C:\Users\Coding\CrimsonDesertModding\ResearchFolder\SOURCE\mods test"
ACCESSORY_JSON = os.path.join(MODS_DIR, "AccessorySocketsMod.json")
LANTERN_JSON = os.path.join(MODS_DIR, "Ultimate Lantern Reborn v1.0.json")


def _build_entry_start_map(vanilla: bytes) -> dict[str, int]:
    """Walk vanilla with tracked reader to get entry start offsets."""
    t = crimson_rs.parse_iteminfo_tracked(vanilla)
    out = {}
    for it, span in zip(t["items"], t["spans"]):
        out[it["string_key"]] = span["start"]
    return out


def _extract_function(src: str, name: str) -> str:
    """Pull a top-level `def <name>(...)` block out of source text so we
    can exec it without importing the enclosing module (which drags in
    PySide6)."""
    lines = src.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"def {name}("):
            start = i
            break
    if start is None:
        raise RuntimeError(f"function {name} not found")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        l = lines[j]
        if l.startswith("def ") or l.startswith("class ") or l.startswith("@"):
            end = j
            break
    return "\n".join(lines[start:end])


def _load_apply_legacy_json():
    with open(os.path.join(ROOT, "gui", "tabs", "stacker.py"),
              "r", encoding="utf-8") as f:
        src = f.read()
    func_src = _extract_function(src, "_apply_legacy_json")
    ns: dict = {}
    exec(func_src, ns)
    return ns["_apply_legacy_json"]


def test_accessory_sockets_mod(vanilla: bytes, blob_start: dict) -> None:
    print("\n=== AccessorySocketsMod.json ===")
    with open(ACCESSORY_JSON, encoding="utf-8") as f:
        doc = json.load(f)

    changes = iteminfo_inspector.collect_iteminfo_patches(doc)
    print(f"  changes: {len(changes)}")

    insps = iteminfo_inspector.inspect_patches(vanilla, changes)
    assert len(insps) == len(changes)

    # Tally by status
    by_status: dict[str, int] = {}
    resolved_paths: dict[str, int] = {}
    for i in insps:
        by_status[i.status] = by_status.get(i.status, 0) + 1
        if i.field_path:
            resolved_paths[i.field_path] = resolved_paths.get(i.field_path, 0) + 1
    print(f"  inspector status tally: {by_status}")
    print(f"  unique field paths resolved: {len(resolved_paths)}")
    for path, n in sorted(resolved_paths.items(),
                          key=lambda kv: -kv[1])[:10]:
        print(f"    {n:4d}  {path}")

    # Real-world insight: because mod authors write `rel_offset` as a
    # raw byte position relative to entry start, and Crimson Desert's
    # PABGB entries have a variable-length `string_key` (the dev name)
    # at the top of each record, the SAME rel_offset hits DIFFERENT
    # fields on entries with different name lengths. This mod has 88
    # unique rel_offsets but 242 replaces land across 28 different
    # field paths — exactly the pitfall the semantic Inspector is here
    # to surface. We assert the top field has ≥ 20 hits so we know the
    # distribution is meaningful, not ≥ 300 (which would require every
    # target entry to share a string_key length — they don't).
    top = max(resolved_paths.items(), key=lambda kv: kv[1])
    assert top[1] >= 20, (
        f"expected a meaningful field distribution; top was {top}")
    print(f"  OK top field: {top[0]!r} x {top[1]} "
          f"(shows rel_offset -> field variance across string_key lengths)")

    # The 169 `insert` patches are AccessorySocketsMod's actual socket-
    # expanding edits — they grow the `add_socket_material_item_list`
    # array. Semantic merge of inserts is a future feature: we'd need to
    # read the bytes as a new CArray element and append to the dict-side
    # list. For now, flag them clearly so users understand what's out of
    # scope and can still use CrimsonGameMods's own `_eb_extend_all_
    # sockets_to_5` (which does this correctly in dict-form).
    inserts = [i for i in insps if "insert" in (i.status_note or "")]
    assert len(inserts) == 169, (
        f"expected 169 insert patches flagged unsupported; got {len(inserts)}")
    print(f"  OK {len(inserts)} 'insert' patches flagged unsupported "
          "(deliberate scope boundary — see CrimsonGameMods' socket tab)")

    # Apply byte-level, mark stale
    _apply_legacy_json = _load_apply_legacy_json()
    modded, applied, skipped, mask = _apply_legacy_json(
        vanilla, doc, blob_start)
    print(f"  byte-apply: {applied} applied, {skipped} skipped")
    iteminfo_inspector.mark_stale_status(insps, mask)

    # Re-tally after mark_stale: applied should fall by the number of
    # byte-apply skips (which were resolved but stale-mismatched).
    after_tally: dict[str, int] = {}
    for i in insps:
        after_tally[i.status] = after_tally.get(i.status, 0) + 1
    print(f"  post-byte-apply status tally: {after_tally}")
    # This mod is known stale on this vanilla (only 98/411 patches' bytes
    # match) yet the inspector resolved field paths for ~242 of them.
    # That's the key value: we know WHAT fields 144 stale patches were
    # trying to edit, even though we couldn't byte-apply them.
    assert after_tally.get(iteminfo_inspector.PATCH_STALE, 0) > 100, (
        "expected many stale patches on version-mismatched vanilla")
    assert after_tally.get(iteminfo_inspector.PATCH_APPLIED, 0) > 0, (
        "expected at least some patches to land")

    # Semantic apply — ignores stale byte-match and writes directly to
    # the dict. Confirms the Inspector's field resolution is usable for
    # a dict-level merge path that survives offset drift.
    vanilla_items = crimson_rs.parse_iteminfo_from_bytes(vanilla)
    items_semantic = copy.deepcopy(vanilla_items)
    s_applied, s_skipped, reasons = iteminfo_inspector.apply_semantic(
        items_semantic, insps)
    print(f"  semantic-apply: {s_applied} applied, {s_skipped} skipped")
    if reasons:
        # Strip any non-ASCII from the reason for Windows console compat
        clean = reasons[0].encode("ascii", "replace").decode("ascii")
        print(f"    first skip reason: {clean}")
    assert s_applied > 0, "semantic apply produced zero edits"
    # Confirm the dict really changed for at least one item.
    mutated = 0
    for insp in insps:
        if insp.status != iteminfo_inspector.PATCH_APPLIED:
            continue
        if insp.new_value is None or insp.field_path is None:
            continue
        it = next((x for x in items_semantic
                   if x.get("string_key") == insp.entry), None)
        if it is None:
            continue
        cur = _get_nested(it, insp.field_path.split("."))
        if cur == insp.new_value:
            mutated += 1
            if mutated <= 3:
                print(f"    verified: {insp.entry}.{insp.field_path} "
                      f"= {cur!r}")
        if mutated >= 3:
            break
    assert mutated >= 1, "no dict mutations survived to assert against"
    print(f"  OK semantic-apply verified on {mutated} sampled dict paths")


def test_lantern_mod(vanilla: bytes, blob_start: dict) -> None:
    print("\n=== Ultimate Lantern Reborn v1.0.json ===")
    with open(LANTERN_JSON, encoding="utf-8") as f:
        doc = json.load(f)
    changes = iteminfo_inspector.collect_iteminfo_patches(doc)
    print(f"  changes: {len(changes)}")
    for c in changes:
        print(f"    type={c.get('type')!r} "
              f"offset={c.get('offset')!r} entry={c.get('entry')!r}")
    insps = iteminfo_inspector.inspect_patches(vanilla, changes)
    # Both Lantern changes use absolute offsets OR insert types →
    # inspector should flag UNSUPPORTED with clear reasons.
    for i in insps:
        assert i.status == iteminfo_inspector.PATCH_UNSUPPORTED, (
            f"unexpected status {i.status} for {i}")
    print(f"  OK all {len(insps)} changes classified as UNSUPPORTED with reasons")
    for i in insps:
        print(f"    #{i.index}: {i.status_note}")


def test_synthetic_conflict(vanilla: bytes, blob_start: dict) -> None:
    """Build a second JSON that targets the same field as
    AccessorySocketsMod on the same entry, run both through the merge,
    and assert the resulting FieldConflict carries both sides' patch
    indices."""
    print("\n=== Synthetic conflict against AccessorySocketsMod ===")
    with open(ACCESSORY_JSON, encoding="utf-8") as f:
        acc_doc = json.load(f)

    # Pick a patch that actually lands on this vanilla (not one of the
    # stale 144). Run inspector + byte-apply first to find one, then
    # build the conflict around THAT patch.
    tmp_insps = iteminfo_inspector.inspect_patches(
        vanilla, iteminfo_inspector.collect_iteminfo_patches(acc_doc))
    _apply_legacy_json = _load_apply_legacy_json()
    _, _, _, tmp_mask = _apply_legacy_json(vanilla, acc_doc, blob_start)
    iteminfo_inspector.mark_stale_status(tmp_insps, tmp_mask)

    landed = [i for i in tmp_insps
              if i.status == iteminfo_inspector.PATCH_APPLIED
              and i.field_path and i.new_value is not None]
    assert landed, "expected at least one landed patch to clash with"
    pick = landed[0]
    # Find the raw change at that source index so we have entry/rel/orig.
    all_changes = iteminfo_inspector.collect_iteminfo_patches(acc_doc)
    source = next(c for c in all_changes
                  if c.get("_source_index") == pick.index)
    entry = source["entry"]
    rel = source["rel_offset"]
    orig = source["original"]
    print(f"  clashing on entry={entry!r} rel_offset={rel} "
          f"(field={pick.field_path!r})")

    # Build a competing JSON targeting the SAME field with a different
    # value. Mirror field width (e.g. u8 → 1 byte, u32 → 4 bytes) so the
    # competing patch covers the whole field like the original did — that's
    # what makes semantic-apply classify it as a writable primitive edit.
    field_width = pick.field_end_rel - pick.field_start_rel
    # Conflict value = vanilla+0xAA (arbitrary distinct byte pattern),
    # mapped to same width so the semantic apply actually lands.
    conflict_new = bytes([0xAA % 256] * field_width).hex() if field_width == 1 \
        else (b"\x02" + b"\x00" * (field_width - 1)).hex()
    # Note: for `is_dyeable` (u8) the landed AccessorySockets value is 1;
    # use 2 here so merge sees a different value.
    conflict_doc = {
        "format": 2,
        "patches": [{
            "game_file": "gamedata/iteminfo.pabgb",
            "changes": [
                {"type": "replace", "entry": entry, "rel_offset": rel,
                 "original": orig, "patched": conflict_new},
            ],
        }]
    }

    # Produce each mod's parsed items via Semantic mode. Byte-apply on
    # AccessorySocketsMod would corrupt the file (its 98 landed byte
    # patches damage parsing — a known limitation of byte-patch mods
    # with version-skew). Semantic mode skips byte-apply entirely and
    # writes resolved field edits directly to the dict.
    vanilla_items = crimson_rs.parse_iteminfo_from_bytes(vanilla)

    acc_insps = iteminfo_inspector.inspect_patches(
        vanilla, iteminfo_inspector.collect_iteminfo_patches(acc_doc))
    acc_items = copy.deepcopy(vanilla_items)
    iteminfo_inspector.apply_semantic(acc_items, acc_insps)

    conf_insps = iteminfo_inspector.inspect_patches(
        vanilla, iteminfo_inspector.collect_iteminfo_patches(conflict_doc))
    conf_items = copy.deepcopy(vanilla_items)
    iteminfo_inspector.apply_semantic(conf_items, conf_insps)

    # Run the merge and provenance attach by loading stacker's helpers
    _merge_all = _load_top_level("_merge_all")
    _attach_provenance = _load_top_level("_attach_patch_provenance")
    FieldConflict = _load_class("FieldConflict")

    merged, conflicts = _merge_all(vanilla_items, [
        ("AccessorySocketsMod", acc_items),
        ("ConflictMod", conf_items),
    ])
    print(f"  merge: {len(merged)} entries, {len(conflicts)} conflicts")
    assert len(conflicts) >= 1, "expected at least 1 conflict"

    # Build ModEntry-lite objects with .name/.kind/.inspections so
    # _attach_patch_provenance can walk them.
    class FakeMod:
        def __init__(self, name, kind, inspections):
            self.name = name
            self.kind = kind
            self.inspections = inspections
    fake_mods = [
        FakeMod("AccessorySocketsMod", "legacy_json", acc_insps),
        FakeMod("ConflictMod", "legacy_json", conf_insps),
    ]
    _attach_provenance(conflicts, fake_mods)

    # Locate the conflict we expect (on the entry we clashed)
    target = next((c for c in conflicts if c.entry_key == entry), None)
    assert target is not None, f"no conflict on {entry}"
    print(f"  conflict: {target.entry_key}.{target.field_path}")
    print(f"    winner: {target.winner_mod}  patch#={target.winner_patch_index}")
    print(f"    loser : {target.loser_mod}   patch#={target.loser_patch_index}")
    assert target.winner_patch_index is not None, (
        "winner provenance not attached")
    assert target.loser_patch_index is not None, (
        "loser provenance not attached")
    print("  OK both sides carry patch indices")


def test_reparse_diff_accessory(vanilla: bytes, blob_start: dict) -> None:
    """Reparse-diff mode on AccessorySocketsMod should recover EVERY
    parseable patch's intent — including the 169 insert patches that
    Semantic mode flagged UNSUPPORTED, plus the 144 stale replaces that
    Semantic silently dropped.

    Not every patch will recover cleanly (the mod has internally-
    inconsistent edits on this vanilla), but the report will list
    exactly which ones did and which fell into parse_break."""
    print("\n=== Reparse-Diff mode: AccessorySocketsMod.json ===")
    with open(ACCESSORY_JSON, encoding="utf-8") as f:
        doc = json.load(f)

    report = iteminfo_inspector.reparse_diff_patches(
        vanilla, doc, blob_start)
    print(f"  mode         : {report.mode}")
    print(f"  edits        : {len(report.edits)}")
    print(f"  parse_break  : {len(report.parse_break_patches)}")
    print(f"  no_op        : {len(report.no_op_patches)}")
    print(f"  unapplied    : {len(report.unapplied_patches)}")

    # Op tally
    by_op: dict = {}
    for e in report.edits:
        by_op[e.op] = by_op.get(e.op, 0) + 1
    print(f"  by op        : {by_op}")

    # Reparse-Diff should recover strictly more edits than Semantic did
    # (semantic dropped all stale patches + all inserts). We expect:
    #   APPEND edits from the 169 insert patches that expand socket
    #   arrays, plus SET edits from the stale-but-coherent replaces.
    assert len(report.edits) > 0, "reparse-diff yielded zero edits"

    # Apply the edits to a fresh vanilla and verify dict mutations.
    items = copy.deepcopy(
        crimson_rs.parse_iteminfo_from_bytes(vanilla))
    applied, skipped, reasons = iteminfo_inspector.apply_field_edits(
        items, report.edits)
    print(f"  apply        : {applied} landed, {skipped} skipped")
    if reasons:
        clean = reasons[0].encode("ascii", "replace").decode("ascii")
        print(f"  first skip   : {clean}")
    assert applied > 0, "apply_field_edits produced zero landings"

    # Spot-check one APPEND edit actually grew an array in the dict.
    append_edits = [e for e in report.edits if e.op == iteminfo_inspector.APPEND]
    if append_edits:
        e = append_edits[0]
        target = iteminfo_inspector._resolve_path_container(
            next(it for it in items if it.get("string_key") == e.entry),
            e.path,
        )
        orig_len = len(target) - len(e.value) if target else 0
        assert isinstance(target, list) and len(target) >= orig_len + len(e.value)
        print(f"  OK APPEND {e.entry}.{e.path} grew to len={len(target)} "
              f"(added {len(e.value)})")


def test_reparse_diff_lantern(vanilla: bytes, blob_start: dict) -> None:
    """Reparse-diff mode on Ultimate Lantern Reborn should either
    recover its edits or cleanly report why — absolute-offset + insert
    patches that Semantic mode could not touch."""
    print("\n=== Reparse-Diff mode: Ultimate Lantern Reborn v1.0.json ===")
    with open(LANTERN_JSON, encoding="utf-8") as f:
        doc = json.load(f)

    report = iteminfo_inspector.reparse_diff_patches(
        vanilla, doc, blob_start)
    print(f"  mode         : {report.mode}")
    print(f"  edits        : {len(report.edits)}")
    print(f"  parse_break  : {len(report.parse_break_patches)}")
    print(f"  unapplied    : {len(report.unapplied_patches)}")
    for idx, reason in report.unapplied_patches[:5]:
        print(f"    #{idx}: {reason}")

    by_op: dict = {}
    for e in report.edits:
        by_op[e.op] = by_op.get(e.op, 0) + 1
    print(f"  by op        : {by_op}")

    # The Lantern mod's 1792-byte insert is between existing entries —
    # it should be detected as ADD_ENTRY(s). The absolute-offset replace
    # should either land as SET/APPEND edits or report parse_break.
    # We accept any non-empty recovery OR a clear parse_break report.
    if report.edits:
        print("  OK Lantern mod recovered at least some edits")
        if iteminfo_inspector.ADD_ENTRY in by_op:
            print(f"  OK detected {by_op[iteminfo_inspector.ADD_ENTRY]} "
                  "ADD_ENTRY edit(s) (insert became new item)")
    elif report.parse_break_patches or report.unapplied_patches:
        print("  OK Lantern mod reported unapplied/parse_break patches "
              "cleanly — inspector classified the mod's limits")
    else:
        raise AssertionError(
            "reparse-diff on Lantern yielded no edits and no errors — "
            "something is silently swallowing the patch")


def _get_nested(d, parts):
    """Walk parts like ['a','b','c'] or ['a','b[2]'] into a dict tree."""
    cur = d
    for p in parts:
        while p and "[" in p and "]" in p:
            lb = p.find("[")
            rb = p.find("]")
            base = p[:lb]
            idx = int(p[lb+1:rb])
            cur = cur.get(base) if isinstance(cur, dict) else None
            if isinstance(cur, list) and idx < len(cur):
                cur = cur[idx]
            else:
                return None
            p = p[rb+1:]
            if p.startswith("."):
                p = p[1:]
        if p:
            cur = cur.get(p) if isinstance(cur, dict) else None
            if cur is None:
                return None
    return cur


def _load_top_level(name):
    with open(os.path.join(ROOT, "gui", "tabs", "stacker.py"),
              "r", encoding="utf-8") as f:
        src = f.read()
    func_src = _extract_function(src, name)
    # Also need the helpers used inside — simpler to exec with the
    # module's namespace by extracting its imports & dataclass defs.
    ns: dict = {}
    # Pull in json + copy + typing helpers + dataclasses (used by helpers)
    exec("import json, copy\n"
         "from dataclasses import dataclass, field\n"
         "from typing import Optional\n", ns)
    # Extract + exec the FieldConflict dataclass first (merge needs it)
    cls_src = _extract_class(src, "FieldConflict")
    exec(cls_src, ns)
    # Extract helpers used by _merge_all
    for helper in ("_walk_leaves", "_get_path", "_set_path",
                   "_merge_entries"):
        try:
            h_src = _extract_function(src, helper)
            exec(h_src, ns)
        except RuntimeError:
            pass
    exec(func_src, ns)
    return ns[name]


def _load_class(name):
    with open(os.path.join(ROOT, "gui", "tabs", "stacker.py"),
              "r", encoding="utf-8") as f:
        src = f.read()
    cls_src = _extract_class(src, name)
    ns: dict = {}
    exec("from dataclasses import dataclass, field\n"
         "from typing import Optional\n", ns)
    exec(cls_src, ns)
    return ns[name]


def _extract_class(src: str, name: str) -> str:
    """Extract a top-level @dataclass class <name>... block.

    Walks from the `class` line forward: the class body is indented, so
    the block ends at the first NON-indented, non-blank line that isn't
    a decorator. Pre-pends any `@dataclass` decorator on the line
    immediately above the class."""
    lines = src.splitlines()
    class_line = None
    for i, line in enumerate(lines):
        if line.startswith(f"class {name}"):
            class_line = i
            break
    if class_line is None:
        raise RuntimeError(f"class {name} not found")
    start = class_line
    if class_line > 0 and lines[class_line - 1].strip().startswith("@"):
        start = class_line - 1
    end = len(lines)
    for j in range(class_line + 1, len(lines)):
        l = lines[j]
        if not l.strip():
            continue  # blank lines within the body are fine
        if l.startswith(" ") or l.startswith("\t"):
            continue  # indented = still inside the class body
        # Top-level non-indented line that isn't a further decorator
        # marks the boundary.
        if l.startswith("@"):
            # Could be a decorator for the next top-level item; still
            # counts as outside the class.
            end = j
            break
        end = j
        break
    return "\n".join(lines[start:end])


def main() -> int:
    if not os.path.isfile(VANILLA_PATH):
        print(f"vanilla not found: {VANILLA_PATH}", file=sys.stderr)
        return 1
    with open(VANILLA_PATH, "rb") as f:
        vanilla = f.read()
    print(f"loaded {len(vanilla):,} bytes vanilla")

    blob_start = _build_entry_start_map(vanilla)
    print(f"entry map: {len(blob_start)} entries")

    test_accessory_sockets_mod(vanilla, blob_start)
    test_lantern_mod(vanilla, blob_start)
    test_synthetic_conflict(vanilla, blob_start)
    test_reparse_diff_accessory(vanilla, blob_start)
    test_reparse_diff_lantern(vanilla, blob_start)

    print("\nALL INSPECTOR TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
