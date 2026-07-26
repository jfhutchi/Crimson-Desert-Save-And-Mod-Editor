"""Deep dump of slot100/save - copy.save where all Pailune missions are
completed. Show every field on node 1000187 and 1000697 + full mission
records so we can see what 'finished Pailune' actually looks like.
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

PATH = r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot100\save - copy.save"


def u32(blob, off):
    return struct.unpack_from("<I", blob, off)[0]


def u64(blob, off):
    return struct.unpack_from("<Q", blob, off)[0]


def main():
    print(f"Loading {PATH}")
    sd = load_save_file(PATH)
    blob = bytes(sd.decompressed_blob)
    print(f"Decompressed: {len(blob):,} bytes")

    result = build_result_from_raw(blob, {"input_kind": "raw_blob"})
    print(f"Parsed {len(result['objects'])} objects\n")

    print("=" * 78)
    print("ALL fields on Pailune nodes (showing only PRESENT fields)")
    print("=" * 78)

    PAILUNE_NODES = {1000062, 1000187, 1000271, 1000272, 1000291,
                     1000304, 1000697, 1000757}
    for obj in result["objects"]:
        if obj.class_name != "FactionSaveData":
            continue
        for f in obj.fields:
            if f.name != "_factionNodeElementSaveDataList":
                continue
            for elem in (f.list_elements or []):
                owner = None
                for cf in (elem.child_fields or []):
                    if cf.present and cf.name == "_ownerFactionKey":
                        owner = u32(blob, cf.start_offset)
                        break
                if owner not in PAILUNE_NODES:
                    continue

                print(f"\n--- Node {owner} ---")
                print(f"  elem range: 0x{elem.start_offset:X}-0x{elem.end_offset:X}  "
                      f"({elem.end_offset - elem.start_offset}B)")
                print(f"  mask: {elem.child_mask_bytes.hex() if elem.child_mask_bytes else '?'}")

                for cf in (elem.child_fields or []):
                    if not cf.present:
                        continue
                    sz = cf.end_offset - cf.start_offset
                    val_str = ""
                    if sz == 1:
                        val_str = f"u8={blob[cf.start_offset]}"
                    elif sz == 2:
                        val_str = f"u16={struct.unpack_from('<H', blob, cf.start_offset)[0]}"
                    elif sz == 4:
                        val_str = f"u32={u32(blob, cf.start_offset)}"
                    elif sz == 8:
                        val_str = f"u64={u64(blob, cf.start_offset)}"
                    elif sz == 12:
                        f3 = struct.unpack_from('<3f', blob, cf.start_offset)
                        val_str = f"float3={f3}"

                    if cf.list_elements is not None:
                        n = len(cf.list_elements)
                        print(f"  {cf.name:<40} list={n} entries")
                        if cf.name == "_completedSubInnerGimmickUuidList":
                            for uel in cf.list_elements[:5]:
                                raw = blob[uel.start_offset:uel.end_offset]
                                print(f"    uuid: {raw.hex()}")
                            if n > 5:
                                print(f"    ... +{n-5} more")
                        elif cf.name == "_subInnerEnableDataList":
                            for s in cf.list_elements:
                                sk = se = None
                                for scf in (s.child_fields or []):
                                    if not scf.present:
                                        continue
                                    if scf.name == "_subInnerNodeKey":
                                        sk = u32(blob, scf.start_offset)
                                    elif scf.name == "_isEnable":
                                        se = blob[scf.start_offset]
                                print(f"    sub_key={sk} isEnable={se}")
                    else:
                        print(f"  {cf.name:<40} {val_str}")

    print("\n" + "=" * 78)
    print("Pailune missions (full record)")
    print("=" * 78)

    PAILUNE_MISSIONS = {1000230, 4294964447, 4294964446, 4294964445,
                        4294964444, 4294964443, 4294964442}
    for obj in result["objects"]:
        if obj.class_name != "QuestSaveData":
            continue
        for f in obj.fields:
            if f.name != "_missionStateList":
                continue
            for elem in (f.list_elements or []):
                k = None
                for cf in (elem.child_fields or []):
                    if cf.present and cf.name == "_key":
                        k = u32(blob, cf.start_offset)
                        break
                if k not in PAILUNE_MISSIONS:
                    continue
                print(f"\n  Mission {k}: mask={elem.child_mask_bytes.hex()}, "
                      f"size={elem.end_offset - elem.start_offset}B")
                for cf in (elem.child_fields or []):
                    if not cf.present:
                        continue
                    sz = cf.end_offset - cf.start_offset
                    val_str = ""
                    if sz == 1:
                        val_str = f"u8={blob[cf.start_offset]}"
                    elif sz == 2:
                        val_str = f"u16={struct.unpack_from('<H', blob, cf.start_offset)[0]}"
                    elif sz == 4:
                        val_str = f"u32={u32(blob, cf.start_offset)}"
                    elif sz == 8:
                        val_str = f"u64={u64(blob, cf.start_offset)}"
                    print(f"    {cf.name:<25} {val_str}")


if __name__ == "__main__":
    main()
