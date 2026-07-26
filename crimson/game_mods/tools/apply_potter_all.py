from __future__ import annotations
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crimson.game_mods import crimson_rs as crimson_rs
from crimson.game_mods import imbue as imbue


GAME_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert"
INTERNAL_DIR = "gamedata/binary__/client/bin"
BUFF_DIR = "0058"

POTTER_EXTRA_CLASSES = [0x46C1BBDE, 0x01E2F397]


DOCKING_BASE = {
    "character_key": 0, "item_key": 0, "attach_child_socket_name": "",
    "docking_equip_slot_no": 65535, "spawn_distance_level": 4294967295,
    "send_damage_to_parent": 0, "is_body_part": 0, "docking_type": 0,
    "is_summoner_team": 0, "is_npc_only": 0, "is_sync_break_parent": 0,
    "hit_part": 0, "detected_by_npc": 0, "is_bag_docking": 0,
    "enable_collision": 0, "disable_collision_with_other_gimmick": 1,
    "docking_slot_key": "",
}


def _apply_plate_boots(it):
    it["equip_passive_skill_list"] = [
        {"skill": 7201, "level": 1},
        {"skill": 7055, "level": 1},
        {"skill": 7202, "level": 1},
    ]
    it["gimmick_info"] = 1004431
    it["cooltime"] = 1
    it["item_charge_type"] = 0
    it["max_charged_useable_count"] = 100
    it["respawn_time_seconds"] = 0
    it["docking_child_data"] = {
        **DOCKING_BASE,
        "gimmick_info_key": 1004431,
        "attach_parent_socket_name": "Bip01 Footsteps",
        "docking_tag_name_hash": [247236102, 0, 0, 0],
        "is_item_equip_docking_gimmick": 0,
        "is_player_only": 0,
    }
    ddd = it.setdefault("drop_default_data", {})
    lst = ddd.setdefault("add_socket_material_item_list", [])
    lst.extend([
        {"item": 1, "value": 600},
        {"item": 1, "value": 800},
        {"item": 1, "value": 1000},
    ])


def _apply_trooper_spear(it):
    it["equip_passive_skill_list"] = [
        {"skill": 91101, "level": 3},
        {"skill": 91105, "level": 3},
        {"skill": 91109, "level": 1},
    ]
    it["gimmick_info"] = 1001961
    it["cooltime"] = 1
    it["item_charge_type"] = 0
    it["max_charged_useable_count"] = 100
    it["respawn_time_seconds"] = 0
    it["docking_child_data"] = {
        **DOCKING_BASE,
        "gimmick_info_key": 1001961,
        "attach_parent_socket_name": "Gimmick_Weapon_00_Socket",
        "docking_tag_name_hash": [666382090, 0, 0, 0],
        "is_item_equip_docking_gimmick": 1,
        "is_player_only": 0,
    }
    ddd = it.setdefault("drop_default_data", {})
    lst = ddd.setdefault("add_socket_material_item_list", [])
    lst.extend([
        {"item": 1, "value": 6000},
        {"item": 1, "value": 8000},
        {"item": 1, "value": 10000},
        {"item": 1, "value": 12000},
        {"item": 1, "value": 14000},
    ])


def _apply_whitewind_rapier(it):
    it["equip_passive_skill_list"] = [
        {"skill": 91101, "level": 3},
        {"skill": 91105, "level": 3},
        {"skill": 91109, "level": 1},
        {"skill": 91104, "level": 3},
    ]


def _apply_hwando(it):
    it["equip_passive_skill_list"] = [
        {"skill": 91101, "level": 3},
        {"skill": 91105, "level": 3},
        {"skill": 91109, "level": 1},
    ]
    it["gimmick_info"] = 1001961
    it["cooltime"] = 1
    it["item_charge_type"] = 0
    it["max_charged_useable_count"] = 100
    it["respawn_time_seconds"] = 0
    it["docking_child_data"] = {
        **DOCKING_BASE,
        "gimmick_info_key": 1001961,
        "attach_parent_socket_name": "Gimmick_Weapon_00_Socket",
        "docking_tag_name_hash": [3365725887, 0, 0, 0],
        "is_item_equip_docking_gimmick": 1,
        "is_player_only": 0,
    }


def _apply_oath_of_darkness(it):
    it["equip_passive_skill_list"] = [
        {"skill": 70994, "level": 1},
        {"skill": 8037, "level": 15},
        {"skill": 8038, "level": 15},
        {"skill": 8039, "level": 15},
        {"skill": 76009, "level": 1},
        {"skill": 91010, "level": 1},
    ]
    for ed in it.get("enchant_data_list", []):
        esd = ed.setdefault("enchant_stat_data", {})
        esd["regen_stat_list"] = [
            {"stat": 1000026, "change_mb": 100000},
            {"stat": 1000027, "change_mb": 100000},
            {"stat": 1000000, "change_mb": 100000},
        ]


ITEM_PATCHES = {
    1000382:  ("Plate Boots of the Shadows",  _apply_plate_boots),
    1000082:  ("Trooper's TwoHandSpear",      _apply_trooper_spear),
    200901:   ("Whitewind Rapier (A)",        _apply_whitewind_rapier),
    1001747:  ("Whitewind Rapier (B)",        _apply_whitewind_rapier),
    1000080:  ("Hwando (TwoHandSword)",       _apply_hwando),
    391518535:("Oath of Darkness",            _apply_oath_of_darkness),
}


def main() -> int:
    print(f"=> Extracting vanilla iteminfo.pabgb")
    raw_item = crimson_rs.extract_file(GAME_DIR, "0008", INTERNAL_DIR, "iteminfo.pabgb")
    items = crimson_rs.parse_iteminfo_from_bytes(raw_item)
    print(f"   {len(items)} items parsed")

    by_key = {it.get("key"): it for it in items}
    applied = []
    missing = []
    for key, (disp, fn) in ITEM_PATCHES.items():
        it = by_key.get(key)
        if it is None:
            missing.append(f"{key} ({disp})")
            continue
        fn(it)
        applied.append(f"{key} {it.get('string_key','?')} ({disp})")
    print(f"   iteminfo patches applied:")
    for line in applied:
        print(f"     + {line}")
    if missing:
        print(f"   MISSING items (will be skipped):")
        for line in missing:
            print(f"     - {line}")

    new_iteminfo = bytes(crimson_rs.serialize_iteminfo(items))
    print(f"   iteminfo.pabgb: {len(raw_item):,} -> {len(new_iteminfo):,} bytes "
          f"(delta {len(new_iteminfo)-len(raw_item):+d})")

    print(f"\n=> Extracting vanilla skill.pabgb + skill.pabgh")
    raw_skill_pabgb = crimson_rs.extract_file(GAME_DIR, "0008", INTERNAL_DIR, "skill.pabgb")
    raw_skill_pabgh = crimson_rs.extract_file(GAME_DIR, "0008", INTERNAL_DIR, "skill.pabgh")
    print(f"   skill.pabgb: {len(raw_skill_pabgb):,}  skill.pabgh: {len(raw_skill_pabgh):,}")

    def _extend_filter(rec):
        for h in POTTER_EXTRA_CLASSES:
            rec = imbue.add_class_to_skill_record(rec, h)
        return rec

    edits = {91101: _extend_filter, 91104: _extend_filter, 91109: _extend_filter}
    new_skill_pabgh, new_skill_pabgb = imbue.rebuild_skill_pair(
        raw_skill_pabgh, raw_skill_pabgb, edits,
    )
    print(f"   skill.pabgb: {len(raw_skill_pabgb):,} -> {len(new_skill_pabgb):,} "
          f"(delta {len(new_skill_pabgb)-len(raw_skill_pabgb):+d})")
    print(f"   skill.pabgh: {len(raw_skill_pabgh):,} -> {len(new_skill_pabgh):,} "
          f"(delta {len(new_skill_pabgh)-len(raw_skill_pabgh):+d})")

    verify_entries = imbue.parse_skill_pabgh(new_skill_pabgh, len(new_skill_pabgb))
    by2 = {k: (o, l) for (k, o, l) in verify_entries}
    for sid, (disp, _name) in imbue.IMBUE_SKILLS.items():
        if sid not in (91101, 91104, 91109):
            continue
        o, l = by2[sid]
        rec = new_skill_pabgb[o:o+l]
        for h in POTTER_EXTRA_CLASSES:
            if not imbue.skill_allows_class(rec, h):
                print(f"   !! {sid} ({disp}) missing class hash {h:#x} after rebuild")
                return 2
    print("   validated: all 3 imbue skills allow OneHandRapier + OneHandPistol")

    with tempfile.TemporaryDirectory() as tmp:
        group_dir = os.path.join(tmp, BUFF_DIR)
        print(f"\n=> Building overlay group '{BUFF_DIR}' (compression=NONE)")
        builder = crimson_rs.PackGroupBuilder(
            group_dir, crimson_rs.Compression.NONE, crimson_rs.Crypto.NONE
        )
        builder.add_file(INTERNAL_DIR, "iteminfo.pabgb", new_iteminfo)
        builder.add_file(INTERNAL_DIR, "skill.pabgb",    new_skill_pabgb)
        builder.add_file(INTERNAL_DIR, "skill.pabgh",    new_skill_pabgh)
        pamt_bytes = bytes(builder.finish())
        print(f"   0.pamt: {len(pamt_bytes):,} bytes  "
              f"0.paz: {os.path.getsize(os.path.join(group_dir, '0.paz')):,} bytes")

        pamt_parsed = crimson_rs.parse_pamt_bytes(pamt_bytes)
        pamt_checksum = pamt_parsed["checksum"]
        print(f"   PAMT self-checksum (for papgt): 0x{pamt_checksum:08X}")

        papgt_dst = os.path.join(GAME_DIR, "meta", "0.papgt")
        papgt_vanilla = papgt_dst + ".vanilla"
        source_papgt = papgt_vanilla if os.path.isfile(papgt_vanilla) else papgt_dst
        papgt = crimson_rs.parse_papgt_file(source_papgt)
        papgt["entries"] = [e for e in papgt["entries"] if e.get("group_name") != BUFF_DIR]
        papgt = crimson_rs.add_papgt_entry(papgt, BUFF_DIR, pamt_checksum, 0, 16383)
        print(f"   papgt entries: {len(papgt['entries'])}")

        game_mod = os.path.join(GAME_DIR, BUFF_DIR)
        if os.path.isdir(game_mod):
            shutil.rmtree(game_mod)
        os.makedirs(game_mod, exist_ok=True)
        shutil.copy2(os.path.join(group_dir, "0.paz"),
                      os.path.join(game_mod, "0.paz"))
        shutil.copy2(os.path.join(group_dir, "0.pamt"),
                      os.path.join(game_mod, "0.pamt"))
        print(f"   installed overlay -> {game_mod}")

        if not os.path.isfile(papgt_vanilla):
            shutil.copy2(papgt_dst, papgt_vanilla)
            print(f"   vanilla papgt backup -> {papgt_vanilla}")
        crimson_rs.write_papgt_file(papgt, papgt_dst)
        print(f"   updated papgt -> {papgt_dst}")

    print(f"\n=> Done.")
    print(f"   Overlay: {os.path.join(GAME_DIR, BUFF_DIR)} (iteminfo + skill pair)")
    print(f"   Launch the game and test:")
    print(f"     1000082 Trooper's TwoHandSpear  (in-filter, proven)")
    print(f"     1000080 Hwando TwoHandSword     (in-filter)")
    print(f"     200901  Whitewind Rapier (A)    (cross-class via OneHandRapier hash)")
    print(f"     1001747 Whitewind Rapier (B)    (cross-class)")
    print(f"     1000382 Plate Boots of Shadows  (non-imbue gimmick on boots)")
    print(f"     391518535 Oath of Darkness     (stat+passive overhaul)")
    print(f"   To undo: click Restore Original in ItemBuffs (or delete 0058/ +")
    print(f"   copy 0.papgt.vanilla over 0.papgt)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
