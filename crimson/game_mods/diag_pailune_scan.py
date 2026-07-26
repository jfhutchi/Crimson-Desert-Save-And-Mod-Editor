"""Quick scan: dump just the Pailune Hospital + 1000187 sub-inner state
for many saves so we can find any with novel data.
"""
from __future__ import annotations

import glob
import os
import struct
import sys

EDITOR = r"C:\Users\Coding\CrimsonDesertModding\CrimsonSaveEditorGUI"
sys.path.insert(0, os.path.join(EDITOR, "Communitydump", "desktopeditor"))
sys.path.insert(0, EDITOR)

from crimson.game_mods.save_crypto import load_save_file  # noqa: E402
from crimson.game_mods.save_parser import build_result_from_raw  # noqa: E402

ROOTS = [
    r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\save",
    r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\bugged missing faction",
    r"C:\Users\Coding\AppData\Local\Pearl Abyss\CD\diff save",
]


def u32(blob, off):
    return struct.unpack_from("<I", blob, off)[0]


def scan(path):
    try:
        sd = load_save_file(path)
    except Exception as e:
        return None, f"LOAD ERR: {e}"
    blob = bytes(sd.decompressed_blob)

    try:
        result = build_result_from_raw(blob, {"input_kind": "raw_blob"})
    except Exception as e:
        return None, f"PARSE ERR: {e}"

    out = {
        "size": len(blob),
        "hospital": None,
        "main_pailune_subs": None,
        "missions": {},
    }

    for obj in result["objects"]:
        if obj.class_name == "FactionSaveData":
            for f in obj.fields:
                if f.name != "_factionNodeElementSaveDataList":
                    continue
                for elem in (f.list_elements or []):
                    owner = state = block = None
                    subs = None
                    for cf in (elem.child_fields or []):
                        if not cf.present:
                            continue
                        if cf.name == "_ownerFactionKey":
                            owner = u32(blob, cf.start_offset)
                        elif cf.name == "_factionState":
                            state = blob[cf.start_offset]
                        elif cf.name == "_isBlock":
                            block = blob[cf.start_offset]
                        elif cf.name == "_subInnerEnableDataList":
                            subs = []
                            for s in (cf.list_elements or []):
                                sk = se = None
                                for scf in (s.child_fields or []):
                                    if not scf.present:
                                        continue
                                    if scf.name == "_subInnerNodeKey":
                                        sk = u32(blob, scf.start_offset)
                                    elif scf.name == "_isEnable":
                                        se = blob[scf.start_offset]
                                subs.append((sk, se))
                    if owner == 1000697:
                        out["hospital"] = (state, block)
                    if owner == 1000187:
                        out["main_pailune_subs"] = subs

        if obj.class_name == "QuestSaveData":
            mf = next((f for f in obj.fields
                       if f.name == "_missionStateList"), None)
            if mf:
                for elem in (mf.list_elements or []):
                    k = state = comp = None
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
                    if k in (1000230, 4294964447, 4294964446, 4294964445,
                             4294964444, 4294964443, 4294964442):
                        out["missions"][k] = (state, comp)

    return out, "OK"


def short_label(p):
    return p.replace("C:\\Users\\Coding\\AppData\\Local\\Pearl Abyss\\CD\\", "")


def main():
    paths = []
    for r in ROOTS:
        for p in glob.glob(os.path.join(r, "**", "save.save"), recursive=True):
            paths.append(p)
        for p in glob.glob(os.path.join(r, "**", "save - copy.save"), recursive=True):
            paths.append(p)
        for p in glob.glob(os.path.join(r, "**", "save.save"), recursive=True):
            pass

    # Dedupe
    paths = sorted(set(paths))

    print(f"Scanning {len(paths)} save files\n")
    print(f"{'PATH':<60} {'SIZE':>10} {'HOSP':>10} {'#SUBS':>6} {'MISSIONS'}")

    hospital_by_state = {}
    sub_count_distrib = {}

    for p in paths:
        out, status = scan(p)
        if status != "OK":
            print(f"{short_label(p):<60} -- {status}")
            continue

        h = out["hospital"]
        h_str = f"{h[0]}/{h[1]}" if h else "MISSING"
        subs = out["main_pailune_subs"]
        n_subs = len(subs) if subs else (0 if subs == [] else "MISSING")

        # Mission summary
        ms = out["missions"]
        m_compact = ""
        for k in (4294964447, 4294964446, 4294964445, 4294964444,
                  4294964443, 4294964442, 1000230):
            if k in ms:
                st, c = ms[k]
                tag = "C" if c is not None else "n"
                m_compact += f"{st}{tag} "
            else:
                m_compact += "-- "

        print(f"{short_label(p):<60} {out['size']:>10,} {h_str:>10} "
              f"{str(n_subs):>6}  {m_compact}")

        hospital_by_state.setdefault(h_str, []).append(p)
        sub_count_distrib.setdefault(str(n_subs), []).append(p)

    print("\n=== Hospital state distribution ===")
    for state, files in sorted(hospital_by_state.items()):
        print(f"  {state}: {len(files)} files")

    print("\n=== Main Pailune sub-inner count distribution ===")
    for cnt, files in sorted(sub_count_distrib.items()):
        print(f"  {cnt} subs: {len(files)} files")
        for f in files[:3]:
            print(f"    {short_label(f)}")
        if len(files) > 3:
            print(f"    ... +{len(files)-3} more")

    print("\nLegend: missions = _Start_0,1,2,3,4,5,parent — "
          "<state><C=hasCompletedTime,n=no time>")


if __name__ == "__main__":
    main()
