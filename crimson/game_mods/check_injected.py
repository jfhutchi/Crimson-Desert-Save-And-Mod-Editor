"""Check if our 36 injected knowledge keys are still in the current save."""
from __future__ import annotations
import os, struct, sys
EDITOR = r"C:\Users\Coding\CrimsonDesertModding\CrimsonSaveEditorGUI"
sys.path.insert(0, os.path.join(EDITOR, "Communitydump", "desktopeditor"))
sys.path.insert(0, EDITOR)
from crimson.game_mods.save_crypto import load_save_file
from crimson.game_mods.save_parser import build_result_from_raw

INJECTED = [41025, 42036, 42037, 42038, 42039, 42040, 42042, 42043, 42049,
            1000391, 1000392, 1000452, 1000453, 1000544, 1000654, 1000900,
            1000958, 1001184, 1001185, 1001186, 1001255, 1001566, 1001937,
            1001998, 1001999, 1002000, 1002004, 1002005, 1002006,
            1002069, 1002081, 1002108, 1002233, 1003372, 1003420, 1003752]

sd = load_save_file(r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot100\save.save")
blob = bytes(sd.decompressed_blob)
result = build_result_from_raw(blob, {"input_kind": "raw_blob"})
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

still_have = [k for k in INJECTED if k in have]
gone = [k for k in INJECTED if k not in have]
print(f"Total knowledge: {len(have)}")
print(f"Injected and STILL present: {len(still_have)}/{len(INJECTED)}")
print(f"Injected but GONE: {len(gone)}")
for k in gone[:10]:
    print(f"  gone: {k}")
