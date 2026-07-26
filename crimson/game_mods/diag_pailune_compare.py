"""Compare Pailune state across multiple saves — find one where Infirmary
quest is properly completed so we can see what 'finished' state looks like.
"""
from __future__ import annotations

import os
import struct
import sys

EDITOR = r"C:\Users\Coding\CrimsonDesertModding\CrimsonSaveEditorGUI"
sys.path.insert(0, os.path.join(EDITOR, "Communitydump", "desktopeditor"))
sys.path.insert(0, EDITOR)

from crimson.game_mods.save_crypto import load_save_file  # noqa: E402
from crimson.game_mods.save_parser import build_result_from_raw  # noqa: E402

SAVES = [
    ("USER",   r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot100\save.save"),
    ("DIFF100", r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\diff save\slot100\save.save"),
    ("DIFF102", r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\diff save\slot102\save.save"),
    ("FINISHED", r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\diff save\Main Story Finished Save\save.save"),
]

PAILUNE_NODE_KEYS = {
    1000062: "PailuneCampsite",
    1000187: "Kwe_Pailune (main)",
    1000271: "PailuneEastGate",
    1000272: "PailuneSouthGate",
    1000291: "PailuneBeacon",
    1000304: "PailuneSecretSafe",
    1000697: "PailuneHospital",
    1000757: "Pailune_Witch",
}

PAILUNE_MISSION_KEYS = {
    1000230:    "Reconstructing Pailune (parent)",
    4294964447: "_Start_0 Prep",
    4294964446: "_Start_1 Council",
    4294964445: "_Start_2 Institute",
    4294964444: "_Start_3 Infirmary",
    4294964443: "_Start_4 Barracks",
    4294964442: "_Start_5 Trading Post",
}


def u32(blob, off):
    return struct.unpack_from("<I", blob, off)[0]


def u64(blob, off):
    return struct.unpack_from("<Q", blob, off)[0]


def analyze(label, path):
    print(f"\n{'#' * 78}")
    print(f"# {label}: {path}")
    print('#' * 78)
    if not os.path.exists(path):
        print("  FILE NOT FOUND")
        return

    sd = load_save_file(path)
    blob = bytes(sd.decompressed_blob)
    print(f"Decompressed: {len(blob):,} bytes")
    result = build_result_from_raw(blob, {"input_kind": "raw_blob"})
    print(f"Parsed {len(result['objects'])} objects")

    print("\n-- Pailune mission states --")
    quest_obj = None
    for obj in result["objects"]:
        if obj.class_name == "QuestSaveData":
            quest_obj = obj
            break
    if quest_obj:
        for fname in ("_missionStateList", "_questStateList"):
            mf = next((f for f in quest_obj.fields if f.name == fname), None)
            if not mf:
                continue
            for elem in (mf.list_elements or []):
                k = state = comp = brn = None
                key_field = "_key" if fname == "_missionStateList" else "_questKey"
                for cf in (elem.child_fields or []):
                    if not cf.present:
                        continue
                    if cf.name == key_field:
                        k = u32(blob, cf.start_offset)
                    elif cf.name == "_state":
                        sz = cf.end_offset - cf.start_offset
                        if sz == 1:
                            state = blob[cf.start_offset]
                        elif sz == 2:
                            state = struct.unpack_from("<H", blob, cf.start_offset)[0]
                        else:
                            state = struct.unpack_from("<I", blob, cf.start_offset)[0]
                    elif cf.name == "_completedTime":
                        comp = u64(blob, cf.start_offset)
                    elif cf.name == "_branchedTime":
                        brn = u64(blob, cf.start_offset)
                if k in PAILUNE_MISSION_KEYS:
                    label = PAILUNE_MISSION_KEYS[k]
                    print(f"  [{fname[1:9]}] {k:>10}  state={state:<5} "
                          f"branched={brn}  completed={comp}  {label}")

    print("\n-- Pailune faction nodes --")
    for obj in result["objects"]:
        if obj.class_name != "FactionSaveData":
            continue
        for f in obj.fields:
            if f.name != "_factionNodeElementSaveDataList":
                continue
            for elem in (f.list_elements or []):
                owner = state = block = blockplayer = enable = conq = None
                op_key = op_state = None
                sub_inners = []
                gimmicks = []
                for cf in (elem.child_fields or []):
                    if not cf.present:
                        continue
                    if cf.name == "_ownerFactionKey":
                        owner = u32(blob, cf.start_offset)
                    elif cf.name == "_factionState":
                        state = blob[cf.start_offset]
                    elif cf.name == "_isBlock":
                        block = blob[cf.start_offset]
                    elif cf.name == "_isBlockByPlayer":
                        blockplayer = blob[cf.start_offset]
                    elif cf.name == "_enableNode":
                        enable = blob[cf.start_offset]
                    elif cf.name == "_conquerorFactionKey":
                        conq = u32(blob, cf.start_offset)
                    elif cf.name == "_operationKey":
                        op_key = u32(blob, cf.start_offset)
                    elif cf.name == "_operationStateType":
                        op_state = blob[cf.start_offset]
                    elif cf.name == "_subInnerEnableDataList":
                        for s in (cf.list_elements or []):
                            sk = se = None
                            for scf in (s.child_fields or []):
                                if not scf.present:
                                    continue
                                if scf.name == "_subInnerNodeKey":
                                    sk = u32(blob, scf.start_offset)
                                elif scf.name == "_isEnable":
                                    se = blob[scf.start_offset]
                            sub_inners.append((sk, se))
                    elif cf.name == "_completedSubInnerGimmickUuidList":
                        for g in (cf.list_elements or []):
                            raw = blob[g.start_offset:g.end_offset]
                            gimmicks.append(raw.hex())

                if owner in PAILUNE_NODE_KEYS:
                    name = PAILUNE_NODE_KEYS[owner]
                    parts = [
                        f"_factionState={state}",
                        f"_isBlock={block}",
                        f"_enableNode={enable}",
                    ]
                    if conq is not None:
                        parts.append(f"_conqueror={conq}")
                    if op_key is not None:
                        parts.append(f"_operationKey={op_key}")
                    if op_state is not None:
                        parts.append(f"_operationState={op_state}")
                    print(f"  {owner} ({name}): " + " ".join(parts))
                    if sub_inners:
                        for sk, se in sub_inners:
                            print(f"    sub_inner: key={sk} isEnable={se}")
                    if gimmicks:
                        print(f"    completed gimmicks: {len(gimmicks)}")
                        for g in gimmicks[:3]:
                            print(f"      {g}")
                        if len(gimmicks) > 3:
                            print(f"      ... +{len(gimmicks) - 3} more")


def main():
    for label, path in SAVES:
        analyze(label, path)


if __name__ == "__main__":
    main()
