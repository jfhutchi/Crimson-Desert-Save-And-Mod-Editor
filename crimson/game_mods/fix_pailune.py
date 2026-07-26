"""Fix Pailune Infirmary world state — SAFE byte-only version.

Skips PARC structural expansion (the existing complete_mission_entry uses
heuristic sentinel scanning that gets ~43K false positives on a 6.5MB save).
Only flips two bytes on FactionNodeElementSaveData[owner=1000697]:

  _factionState   0 -> 2   (hostile -> active/cleared, like other Pailune nodes)
  _isBlock        1 -> 0   (block lifted)

Backup at: _backup_pailune_20260416_042753/save.save
"""
from __future__ import annotations

import os
import struct
import sys

EDITOR = r"C:\Users\Coding\CrimsonDesertModding\CrimsonSaveEditorGUI"
sys.path.insert(0, os.path.join(EDITOR, "Communitydump", "desktopeditor"))
sys.path.insert(0, EDITOR)

from crimson.game_mods.save_crypto import load_save_file, write_save_file  # noqa: E402
from crimson.game_mods.save_parser import build_result_from_raw  # noqa: E402

SAVE = r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot100\save.save"


def u32(blob, off):
    return struct.unpack_from("<I", blob, off)[0]


def main():
    print(f"Loading {SAVE}")
    sd = load_save_file(SAVE)
    blob = bytearray(sd.decompressed_blob)
    print(f"Decompressed: {len(blob):,} bytes\n")

    print("Parsing PARC tree...")
    result = build_result_from_raw(bytes(blob), {"input_kind": "raw_blob"})
    print(f"Parsed {len(result['objects'])} objects\n")

    state_off = block_off = elem_range = None
    for obj in result["objects"]:
        if obj.class_name != "FactionSaveData":
            continue
        for f in obj.fields:
            if f.name != "_factionNodeElementSaveDataList":
                continue
            for elem in (f.list_elements or []):
                owner_key = None
                tmp_state = tmp_block = None
                for cf in (elem.child_fields or []):
                    if not cf.present:
                        continue
                    if cf.name == "_ownerFactionKey":
                        owner_key = u32(blob, cf.start_offset)
                    elif cf.name == "_factionState":
                        tmp_state = cf.start_offset
                    elif cf.name == "_isBlock":
                        tmp_block = cf.start_offset
                if owner_key == 1000697:
                    state_off = tmp_state
                    block_off = tmp_block
                    elem_range = (elem.start_offset, elem.end_offset)
                    break

    if state_off is None or block_off is None:
        print(f"ERROR: could not find both fields "
              f"(state_off={state_off}, block_off={block_off})")
        return 1

    print(f"Pailune Hospital (node 1000697) found:")
    print(f"  elem range: 0x{elem_range[0]:X} - 0x{elem_range[1]:X} "
          f"({elem_range[1] - elem_range[0]} bytes)")
    print(f"  _factionState @ 0x{state_off:X}: "
          f"current={blob[state_off]} -> 2")
    print(f"  _isBlock      @ 0x{block_off:X}: "
          f"current={blob[block_off]} -> 0\n")

    if blob[state_off] == 2 and blob[block_off] == 0:
        print("Already in target state — nothing to do.")
        return 0

    # Apply
    old_state = blob[state_off]
    old_block = blob[block_off]
    blob[state_off] = 2
    blob[block_off] = 0

    # Re-parse to verify
    print("Verifying with re-parse...")
    result = build_result_from_raw(bytes(blob), {"input_kind": "raw_blob"})
    found = False
    for obj in result["objects"]:
        if obj.class_name != "FactionSaveData":
            continue
        for f in obj.fields:
            if f.name != "_factionNodeElementSaveDataList":
                continue
            for elem in (f.list_elements or []):
                owner = st = blk = None
                for cf in (elem.child_fields or []):
                    if not cf.present:
                        continue
                    if cf.name == "_ownerFactionKey":
                        owner = u32(blob, cf.start_offset)
                    elif cf.name == "_factionState":
                        st = blob[cf.start_offset]
                    elif cf.name == "_isBlock":
                        blk = blob[cf.start_offset]
                if owner == 1000697:
                    print(f"  Verified: _factionState={st} _isBlock={blk}")
                    if st == 2 and blk == 0:
                        found = True
                    break

    if not found:
        print("VERIFY FAILED — not writing")
        return 2

    print(f"\nWriting save: {SAVE}")
    print(f"  Diff: 2 bytes total "
          f"(_factionState {old_state}->2, _isBlock {old_block}->0)")
    print(f"  Blob size unchanged: {len(blob):,} bytes")
    write_save_file(SAVE, bytes(blob), sd.raw_header)
    print("\nDone.")
    print("\nIn-game: load this slot, fast-travel to Pailune, check the "
          "Hospital/Infirmary site. If bandits still spawn, we may also "
          "need to flip _conquerorFactionKey or wait for region tick.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
