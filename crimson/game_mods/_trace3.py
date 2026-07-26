import struct

old = open('C:/Users/Coding/CrimsonDesertModding/extractedpaz/0008_full/iteminfo.pabgb', 'rb').read()
new = open('_new_iteminfo.pabgb', 'rb').read()

def r8(d, o): return d[o], o+1
def r16(d, o): return struct.unpack_from('<H', d, o)[0], o+2
def r32(d, o): return struct.unpack_from('<I', d, o)[0], o+4
def r64(d, o): return struct.unpack_from('<Q', d, o)[0], o+8
def rcs(d, o):
    length, o2 = r32(d, o)
    if length > 1000000:
        raise ValueError("bad string len %d at 0x%X" % (length, o))
    return d[o2:o2+length], length, o2+length

def trace_from_multi_change(d, off, label, has_new_fields):
    print("\n=== %s from 0x%X ===" % (label, off))

    if has_new_fields:
        v1, off2 = r32(d, off)
        v2, off2 = r16(d, off2)
        print("NEW_FIELD_1: u32=%d u16=0x%04X next=0x%X" % (v1, v2, off2))
        off = off2

    s, slen, off = rcs(d, off)
    print("item_memo len=%d next=0x%X" % (slen, off))
    s, slen, off = rcs(d, off)
    print("filter_type len=%d next=0x%X" % (slen, off))
    _, off = r32(d, off)
    cnt, off = r32(d, off)
    for j in range(cnt): _, _, off = rcs(d, off)
    print("gimmick_tag count=%d next=0x%X" % (cnt, off))
    _, off = r32(d, off)
    _, off = r8(d, off)
    _, off = r8(d, off)
    for lst in range(5):
        cnt, off = r32(d, off)
        for j in range(cnt):
            tt, off = r8(d, off); _, off = r32(d, off); _, off = r64(d, off)
            if tt in (0,1,3,4): _, off = r32(d, off)
            elif tt == 2: _, _, off = rcs(d, off)
    cnt, off = r32(d, off); off += cnt * 4
    _, off = r8(d, off); _, off = r32(d, off); _, off = r8(d, off)
    cnt, off = r32(d, off); off += cnt * 4
    cnt, off = r32(d, off); off += cnt * 4
    cnt, off = r32(d, off); off += cnt * 2
    _, off = r8(d, off)
    cnt, off = r32(d, off); off += cnt * 4
    print("after multi_change_info_list 0x%X" % off)

    # Booleans
    vals = []
    for f in range(5):
        v, off = r8(d, off)
        vals.append(v)
    print("5 u8s: %s next=0x%X" % (vals, off))

    if has_new_fields:
        v, off = r8(d, off)
        print("NEW_FIELD_2: u8=%d next=0x%X" % (v, off))

    v, off = r8(d, off)
    print("quick_slot_index=%d next=0x%X" % (v, off))

    # reserve_slot
    cnt, off = r32(d, off)
    off += cnt * 8
    print("reserve_slot count=%d next=0x%X" % (cnt, off))

    vals = []
    for f in range(3):
        v, off = r8(d, off)
        vals.append(v)
    print("tier/important/drop_stat: %s next=0x%X" % (vals, off))

    # DropDefaultData
    de, off = r16(d, off)
    cnt, off = r32(d, off); off += cnt * 4
    cnt2, off = r32(d, off); off += cnt2 * 12
    ti, off = r8(d, off)
    if ti in (0,3,9): _, off = r32(d, off)
    elif ti != 14: raise ValueError("bad SubItem %d" % ti)
    _, off = r8(d, off); _, off = r8(d, off)
    print("DropDefault: enchant=%d sockets=%d materials=%d SubItem=%d next=0x%X" % (de, cnt, cnt2, ti, off))

    # prefab
    cnt, off = r32(d, off)
    for j in range(cnt):
        pc, off = r32(d, off); off += pc * 4
        ec, off = r32(d, off); off += ec * 2
        tc, off = r32(d, off); off += tc * 4
        _, off = r8(d, off)
    print("prefab count=%d next=0x%X" % (cnt, off))

    # enchant
    cnt, off = r32(d, off)
    for j in range(cnt):
        _, off = r16(d, off)
        for k in range(3):
            sc, off = r32(d, off); off += sc * 12
        sc, off = r32(d, off); off += sc * 5
        pc, off = r32(d, off); off += pc * 20
        bc, off = r32(d, off); off += bc * 8
    print("enchant count=%d next=0x%X" % (cnt, off))

    # gimmick_visual
    cnt, off = r32(d, off)
    for j in range(cnt):
        _, off = r32(d, off); off += 12
        pc, off = r32(d, off); off += pc * 4
        ac, off = r32(d, off); off += ac * 4
        _, off = r8(d, off)
    print("gimmick_visual count=%d next=0x%X" % (cnt, off))

    # price
    cnt, off = r32(d, off); off += cnt * 20
    print("price count=%d next=0x%X" % (cnt, off))

    # docking
    flag, off = r8(d, off)
    if flag:
        _, off = r32(d, off); _, off = r32(d, off); _, off = r32(d, off)
        _, _, off = rcs(d, off); _, _, off = rcs(d, off)
        off += 16; _, off = r16(d, off); _, off = r32(d, off)
        for f in range(8): _, off = r8(d, off)
        _, off = r32(d, off)
        for f in range(5): _, off = r8(d, off)
        _, _, off = rcs(d, off)
    print("docking flag=%d next=0x%X" % (flag, off))

    # inv change
    flag, off = r8(d, off)
    if flag:
        _, off = r8(d, off); _, off = r32(d, off); _, off = r32(d, off); _, off = r32(d, off)
        _, off = r16(d, off)
    print("inv_change flag=%d next=0x%X" % (flag, off))

    _, _, off = rcs(d, off)
    print("after unk_texture next=0x%X" % off)

    for lst in range(2):
        cnt, off = r32(d, off)
        for j in range(cnt):
            _, _, off = rcs(d, off); _, _, off = rcs(d, off)
            _, off = r32(d, off); _, off = r32(d, off)
    print("after page_lists next=0x%X" % off)

    cnt, off = r32(d, off)
    for j in range(cnt):
        for f in range(4): _, off = r32(d, off)
        _, _, off = rcs(d, off)
        _, off = r32(d, off); _, off = r32(d, off)
        _, off = r8(d, off); _, off = r32(d, off)
        _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
        _, off = r32(d, off); _, off = r8(d, off); _, off = r32(d, off); _, off = r32(d, off)
        _, off = r8(d, off); _, off = r32(d, off)
        _, off = r8(d, off); _, off = r8(d, off)
        _, off = r32(d, off); _, off = r32(d, off)
    print("inspect count=%d next=0x%X" % (cnt, off))

    _, off = r32(d, off); _, off = r32(d, off)
    _, _, off = rcs(d, off); _, _, off = rcs(d, off)
    print("after inspect_action next=0x%X" % off)

    ti, off = r8(d, off)
    if ti in (0,3,9): _, off = r32(d, off)
    elif ti != 14: raise ValueError("bad SubItem %d" % ti)
    _, off = r64(d, off); _, off = r8(d, off)
    print("after cooltime/charge next=0x%X" % off)

    _, off = r16(d, off); _, off = r16(d, off)
    for k in range(3):
        sc, off = r32(d, off); off += sc * 12
    sc, off = r32(d, off); off += sc * 5
    print("after sharpness next=0x%X" % off)

    _, off = r32(d, off)
    cnt, off = r32(d, off); off += cnt * 2
    cnt, off = r32(d, off); off += cnt * 2
    off += 4
    for f in range(4): _, off = r8(d, off)
    for f in range(3): _, off = r32(d, off)
    _, off = r32(d, off); _, off = r32(d, off)
    for f in range(4): _, off = r8(d, off)
    _, off = r32(d, off)
    cnt, off = r32(d, off); off += cnt * 12
    print("after bundle next=0x%X" % off)

    flag, off = r8(d, off)
    if flag:
        _, off = r64(d, off)
        mc, off = r32(d, off)
        for j in range(mc):
            _, off = r32(d, off)
            _, _, off = rcs(d, off); _, off = r32(d, off); _, off = r32(d, off)
            _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
            _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
    print("money_type flag=%d next=0x%X" % (flag, off))

    _, _, off = rcs(d, off)
    for f in range(3): _, off = r8(d, off)
    _, off = r64(d, off)
    _, off = r16(d, off)
    cnt, off = r32(d, off); off += cnt * 15
    print("ITEM END at 0x%X (size=%d)" % (off, off - start if off > start else off))

    k2, _ = r32(d, off)
    print("Next key: %d" % k2)
    return off

# Old: extract_multi_change ends at 0xBF+4=0xC3 (no new field), we verified this
# New: extract_multi_change ends at 0xC3 (with new field starting here)
try:
    trace_from_multi_change(old, 0xC3, "OLD", False)
except Exception as e:
    print("OLD FAILED: %s" % e)

try:
    trace_from_multi_change(new, 0xC3, "NEW", True)
except Exception as e:
    print("NEW FAILED: %s" % e)
