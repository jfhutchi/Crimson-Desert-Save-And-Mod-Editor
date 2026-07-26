"""Show all Pailune-related knowledge entries the USER currently has,
so we can see which Pailune entries are present and find what's missing
by comparing card-membership patterns.
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
REF_B = r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot101\save - copy.save"


def get_keys(path):
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
    user = get_keys(USER_SAVE)
    ref = get_keys(REF_B)
    print(f"User: {len(user)}, Ref: {len(ref)}\n")

    with open(os.path.join(EDITOR, "knowledge_keys_all.json"), encoding="utf-8") as f:
        kl = json.load(f)
    name_by_key = {}
    for entry in kl:
        if isinstance(entry, dict) and "key" in entry:
            name_by_key[entry["key"]] = (
                entry.get("name", "?"),
                entry.get("display_name", ""),
            )

    # Pailune/Greymane patterns — broader filter
    PATTERNS = [
        "Pailune", "Pailoon", "Pailunese", "Pai_",
        "Greymane", "Graymane", "GreyMane", "Grey_Mane",
        "GreyWolf", "Greywolf",
        "Sunrise", "Howling", "Marquis", "Serkis",
        "Kwe_", "Kwe ",  # Pailune region prefix
        "Hope_", "Hope ", "Reuniting",
    ]

    user_pailune = []
    ref_only_pailune = []

    for k in sorted(ref | user):
        nm, disp = name_by_key.get(k, ("UNKNOWN", ""))
        is_pailune = any(p in nm for p in PATTERNS) or any(p in disp for p in PATTERNS)
        if not is_pailune:
            continue
        in_user = k in user
        in_ref = k in ref
        line = f"  {'U' if in_user else ' '}{'R' if in_ref else ' '}  " \
               f"{k:>10}  {nm:<55} \"{disp}\""
        if in_user:
            user_pailune.append(line)
        else:
            ref_only_pailune.append(line)

    print(f"== PAILUNE/GREYMANE knowledge USER HAS ({len(user_pailune)}) ==")
    for line in user_pailune:
        print(line)

    print(f"\n== PAILUNE/GREYMANE knowledge in REF but NOT user ({len(ref_only_pailune)}) ==")
    for line in ref_only_pailune:
        print(line)


if __name__ == "__main__":
    main()
