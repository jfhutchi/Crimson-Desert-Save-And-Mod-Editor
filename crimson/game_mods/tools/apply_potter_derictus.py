from __future__ import annotations
import os
import shutil
import sys
import tempfile

from crimson.game_mods import crimson_rs as crimson_rs
from crimson.game_mods import crimson_rs as pack_mod


GAME_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert"
INTERNAL_DIR = "gamedata/binary__/client/bin"
BUFF_DIR = "0058"
TARGET_KEY = 1000082


POTTER_DERICTUS_PATCH = {
    "equip_passive_skill_list": [
        {"skill": 91101, "level": 3},
        {"skill": 91105, "level": 3},
        {"skill": 91109, "level": 1},
    ],
    "gimmick_info": 1001961,
    "cooltime": 1,
    "item_charge_type": 0,
    "max_charged_useable_count": 100,
    "respawn_time_seconds": 0,
    "docking_child_data": {
        "gimmick_info_key": 1001961,
        "character_key": 0,
        "item_key": 0,
        "attach_parent_socket_name": "Gimmick_Weapon_00_Socket",
        "attach_child_socket_name": "",
        "docking_tag_name_hash": [666382090, 0, 0, 0],
        "docking_equip_slot_no": 65535,
        "spawn_distance_level": 4294967295,
        "is_item_equip_docking_gimmick": 1,
        "send_damage_to_parent": 0,
        "is_body_part": 0,
        "docking_type": 0,
        "is_summoner_team": 0,
        "is_player_only": 0,
        "is_npc_only": 0,
        "is_sync_break_parent": 0,
        "hit_part": 0,
        "detected_by_npc": 0,
        "is_bag_docking": 0,
        "enable_collision": 0,
        "disable_collision_with_other_gimmick": 1,
        "docking_slot_key": "",
    },
}
EXTRA_SOCKETS = [
    {"item": 1, "value": 6000},
    {"item": 1, "value": 8000},
    {"item": 1, "value": 10000},
    {"item": 1, "value": 12000},
    {"item": 1, "value": 14000},
]


def main() -> int:
    print(f"=> Extracting vanilla iteminfo.pabgb from {GAME_DIR}")
    raw = crimson_rs.extract_file(GAME_DIR, "0008", INTERNAL_DIR, "iteminfo.pabgb")
    print(f"   {len(raw):,} bytes")

    items = crimson_rs.parse_iteminfo_from_bytes(raw)
    print(f"   parsed {len(items)} items")

    target = next((it for it in items if it.get("key") == TARGET_KEY), None)
    if target is None:
        print(f"!! item key {TARGET_KEY} not found. Aborting.")
        return 2
    print(f"\n=> Target item {TARGET_KEY}: {target.get('string_key')}")
    print(f"   category_info={target.get('category_info')}  "
          f"equip_type_info={target.get('equip_type_info')}")
    print(f"   BEFORE: equip_passive_skill_list = {target.get('equip_passive_skill_list')}")
    print(f"   BEFORE: gimmick_info              = {target.get('gimmick_info')}")
    print(f"   BEFORE: docking_child_data tag    = "
          f"{(target.get('docking_child_data') or {}).get('docking_tag_name_hash')}")

    print("\n=> Applying Potter's exact patch fields")
    for k, v in POTTER_DERICTUS_PATCH.items():
        target[k] = v
    ddd = target.setdefault("drop_default_data", {})
    lst = ddd.setdefault("add_socket_material_item_list", [])
    lst.extend(EXTRA_SOCKETS)
    print(f"   added {len(EXTRA_SOCKETS)} socket slots (total={len(lst)})")

    print(f"   AFTER:  equip_passive_skill_list = {target['equip_passive_skill_list']}")
    print(f"   AFTER:  gimmick_info              = {target['gimmick_info']}")
    print(f"   AFTER:  attach_parent_socket_name = "
          f"{target['docking_child_data']['attach_parent_socket_name']}")

    print("\n=> Re-serializing iteminfo...")
    new_bytes = bytes(crimson_rs.serialize_iteminfo(items))
    print(f"   {len(new_bytes):,} bytes (delta {len(new_bytes) - len(raw):+d})")

    with tempfile.TemporaryDirectory() as tmp:
        mod_dir = os.path.join(tmp, "gamedata", "binary__", "client", "bin")
        os.makedirs(mod_dir, exist_ok=True)
        with open(os.path.join(mod_dir, "iteminfo.pabgb"), "wb") as f:
            f.write(new_bytes)

        out_dir = os.path.join(tmp, "output")
        os.makedirs(out_dir, exist_ok=True)

        print(f"\n=> Packing with pack_mod into group '{BUFF_DIR}'")
        crimson_rs.pack_mod.pack_mod(
            game_dir=GAME_DIR,
            mod_folder=tmp,
            output_dir=out_dir,
            group_name=BUFF_DIR,
        )

        game_mod = os.path.join(GAME_DIR, BUFF_DIR)
        if os.path.isdir(game_mod):
            shutil.rmtree(game_mod)
        os.makedirs(game_mod, exist_ok=True)
        shutil.copy2(os.path.join(out_dir, BUFF_DIR, "0.paz"),
                      os.path.join(game_mod, "0.paz"))
        shutil.copy2(os.path.join(out_dir, BUFF_DIR, "0.pamt"),
                      os.path.join(game_mod, "0.pamt"))
        print(f"   installed -> {game_mod}")

        papgt_src = os.path.join(out_dir, "meta", "0.papgt")
        papgt_dst = os.path.join(GAME_DIR, "meta", "0.papgt")
        papgt_vanilla = papgt_dst + ".vanilla"
        if not os.path.isfile(papgt_vanilla):
            shutil.copy2(papgt_dst, papgt_vanilla)
            print(f"   vanilla papgt backup -> {papgt_vanilla}")
        shutil.copy2(papgt_src, papgt_dst)
        print(f"   installed papgt -> {papgt_dst}")

    print("\n=> Done.")
    print("   Launch the game and equip the Derictus Spear (item 1000082).")
    print("   Expected: Lightning/Fire/Bismuth imbue passives + Marni gimmick VFX.")
    print("   To undo: delete the 0058/ folder and copy 0.papgt.vanilla -> 0.papgt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
