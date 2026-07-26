import struct, sys

new = open('_new_iteminfo.pabgb', 'rb').read()
old = open('C:/Users/Coding/CrimsonDesertModding/extractedpaz/0008_full/iteminfo.pabgb', 'rb').read()

def r8(d, o): return d[o], o+1
def r16(d, o): return struct.unpack_from('<H', d, o)[0], o+2
def r32(d, o): return struct.unpack_from('<I', d, o)[0], o+4
def r64(d, o): return struct.unpack_from('<Q', d, o)[0], o+8
def rcs(d, o):
    length, o2 = r32(d, o)
    if length > 1000000:
        raise ValueError("bad string len %d at 0x%X" % (length, o))
    return d[o2:o2+length], length, o2+length

def parse_item(d, off, has_new_field):
    start = off
    key, off = r32(d, off)
    _, slen, off = rcs(d, off)
    _, off = r8(d, off)  # is_blocked
    _, off = r64(d, off)  # max_stack
    # item_name
    _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
    _, off = r32(d, off)  # broken_item_prefix
    _, off = r16(d, off)  # inventory
    _, off = r32(d, off)  # equip_type
    # occupied_equip
    cnt, off = r32(d, off)
    for i in range(cnt):
        _, off = r32(d, off)
        c2, off = r32(d, off)
        off += c2
    # item_tag_list
    cnt, off = r32(d, off); off += cnt * 4
    _, off = r32(d, off)  # equipable_hash
    # consumable_type_list
    cnt, off = r32(d, off); off += cnt * 4
    # item_use_info_list
    cnt, off = r32(d, off); off += cnt * 4
    # item_icon_list
    cnt, off = r32(d, off)
    for i in range(cnt):
        _, off = r32(d, off)
        _, off = r8(d, off)
        gc, off = r32(d, off)
        off += gc * 4
    _, off = r32(d, off)  # map_icon
    _, off = r32(d, off)  # money_icon
    _, off = r8(d, off)   # use_map_icon_alert
    _, off = r8(d, off)   # item_type
    _, off = r32(d, off)  # material_key
    _, off = r32(d, off)  # material_match
    # item_desc
    _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
    # item_desc2
    _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
    _, off = r32(d, off)  # equipable_level
    _, off = r16(d, off)  # category
    _, off = r32(d, off)  # knowledge
    _, off = r8(d, off)   # knowledge_obtain_type
    _, off = r32(d, off)  # destroy_effect
    # equip_passive_skill_list
    cnt, off = r32(d, off); off += cnt * 8
    _, off = r8(d, off)   # use_immediately
    _, off = r8(d, off)   # apply_max_stack_cap
    _, off = r32(d, off)  # extract_multi_change

    if has_new_field:
        off += 6

    # item_memo, filter_type
    _, _, off = rcs(d, off)
    _, _, off = rcs(d, off)
    _, off = r32(d, off)  # gimmick_info
    # gimmick_tag_list
    cnt, off = r32(d, off)
    for j in range(cnt):
        _, _, off = rcs(d, off)
    _, off = r32(d, off)  # max_drop_result
    _, off = r8(d, off)   # use_drop_set_target
    _, off = r8(d, off)   # is_all_gimmick_sealable

    # 5 sealable lists
    for lst in range(5):
        cnt, off = r32(d, off)
        for j in range(cnt):
            tt, off = r8(d, off)
            _, off = r32(d, off)
            _, off = r64(d, off)
            if tt in (0, 1, 3, 4):
                _, off = r32(d, off)
            elif tt == 2:
                _, _, off = rcs(d, off)
            else:
                raise ValueError("bad sealable type %d" % tt)

    # sealable_money
    cnt, off = r32(d, off); off += cnt * 4
    _, off = r8(d, off)   # delete_by_gimmick
    _, off = r32(d, off)  # gimmick_unlock_msg
    _, off = r8(d, off)   # can_disassemble
    # 3 transmutation lists
    cnt, off = r32(d, off); off += cnt * 4
    cnt, off = r32(d, off); off += cnt * 4
    cnt, off = r32(d, off); off += cnt * 2
    _, off = r8(d, off)   # is_register_trade_market
    # multi_change_info_list
    cnt, off = r32(d, off); off += cnt * 4
    # 6 u8 booleans
    for f in range(6):
        _, off = r8(d, off)
    # reserve_slot_target_data_list
    cnt, off = r32(d, off); off += cnt * 8
    # 3 u8 (tier, important, apply_drop_stat)
    for f in range(3):
        _, off = r8(d, off)

    # DropDefaultData
    _, off = r16(d, off)   # drop_enchant_level
    cnt, off = r32(d, off); off += cnt * 4   # socket_item_list
    cnt, off = r32(d, off); off += cnt * 12  # add_socket_material
    ti, off = r8(d, off)   # default_sub_item
    if ti in (0, 3, 9):
        _, off = r32(d, off)
    elif ti != 14:
        raise ValueError("bad SubItem type %d at 0x%X" % (ti, off-1))
    _, off = r8(d, off)    # socket_valid_count
    _, off = r8(d, off)    # use_socket

    # prefab_data_list
    cnt, off = r32(d, off)
    for j in range(cnt):
        pc, off = r32(d, off); off += pc * 4
        ec, off = r32(d, off); off += ec * 2
        tc, off = r32(d, off); off += tc * 4
        _, off = r8(d, off)

    # enchant_data_list
    cnt, off = r32(d, off)
    for j in range(cnt):
        _, off = r16(d, off)  # level
        for k in range(3):
            sc, off = r32(d, off); off += sc * 12
        sc, off = r32(d, off); off += sc * 5
        pc, off = r32(d, off); off += pc * 20
        bc, off = r32(d, off); off += bc * 8

    # gimmick_visual_prefab_data_list
    cnt, off = r32(d, off)
    for j in range(cnt):
        _, off = r32(d, off)
        off += 12  # scale [f32;3]
        pc, off = r32(d, off); off += pc * 4
        ac, off = r32(d, off); off += ac * 4
        _, off = r8(d, off)

    # price_list
    cnt, off = r32(d, off); off += cnt * 20

    # docking_child_data (COptional)
    flag, off = r8(d, off)
    if flag:
        _, off = r32(d, off)
        _, off = r32(d, off)
        _, off = r32(d, off)
        _, _, off = rcs(d, off)
        _, _, off = rcs(d, off)
        off += 16
        _, off = r16(d, off)
        _, off = r32(d, off)
        for f in range(8):
            _, off = r8(d, off)
        _, off = r32(d, off)  # is_npc_only
        for f in range(5):
            _, off = r8(d, off)
        _, _, off = rcs(d, off)

    # inventory_change_data (COptional)
    flag, off = r8(d, off)
    if flag:
        _, off = r8(d, off)
        _, off = r32(d, off)
        _, off = r32(d, off)
        _, off = r32(d, off)
        _, off = r16(d, off)

    # unk_texture_path
    _, _, off = rcs(d, off)

    # fixed_page_data_list, dynamic_page_data_list
    for lst in range(2):
        cnt, off = r32(d, off)
        for j in range(cnt):
            _, _, off = rcs(d, off)
            _, _, off = rcs(d, off)
            _, off = r32(d, off)
            _, off = r32(d, off)

    # inspect_data_list
    cnt, off = r32(d, off)
    for j in range(cnt):
        _, off = r32(d, off)  # item_info
        _, off = r32(d, off)  # gimmick_info
        _, off = r32(d, off)  # character_info
        _, off = r32(d, off)  # spawn_reason_hash
        _, _, off = rcs(d, off)  # socket_name
        _, off = r32(d, off)  # speak_character
        _, off = r32(d, off)  # inspect_target_tag
        _, off = r8(d, off)   # reward_own_knowledge
        _, off = r32(d, off)  # reward_knowledge
        # item_desc (LocalizableString)
        _, off = r8(d, off)
        _, off = r64(d, off)
        _, _, off = rcs(d, off)
        _, off = r32(d, off)  # board_key
        _, off = r8(d, off)   # inspect_action_type
        _, off = r32(d, off)  # gimmick_state_name_hash
        _, off = r32(d, off)  # target_page_index
        _, off = r8(d, off)   # is_left_page
        _, off = r32(d, off)  # target_page_related
        _, off = r8(d, off)   # enable_read_after
        _, off = r8(d, off)   # refer_to_left
        _, off = r32(d, off)  # inspect_effect
        _, off = r32(d, off)  # inspect_complete_effect

    # inspect_action
    _, off = r32(d, off)  # action_name_hash
    _, off = r32(d, off)  # catch_tag_name_hash
    _, _, off = rcs(d, off)  # catcher_socket_name
    _, _, off = rcs(d, off)  # catch_target_socket_name

    # default_sub_item (top-level)
    ti, off = r8(d, off)
    if ti in (0, 3, 9):
        _, off = r32(d, off)
    elif ti != 14:
        raise ValueError("bad top SubItem type %d at 0x%X" % (ti, off-1))

    _, off = r64(d, off)   # cooltime
    _, off = r8(d, off)    # item_charge_type

    # sharpness_data
    _, off = r16(d, off)   # max_sharpness
    _, off = r16(d, off)   # craft_tool_info
    for k in range(3):
        sc, off = r32(d, off); off += sc * 12
    sc, off = r32(d, off); off += sc * 5

    _, off = r32(d, off)   # max_charged_useable_count
    cnt, off = r32(d, off); off += cnt * 2  # hackable_character
    cnt, off = r32(d, off); off += cnt * 2  # item_group_info
    off += 4  # discard_offset_y (f32)
    for f in range(4):
        _, off = r8(d, off)  # hide/shield/tower/wild
    for f in range(3):
        _, off = r32(d, off)  # packed/unpacked/convert
    _, off = r32(d, off)  # game_advice
    _, off = r32(d, off)  # mission
    for f in range(4):
        _, off = r8(d, off)
    _, off = r32(d, off)  # shared_cool_time

    # item_bundle_data_list
    cnt, off = r32(d, off); off += cnt * 12

    # money_type_define (COptional)
    flag, off = r8(d, off)
    if flag:
        _, off = r64(d, off)  # price_floor_value
        mc, off = r32(d, off)
        for j in range(mc):
            _, off = r32(d, off)  # key
            # UnitData
            _, _, off = rcs(d, off)   # ui_component
            _, off = r32(d, off)      # minimum
            _, off = r32(d, off)      # icon_path
            # item_name (LocalizableString)
            _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
            # item_desc (LocalizableString)
            _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)

    # emoji_texture_id
    _, _, off = rcs(d, off)
    for f in range(3):
        _, off = r8(d, off)
    _, off = r64(d, off)  # respawn_time
    _, off = r16(d, off)  # max_endurance
    # repair_data_list
    cnt, off = r32(d, off); off += cnt * 15

    return off, key, off - start


# Test new file
print("=== NEW FILE ===")
off = 0
count = 0
try:
    while off < len(new):
        off, key, sz = parse_item(new, off, True)
        count += 1
        if count <= 3:
            print("  Item %d: key=%d size=%d" % (count, key, sz))
except Exception as e:
    import traceback
    traceback.print_exc()
    print("  FAIL at item %d, offset 0x%X: %s" % (count+1, off, e))
print("  Total: %d items, offset 0x%X / 0x%X" % (count, off, len(new)))

# Test old file
print("\n=== OLD FILE ===")
off = 0
count = 0
try:
    while off < len(old):
        off, key, sz = parse_item(old, off, False)
        count += 1
        if count <= 3:
            print("  Item %d: key=%d size=%d" % (count, key, sz))
except Exception as e:
    import traceback
    traceback.print_exc()
    print("  FAIL at item %d, offset 0x%X: %s" % (count+1, off, e))
print("  Total: %d items, offset 0x%X / 0x%X" % (count, off, len(old)))
