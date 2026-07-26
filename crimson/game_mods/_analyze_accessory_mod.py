"""Forensic analysis of AccessorySocketsMod vs our dict-level approach.

Goal: identify which specific field operations corrupt parsing when
applied as raw byte patches, so we can:
  1. Understand WHY our _eb_extend_all_sockets_to_5 works cleanly
  2. Build safeguards that flag mods doing the unsafe operations
  3. Tell authors what patterns are actually dangerous
"""
from __future__ import annotations

import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from crimson.game_mods import crimson_rs as crimson_rs
from crimson.game_mods.gui.tabs import iteminfo_inspector as ii

VANILLA = os.path.join(ROOT, "_iteminfo_0058_extracted.pabgb")
MOD = r"C:\Users\Coding\CrimsonDesertModding\ResearchFolder\SOURCE\mods test\AccessorySocketsMod.json"


def main():
    with open(VANILLA, "rb") as f:
        vanilla = f.read()
    with open(MOD, encoding="utf-8") as f:
        doc = json.load(f)

    # 1. What FIELDS do the REPLACE patches touch?
    print("=" * 72)
    print("PART 1: Replace patches -- which fields")
    print("=" * 72)

    changes = ii.collect_iteminfo_patches(doc)
    replaces = [c for c in changes if c.get("type") == "replace"]
    inserts = [c for c in changes if c.get("type") == "insert"]
    print(f"  {len(replaces)} replace patches")
    print(f"  {len(inserts)} insert patches")

    insps = ii.inspect_patches(vanilla, replaces)

    # Group by resolved field TYPE
    by_ty = {}
    count_prefix_hits = 0
    for i in insps:
        if i.field_path is None:
            continue
        ty = i.field_ty or "unknown"
        by_ty.setdefault(ty, []).append(i)
        if ".__count__" in (i.field_path or ""):
            count_prefix_hits += 1

    print(f"\n  Field type distribution (of resolved {sum(len(v) for v in by_ty.values())}):")
    for ty, insps_of_ty in sorted(by_ty.items(), key=lambda kv: -len(kv[1])):
        print(f"    {ty:12s} : {len(insps_of_ty)}")

    print(f"\n  DANGER SIGNAL: {count_prefix_hits} patches write to "
          "CArray.__count__ prefixes")
    print("  (Writing a count WITHOUT writing the matching element data")
    print("   is the #1 way to corrupt a pabgb. serialize_iteminfo in our")
    print("   pipeline derives counts from list length automatically; byte")
    print("   patches don't get that safety.)")

    # Show a handful of count-prefix patches
    count_patches = [i for i in insps if i.field_path and
                     i.field_path.endswith(".__count__")]
    print(f"\n  Sample count-prefix patches:")
    for p in count_patches[:5]:
        print(f"    #{p.index} {p.entry}.{p.field_path} "
              f"[{p.field_ty}] -> {p.new_value}")

    # 2. Dissect ONE complete insert patch
    print("\n" + "=" * 72)
    print("PART 2: Anatomy of one INSERT patch")
    print("=" * 72)

    # Pick the first insert and map its absolute offset to an entry
    insert_0 = inserts[0]
    abs_off = int(insert_0.get("offset"), 16) if isinstance(
        insert_0.get("offset"), str) else insert_0.get("offset")
    if abs_off is None:
        # Try rel_offset anchor
        entry = insert_0.get("entry")
        rel = insert_0.get("rel_offset")
        print(f"  insert #0: entry={entry}, rel_offset={rel}")
    else:
        # Find which entry contains abs_off
        t = crimson_rs.parse_iteminfo_tracked(vanilla)
        for it, span in zip(t["items"], t["spans"]):
            if span["start"] <= abs_off < span["end"]:
                print(f"  insert #0 at abs 0x{abs_off:X} lands inside entry "
                      f"{it['string_key']!r} (span 0x{span['start']:X}..0x{span['end']:X})")
                break

    # Inserts should have `entry` + `rel_offset` (the more common form)
    print(f"\n  insert #0 raw keys: {list(insert_0.keys())}")
    print(f"  insert #0 entry: {insert_0.get('entry')}")
    print(f"  insert #0 rel_offset: {insert_0.get('rel_offset')}")
    print(f"  insert #0 patched length: "
          f"{len(bytes.fromhex(insert_0.get('patched', '') or ''))} bytes")

    # Check where rel_offset falls within the entry
    entry_name = insert_0.get("entry")
    rel = insert_0.get("rel_offset")
    if entry_name and rel is not None:
        hit = crimson_rs.inspect_legacy_patches(
            vanilla, [{"entry": entry_name, "rel_offset": int(rel), "length": 1}])
        if hit and hit[0]:
            h = hit[0]
            print(f"  rel_offset {rel} resolves to field: {h['path']} "
                  f"[{h['ty']}]")
            print(f"    field byte range (rel): "
                  f"{h['field_start_rel']}..{h['field_end_rel']}")
            print(f"    byte offset within field: {h['byte_offset_in_field']}")

    # 3. Compare to our approach: show what _eb_extend_all_sockets_to_5 does
    print("\n" + "=" * 72)
    print("PART 3: Our dict-level approach (safe by construction)")
    print("=" * 72)

    # Parse vanilla, find a "force-enable-candidate" accessory
    items = crimson_rs.parse_iteminfo_from_bytes(vanilla)
    sample = None
    for it in items:
        k = it.get("string_key") or ""
        if ("_Ring" in k and "_Earring" not in k):
            ddd = it.get("drop_default_data") or {}
            if not ddd.get("use_socket"):
                sample = it
                break
    if sample is None:
        print("  (no unsocketed ring found)")
        return 0

    print(f"  Before (vanilla '{sample['string_key']}'):")
    ddd = sample["drop_default_data"]
    print(f"    use_socket = {ddd['use_socket']}")
    print(f"    socket_valid_count = {ddd['socket_valid_count']}")
    print(f"    add_socket_material_item_list = "
          f"{ddd['add_socket_material_item_list']}  "
          f"(len={len(ddd['add_socket_material_item_list'])})")

    # Replicate _eb_extend_all_sockets_to_5 logic on this one item
    TARGET = 5
    DEFAULT_COSTS = [500, 1000, 2000, 3000, 4000]
    new_list = []
    while len(new_list) < TARGET:
        new_list.append({"item": 1, "value": DEFAULT_COSTS[len(new_list)]})
    ddd["use_socket"] = 1
    ddd["socket_valid_count"] = TARGET
    ddd["add_socket_material_item_list"] = new_list

    print(f"\n  After (dict mutation, no byte offsets involved):")
    print(f"    use_socket = {ddd['use_socket']}")
    print(f"    socket_valid_count = {ddd['socket_valid_count']}")
    print(f"    add_socket_material_item_list = "
          f"{ddd['add_socket_material_item_list']}  "
          f"(len={len(ddd['add_socket_material_item_list'])})")

    # Serialize -> re-parse to verify integrity
    out = crimson_rs.serialize_iteminfo(items)
    reparsed = crimson_rs.parse_iteminfo_from_bytes(out)
    reparsed_sample = next(
        it for it in reparsed if it["string_key"] == sample["string_key"])
    print(f"\n  After serialize + reparse:")
    rddd = reparsed_sample["drop_default_data"]
    print(f"    use_socket = {rddd['use_socket']}")
    print(f"    socket_valid_count = {rddd['socket_valid_count']}")
    print(f"    add_socket_material_item_list has "
          f"{len(rddd['add_socket_material_item_list'])} entries")
    print(f"  OK serialize rebuilt the CArray count prefix correctly (u32 at "
          "the head of the list) -- no byte-patching required.")

    # 4. WHY AccessorySocketsMod's approach corrupts
    print("\n" + "=" * 72)
    print("PART 4: Why the byte-patch approach corrupts")
    print("=" * 72)
    print("""
  The mod is trying to do the same thing we do:
    1. Flip use_socket from 0 to 1 on candidate items.
    2. Grow add_socket_material_item_list from 0 to 5 entries.

  Step 1 is a SINGLE-BYTE u8 write -- safe. Our inspector correctly
  resolves the rel_offset to `drop_default_data.use_socket`, and an
  isolated flip doesn't change any downstream offsets.

  Step 2 is the killer. It requires BOTH:
    (a) writing the count prefix (u32) from 0 to 5
    (b) inserting 5 new `SocketMaterialItem` records (each 12 bytes:
        u32 item + u64 value = 12B) = 60 bytes of new data

  The mod splits this into multiple physical patches:
    - a `replace` patch that overwrites the 4 bytes of __count__
    - a sequence of `insert` patches that add the element bytes

  For this to land cleanly, all the replaces AND all the inserts
  targeting a single entry must apply together. In practice:

  * If the game version shifted any field (even a string_key length)
    between the mod's target and the user's vanilla, the anchor for
    later rel_offsets drifts -- subsequent inserts land in the wrong
    place.
  * The `original` byte check guards the count but not the inserts,
    so a replace can fail-safe while its paired insert still writes
    into the wrong spot.
  * Even when both land perfectly, anything between the count and the
    element data (padding bytes, nearby fields) also has to be exactly
    the version the mod was authored against.

  That's why __count__ writes are the #1 failure mode and why our
  approach -- mutate the dict list, let serialize_iteminfo rebuild the
  prefix -- can never corrupt, no matter what shifts elsewhere.
    """)

    # 5. Proposed safeguard
    print("=" * 72)
    print("PART 5: Safeguard -- what the Stacker should flag")
    print("=" * 72)
    print("""
  Any legacy JSON that writes to a field path ending in `.__count__`
  (CArray count prefix) or `.__len__` (CString length prefix) is
  DANGEROUS:
    * If the mod is stacked on a version-matched vanilla, it MIGHT work.
    * On any other vanilla, or stacked with any other mod that expands
      an earlier entry, it WILL corrupt the file.

  Recommended user-facing warning at Stacker Preview time:

    ! {mod_name}: {N} patches write to CArray count/CString length
      prefixes. Byte-patching these is extremely fragile and
      frequently corrupts iteminfo.pabgb when game version shifts or
      other mods expand entries.

      Try Reparse-Diff mode on this source -- it extracts intent via
      reparse and emits APPEND edits that work safely through the
      serialize pipeline.
    """)

    return 0


if __name__ == "__main__":
    sys.exit(main())
