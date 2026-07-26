"""Diagnostic: dump FactionNodeElementSaveData entries around Pailune.

Read-only. Loads the save, walks the PARC tree, finds the Pailune node
(1000062 = Node_Kwe_PailuneCampsite, plus 1000187, 1000697, 1000757, etc.)
and dumps every _subInnerEnableDataList entry so we can see which sub-inner
key corresponds to the Infirmary.
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

SAVE = r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot100\save.save"

PAILUNE_NODE_KEYS = {
    1000062: "PailuneCampsite",
    1000187: "Kwe_Pailune (main)",
    1000271: "PailuneEastGate",
    1000272: "PailuneSouthGate",
    1000291: "PailuneBeacon",
    1000304: "PailuneSecretSafe",
    1000697: "PailuneHospital / BanditCamp_I",
    1000757: "Pailune_Witch",
}

PAILUNE_MISSION_KEYS = {
    1000230:    "Reconstructing Pailune (parent)",
    4294964447: "_Start_0 Prepare to Rebuild Pailune",
    4294964446: "_Start_1 Construct Pailune Council",
    4294964445: "_Start_2 Construct Institute of Pailune",
    4294964444: "_Start_3 Construct Pailune Infirmary",
    4294964443: "_Start_4 Construct Pailune Barracks",
    4294964442: "_Start_5 Construct Pailune Trading Post",
}


def u32(blob, off):
    return struct.unpack_from("<I", blob, off)[0]


def main():
    print(f"Loading {SAVE}")
    sd = load_save_file(SAVE)
    blob = bytes(sd.decompressed_blob)
    print(f"Decompressed: {len(blob):,} bytes\n")

    print("Parsing PARC tree...")
    result = build_result_from_raw(blob, {"input_kind": "raw_blob"})
    print(f"Parsed {len(result['objects'])} objects\n")

    faction_obj = None
    for obj in result["objects"]:
        if obj.class_name == "FactionSaveData":
            faction_obj = obj
            break
    if not faction_obj:
        print("ERROR: FactionSaveData not found")
        return

    print(f"FactionSaveData @ 0x{faction_obj.data_offset:X} "
          f"size={faction_obj.data_size}")

    node_list_field = None
    for f in faction_obj.fields:
        if f.name == "_factionNodeElementSaveDataList":
            node_list_field = f
            break
    if not node_list_field:
        print("ERROR: _factionNodeElementSaveDataList not found")
        return

    elems = node_list_field.list_elements or []
    print(f"_factionNodeElementSaveDataList has {len(elems)} entries\n")

    matches = []
    for elem in elems:
        owner_key = None
        for cf in (elem.child_fields or []):
            if cf.name == "_ownerFactionKey" and cf.present:
                owner_key = u32(blob, cf.start_offset)
                break
        if owner_key in PAILUNE_NODE_KEYS:
            matches.append((owner_key, elem))

    print(f"Found {len(matches)} Pailune-related node entries\n")
    print("=" * 78)

    for owner_key, elem in matches:
        label = PAILUNE_NODE_KEYS[owner_key]
        print(f"\n=== Node {owner_key} ({label}) ===")
        print(f"    elem range: 0x{elem.start_offset:X} - 0x{elem.end_offset:X}  "
              f"({elem.end_offset - elem.start_offset} bytes)")
        if elem.child_mask_bytes:
            print(f"    mask: {elem.child_mask_bytes.hex()}")

        for cf in (elem.child_fields or []):
            if not cf.present:
                continue
            sz = cf.end_offset - cf.start_offset
            if cf.name == "_factionState" and sz == 1:
                print(f"    _factionState        = {blob[cf.start_offset]}")
            elif cf.name == "_enableNode" and sz == 1:
                print(f"    _enableNode          = {blob[cf.start_offset]}")
            elif cf.name == "_isCapital" and sz == 1:
                print(f"    _isCapital           = {blob[cf.start_offset]}")
            elif cf.name == "_isBlock" and sz == 1:
                print(f"    _isBlock             = {blob[cf.start_offset]}")
            elif cf.name == "_isBlockByPlayer" and sz == 1:
                print(f"    _isBlockByPlayer     = {blob[cf.start_offset]}")
            elif cf.name == "_isSaveEnabled" and sz == 1:
                print(f"    _isSaveEnabled       = {blob[cf.start_offset]}")
            elif cf.name == "_conquerorFactionKey" and sz == 4:
                print(f"    _conquerorFactionKey = {u32(blob, cf.start_offset)}")
            elif cf.name == "_operationKey" and sz == 4:
                print(f"    _operationKey        = {u32(blob, cf.start_offset)}")
            elif cf.name == "_operationCurrentProgress":
                v = struct.unpack_from('<f', blob, cf.start_offset)[0]
                print(f"    _operationCurrentProgress = {v}")
            elif cf.name == "_subInnerEnableDataList":
                sub_elems = cf.list_elements or []
                print(f"    _subInnerEnableDataList ({len(sub_elems)} entries):")
                for s in sub_elems:
                    sub_key = lvl_key = alias_key = None
                    is_enable = None
                    for scf in (s.child_fields or []):
                        if not scf.present:
                            continue
                        if scf.name == "_subInnerNodeKey":
                            sub_key = u32(blob, scf.start_offset)
                        elif scf.name == "_levelNameKey":
                            lvl_key = u32(blob, scf.start_offset)
                        elif scf.name == "_aliasNameKey":
                            alias_key = u32(blob, scf.start_offset)
                        elif scf.name == "_isEnable":
                            is_enable = blob[scf.start_offset]
                    print(f"      sub_key={sub_key} levelName={lvl_key} "
                          f"alias={alias_key} isEnable={is_enable}")
            elif cf.name == "_completedSubInnerGimmickUuidList":
                cnt = len(cf.list_elements or [])
                print(f"    _completedSubInnerGimmickUuidList: {cnt} UUIDs")
                for uel in (cf.list_elements or []):
                    raw = blob[uel.start_offset:uel.end_offset]
                    print(f"      uuid: {raw.hex()}")
            elif cf.name == "_reviveQuestList":
                cnt = len(cf.list_elements or [])
                if cnt:
                    keys = []
                    for q in cf.list_elements:
                        keys.append(u32(blob, q.start_offset))
                    print(f"    _reviveQuestList: {keys}")

    print("\n\n=== Pailune mission states ===")
    quest_obj = None
    for obj in result["objects"]:
        if obj.class_name == "QuestSaveData":
            quest_obj = obj
            break
    if quest_obj:
        mission_field = None
        for f in quest_obj.fields:
            if f.name == "_missionStateList":
                mission_field = f
                break
        if mission_field:
            for elem in (mission_field.list_elements or []):
                k = state = comp = brn = None
                for cf in (elem.child_fields or []):
                    if not cf.present:
                        continue
                    if cf.name == "_key":
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
                        comp = struct.unpack_from("<Q", blob, cf.start_offset)[0]
                    elif cf.name == "_branchedTime":
                        brn = struct.unpack_from("<Q", blob, cf.start_offset)[0]
                if k in PAILUNE_MISSION_KEYS:
                    label = PAILUNE_MISSION_KEYS[k]
                    print(f"  {k:>10} state={state} branched={brn} completed={comp}  {label}")

        qs_field = None
        for f in quest_obj.fields:
            if f.name == "_questStateList":
                qs_field = f
                break
        if qs_field:
            for elem in (qs_field.list_elements or []):
                k = state = comp = None
                for cf in (elem.child_fields or []):
                    if not cf.present:
                        continue
                    if cf.name == "_questKey":
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
                        comp = struct.unpack_from("<Q", blob, cf.start_offset)[0]
                if k in PAILUNE_MISSION_KEYS:
                    label = PAILUNE_MISSION_KEYS[k]
                    print(f"  Q {k:>8} state={state} completed={comp}  {label}")

    print("\n" + "=" * 78)


if __name__ == "__main__":
    main()
