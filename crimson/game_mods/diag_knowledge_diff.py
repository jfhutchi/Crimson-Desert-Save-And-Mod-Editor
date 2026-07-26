"""Find the missing knowledge entry that's blocking 37/38 in
'Grounds of the Sunrise'.

Approach:
  - Parse KnowledgeSaveData from user save and from each reference save
  - Build set difference: keys in any reference save but NOT in user
  - Cross-reference each missing key against knowledge_keys_all.json
  - Highlight entries with names containing Graymane / Pailune / Greywolf /
    Sunrise / GreyMane / etc. (what 'Grounds of the Sunrise' covers)
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
REF_SAVES = [
    r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot100\save - copy.save",
    r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot101\save - copy.save",
    r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot108\save.save",
]

# Filter terms — knowledge names matching these likely belong to Greymane region
GREYMANE_TERMS = (
    "Graymane", "Greymane", "GreyMane", "Greywolf", "GreyWolf",
    "Pailune", "Sunrise", "GreyMane",
    "Howling", "Marquis", "Serkis",
)


def u32(blob, off):
    return struct.unpack_from("<I", blob, off)[0]


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
                        keys.add(u32(blob, cf.start_offset))
                        break
    return keys


def main():
    print("Loading user save...")
    user_keys = get_knowledge_keys(USER_SAVE)
    print(f"  User has {len(user_keys)} knowledge entries\n")

    all_ref_keys = set()
    for ref in REF_SAVES:
        if not os.path.exists(ref):
            print(f"  REF MISSING: {ref}")
            continue
        try:
            ks = get_knowledge_keys(ref)
            print(f"  Ref {os.path.basename(os.path.dirname(ref))}: {len(ks)} entries")
            all_ref_keys |= ks
        except Exception as e:
            print(f"  Ref {ref}: ERROR {e}")

    print(f"\nUnion of all reference knowledge: {len(all_ref_keys)} entries")
    missing = all_ref_keys - user_keys
    print(f"Missing from user save: {len(missing)} entries\n")

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

    # Highlight Greymane-related missing entries
    print("=" * 78)
    print("Missing entries possibly under 'Grounds of the Sunrise' (Graymane/Pailune/Sunrise):")
    print("=" * 78)
    greymane_missing = []
    other_missing = []
    for k in sorted(missing):
        nm, disp = name_by_key.get(k, ("UNKNOWN", ""))
        line = f"  key={k:<10} {nm:<55} \"{disp}\""
        if any(t in nm for t in GREYMANE_TERMS) or any(t in disp for t in GREYMANE_TERMS):
            greymane_missing.append(line)
        else:
            other_missing.append(line)

    if greymane_missing:
        for line in greymane_missing:
            print(line)
    else:
        print("  (no missing entries match Greymane/Pailune/Sunrise patterns)")

    print(f"\nOther missing entries: {len(other_missing)}")
    for line in other_missing[:30]:
        print(line)
    if len(other_missing) > 30:
        print(f"  ... +{len(other_missing) - 30} more")

    # Also: present in user but not in any reference (shouldn't happen if user is most-progressed)
    extra = user_keys - all_ref_keys
    print(f"\nPresent in user but NOT in any reference: {len(extra)} entries")
    for k in sorted(extra)[:15]:
        nm, disp = name_by_key.get(k, ("UNKNOWN", ""))
        print(f"  key={k:<10} {nm:<55} \"{disp}\"")


if __name__ == "__main__":
    main()
