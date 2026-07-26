"""Inject the missing Pailune/Greymane knowledge entries into user's save.

Uses parc_inserter3.inject_knowledge_fast which is the production knowledge
injector (used by the save editor's existing UI button).

Backup: _backup_pailune_20260416_042753/save.save

Strategy: re-run the diff first to get a fresh missing-keys list, then
filter to Pailune/Greymane/Sunrise-named entries (the candidates that
could possibly be under the 'Grounds of the Sunrise' UI card).
"""
from __future__ import annotations

import json
import os
import struct
import sys

EDITOR = r"C:\Users\Coding\CrimsonDesertModding\CrimsonSaveEditorGUI"
sys.path.insert(0, os.path.join(EDITOR, "Communitydump", "desktopeditor"))
sys.path.insert(0, EDITOR)

from crimson.game_mods.save_crypto import load_save_file, write_save_file  # noqa: E402
from crimson.game_mods.save_parser import build_result_from_raw  # noqa: E402
import parc_inserter3 as pi  # noqa: E402

USER_SAVE = r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot100\save.save"

# Keys identified from prior diff (Greymane/Pailune/Sunrise candidates only)
PAILUNE_GREYMANE_KEYS = [
    41025,    # Knowledge_Dungeon_0025                       "Pailune Secret Vault"
    42036,    # Knowledge_Pailune_AncientMine_No1_Ruins
    42037, 42038, 42039, 42040, 42042, 42043,
    42049,    # Knowledge_Pailune_Ancients_Cave              "Cave of the Ancients"
    1000391,  # Knowledge_Kwe_DyeColor_1
    1000392, 1000452, 1000453,
    1000544,  # Serkis Study
    1000654,  # Serkis Clue
    1000900,  # GreyMane Temple Bird
    1000958,  # Knowledge_OccupyPailune                      "Pailune Conquered"  <-- prime suspect
    1001184,  # Knowledge_Node_Rivercrest_GreyManeTemple
    1001185, 1001186, 1001255,
    1001566,  # Knowledge_KnightCeremony_Greymane            "Bestowal of Title"
    1001937,  # Serkis Book Thief
    1001998, 1001999, 1002000, 1002004, 1002005, 1002006,
    1002069, 1002081, 1002108, 1002233,  # Permit_Pai_*
    1003372,  # Knowledge_Node_Kwe_Pailune_RestArea_0001     "Pailune Castle Hearth"
    1003420,  # Knowledge_Node_Her_RestArea_0053             "Sunrise Shade Hearth"
    1003752,  # Knowledge_Visione_Chip_PailuneSecretBase
]


def main():
    print(f"Loading {USER_SAVE}")
    sd = load_save_file(USER_SAVE)
    blob = bytearray(sd.decompressed_blob)
    orig_size = len(blob)
    print(f"Decompressed: {orig_size:,} bytes")

    # Confirm what's already present vs needed
    print("\nParsing to determine which target keys are absent...")
    result = build_result_from_raw(bytes(blob), {"input_kind": "raw_blob"})
    have = set()
    for obj in result["objects"]:
        if obj.class_name != "KnowledgeSaveData":
            continue
        for f in obj.fields:
            if f.name not in ("_knowledgeElementSaveDataList", "_list"):
                continue
            for elem in (f.list_elements or []):
                for cf in (elem.child_fields or []):
                    if cf.present and cf.name == "_key":
                        have.add(struct.unpack_from("<I", blob, cf.start_offset)[0])
                        break

    target = set(PAILUNE_GREYMANE_KEYS)
    missing = sorted(target - have)
    already = sorted(target & have)
    print(f"  User already has {len(already)} of these knowledge keys")
    print(f"  User is missing  {len(missing)} of these knowledge keys")
    if not missing:
        print("All target knowledge already present — nothing to do.")
        return 0

    print(f"\nWill inject these {len(missing)} keys:")
    for k in missing:
        print(f"  {k}")

    print("\nCalling inject_knowledge_fast...")
    ok, new_blob, msg = pi.inject_knowledge_fast(blob, keys_filter=missing)
    if not ok:
        print(f"  FAILED: {msg}")
        return 1
    print(f"  OK: {msg}")
    print(f"  Blob size: {orig_size:,} -> {len(new_blob):,} "
          f"(+{len(new_blob) - orig_size}B)")

    # Verify by re-parse
    print("\nVerifying by re-parse...")
    try:
        result2 = build_result_from_raw(bytes(new_blob), {"input_kind": "raw_blob"})
        new_keys = set()
        for obj in result2["objects"]:
            if obj.class_name != "KnowledgeSaveData":
                continue
            for f in obj.fields:
                if f.name not in ("_knowledgeElementSaveDataList", "_list"):
                    continue
                for elem in (f.list_elements or []):
                    for cf in (elem.child_fields or []):
                        if cf.present and cf.name == "_key":
                            new_keys.add(struct.unpack_from(
                                "<I", new_blob, cf.start_offset)[0])
                            break
        added = sorted(new_keys - have)
        confirmed = [k for k in missing if k in new_keys]
        print(f"  Re-parse OK: {len(result2['objects'])} objects")
        print(f"  Confirmed inserted: {len(confirmed)}/{len(missing)}")
        if len(confirmed) != len(missing):
            still_missing = [k for k in missing if k not in new_keys]
            print(f"  WARNING: still missing: {still_missing}")
    except Exception as e:
        print(f"  PARSE ERR: {e} — not writing")
        return 2

    print(f"\nWriting save: {USER_SAVE}")
    write_save_file(USER_SAVE, bytes(new_blob), sd.raw_header)
    print("Done. Test in-game: load slot100, check the 'Grounds of the "
          "Sunrise' knowledge card to see if it's now 38/38.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
