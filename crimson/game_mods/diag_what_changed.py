"""Compare current user save vs the backup taken before our edits to see
exactly what changed (knowledge keys added/removed, mission state shifts).
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

CURRENT = r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot100\save.save"
BACKUP = r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save\57173764\slot100\save - copy.save"


def get_state(path):
    sd = load_save_file(path)
    blob = bytes(sd.decompressed_blob)
    result = build_result_from_raw(blob, {"input_kind": "raw_blob"})
    out = {"size": len(blob), "n_objects": len(result["objects"]),
           "knowledge_keys": set(), "missions": {}, "quests": {}}

    for obj in result["objects"]:
        if obj.class_name == "KnowledgeSaveData":
            for f in obj.fields:
                if f.name not in ("_knowledgeElementSaveDataList", "_list"):
                    continue
                for elem in (f.list_elements or []):
                    for cf in (elem.child_fields or []):
                        if cf.present and cf.name == "_key":
                            out["knowledge_keys"].add(struct.unpack_from(
                                "<I", blob, cf.start_offset)[0])
                            break
        elif obj.class_name == "QuestSaveData":
            for f in obj.fields:
                if f.name == "_missionStateList":
                    for elem in (f.list_elements or []):
                        k = state = comp = None
                        for cf in (elem.child_fields or []):
                            if not cf.present:
                                continue
                            if cf.name == "_key":
                                k = struct.unpack_from("<I", blob, cf.start_offset)[0]
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
                        if k is not None:
                            out["missions"][k] = (state, comp)
                elif f.name == "_questStateList":
                    for elem in (f.list_elements or []):
                        k = state = comp = None
                        for cf in (elem.child_fields or []):
                            if not cf.present:
                                continue
                            if cf.name == "_questKey":
                                k = struct.unpack_from("<I", blob, cf.start_offset)[0]
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
                        if k is not None:
                            out["quests"][k] = (state, comp)
    return out


def main():
    print("Loading current save...")
    cur = get_state(CURRENT)
    print(f"  Current: {cur['size']:,} bytes, {cur['n_objects']} objects, "
          f"{len(cur['knowledge_keys'])} knowledge, "
          f"{len(cur['missions'])} missions, {len(cur['quests'])} quests")

    print("Loading backup...")
    bak = get_state(BACKUP)
    print(f"  Backup:  {bak['size']:,} bytes, {bak['n_objects']} objects, "
          f"{len(bak['knowledge_keys'])} knowledge, "
          f"{len(bak['missions'])} missions, {len(bak['quests'])} quests")

    # Knowledge differences
    new_know = cur["knowledge_keys"] - bak["knowledge_keys"]
    lost_know = bak["knowledge_keys"] - cur["knowledge_keys"]
    print(f"\n=== Knowledge changes ===")
    print(f"  +{len(new_know)} new keys (current - backup)")
    print(f"  -{len(lost_know)} lost keys (backup - current)")

    # Mission state changes
    print(f"\n=== Mission state changes ===")
    all_keys = set(cur["missions"].keys()) | set(bak["missions"].keys())
    changes = []
    new_missions = []
    lost_missions = []
    for k in sorted(all_keys):
        cv = cur["missions"].get(k)
        bv = bak["missions"].get(k)
        if cv is None:
            lost_missions.append(k)
        elif bv is None:
            new_missions.append((k, cv))
        elif cv != bv:
            changes.append((k, bv, cv))
    print(f"  {len(changes)} state changes, {len(new_missions)} new, "
          f"{len(lost_missions)} removed")
    for k, bv, cv in changes[:20]:
        print(f"    mission {k}: {bv} -> {cv}")
    if len(changes) > 20:
        print(f"    ... +{len(changes) - 20} more changes")
    for k, cv in new_missions[:10]:
        print(f"    new mission {k}: state={cv[0]} completedTime={cv[1]}")
    if len(new_missions) > 10:
        print(f"    ... +{len(new_missions) - 10} more new")

    # Quest state changes
    print(f"\n=== Quest state changes ===")
    all_q = set(cur["quests"].keys()) | set(bak["quests"].keys())
    qchanges = []
    new_quests = []
    for k in sorted(all_q):
        cv = cur["quests"].get(k)
        bv = bak["quests"].get(k)
        if cv is None:
            print(f"  lost quest {k}")
        elif bv is None:
            new_quests.append((k, cv))
        elif cv != bv:
            qchanges.append((k, bv, cv))
    print(f"  {len(qchanges)} quest state changes, {len(new_quests)} new quests")
    for k, bv, cv in qchanges[:30]:
        print(f"    quest {k}: {bv} -> {cv}")
    if len(qchanges) > 30:
        print(f"    ... +{len(qchanges) - 30} more")
    for k, cv in new_quests[:30]:
        print(f"    new quest {k}: state={cv[0]} completedTime={cv[1]}")
    if len(new_quests) > 30:
        print(f"    ... +{len(new_quests) - 30} more")


if __name__ == "__main__":
    main()
