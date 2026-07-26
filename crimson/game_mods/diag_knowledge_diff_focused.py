"""Diff knowledge against the two saves that have all Pailune missions
completed. Show entries present in BOTH refs but missing from user.
"""
from __future__ import annotations

import json
import os
import struct
import sys

EDITOR = r"C:\Users\Coding\CrimsonDesertModding\CrimsonSaveEditorGUI"
sys.path.insert(0, os.path.join(EDITOR, "Communitydump", "desktopeditor"))
sys.path.insert(0, EDITOR)

from crimson.game_mods.save_crypto import load_save_file  # noqa: E402
from crimson.game_mods.save_parser import build_result_from_raw  # noqa: E402

USER_SAVE = r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot100\save.save"
REF_A = r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot100\save - copy.save"
REF_B = r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot101\save - copy.save"


def get_knowledge_keys(path):
    sd = load_save_file(path)
    blob = bytes(sd.decompressed_blob)
    result = build_result_from_raw(blob, {"input_kind": "raw_blob"})
    keys = set()
    for obj in result["objects"]:
        if obj.class_name != "KnowledgeSaveData":
            continue
        for f in obj.fields:
            if f.name not in ("_knowledgeElementSaveDataList", "_list"):
                continue
            for elem in (f.list_elements or []):
                for cf in (elem.child_fields or []):
                    if cf.present and cf.name == "_key":
                        keys.add(struct.unpack_from("<I", blob, cf.start_offset)[0])
                        break
    return keys


def main():
    print("Loading saves...")
    user = get_knowledge_keys(USER_SAVE)
    ra = get_knowledge_keys(REF_A)
    rb = get_knowledge_keys(REF_B)
    print(f"  User:  {len(user)}")
    print(f"  REF_A: {len(ra)}  (slot100 save-copy)")
    print(f"  REF_B: {len(rb)}  (slot101 save-copy)")

    # Strict candidates — present in BOTH refs but NOT user
    intersect_refs = ra & rb
    missing = sorted(intersect_refs - user)
    print(f"\nIn BOTH refs but NOT in user: {len(missing)}")

    # Also, present in either ref + has Pailune/Greymane/Sunrise/GreyWolf pattern
    union_refs = ra | rb
    only_in_either_missing = sorted(union_refs - user - intersect_refs)
    print(f"In EITHER ref but NOT user (and not in BOTH): {len(only_in_either_missing)}")

    # Load name lookup
    with open(os.path.join(EDITOR, "knowledge_keys_all.json"), encoding="utf-8") as f:
        kl = json.load(f)
    name_by_key = {}
    for entry in kl:
        if isinstance(entry, dict) and "key" in entry:
            name_by_key[entry["key"]] = (
                entry.get("name", "?"),
                entry.get("display_name", ""),
            )

    # Filter terms — anything that could be the Grounds of the Sunrise card
    SUNRISE_TERMS = (
        "Pailune", "Greywolf", "GreyWolf", "GreyMane", "Greymane", "Graymane",
        "Sunrise", "Marquis", "Serkis", "Howling", "Reconstructing", "Pailoon",
        "Hope", "Reuniting", "Comrades", "Asbjorn",
    )

    print("\n" + "=" * 78)
    print("Strict candidates (in BOTH refs, missing from user) — Sunrise/Pailune themed:")
    print("=" * 78)
    sunrise_strict = []
    other_strict = []
    for k in missing:
        nm, disp = name_by_key.get(k, ("UNKNOWN_KEY", ""))
        line = f"  {k:>10}  {nm:<55} \"{disp}\""
        if any(t in nm for t in SUNRISE_TERMS) or any(t in disp for t in SUNRISE_TERMS):
            sunrise_strict.append(line)
        else:
            other_strict.append(line)

    if sunrise_strict:
        for line in sunrise_strict:
            print(line)
    else:
        print("  (none match Sunrise/Pailune name patterns)")

    print(f"\n--- Other strict candidates ({len(other_strict)}) ---")
    for line in other_strict[:50]:
        print(line)
    if len(other_strict) > 50:
        print(f"  ... +{len(other_strict)-50} more")


if __name__ == "__main__":
    main()
