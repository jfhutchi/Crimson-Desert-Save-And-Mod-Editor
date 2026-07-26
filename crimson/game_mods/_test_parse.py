import struct

new = open('_new_iteminfo.pabgb', 'rb').read()
old = open('C:/Users/Coding/CrimsonDesertModding/extractedpaz/0008_full/iteminfo.pabgb', 'rb').read()

def r8(d, o): return d[o], o+1
def r16(d, o): return struct.unpack_from('<H', d, o)[0], o+2
def r32(d, o): return struct.unpack_from('<I', d, o)[0], o+4
def r64(d, o): return struct.unpack_from('<Q', d, o)[0], o+8
def rcs(d, o):
    length, o2 = r32(d, o)
    if length > 1000000: raise ValueError(f"bad string len {length} at 0x{o:X}")
    s = d[o2:o2+length]
    try: text = s.decode('utf-8')
    except: text = '<non-utf8>'
    return text, length, o2+length

def parse_full_item(d, off, has_new_field=True):
    start = off
    key, off = r32(d, off)
    skey, _, off = rcs(d, off)
    _, off = r8(d, off)
    _, off = r64(d, off)
    _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
    _, off = r32(d, off)
    _, off = r16(d, off)
    _, off = r32(d, off)
    cnt, off = r32(d, off)
    for i in range(cnt):
        _, off = r32(d, off); c2, off = r32(d, off); off += c2
    cnt, off = r32(d, off); off += cnt * 4
    _, off = r32(d, off)
    cnt, off = r32(d, off); off += cnt * 4
    cnt, off = r32(d, off); off += cnt * 4
    cnt, off = r32(d, off)
    for i in range(cnt):
        _, off = r32(d, off); _, off = r8(d, off); gc, off = r32(d, off); off += gc * 4
    _, off = r32(d, off); _, off = r32(d, off)
    _, off = r8(d, off); _, off = r8(d, off)
    _, off = r32(d, off); _, off = r32(d, off)
    _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
    _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
    _, off = r32(d, off)
    _, off = r16(d, off)
    _, off = r32(d, off)
    _, off = r8(d, off)
    _, off = r32(d, off)
    cnt, off = r32(d, off); off += cnt * 8
    _, off = r8(d, off)
    _, off = r8(d, off)
    _, off = r32(d, off)

    if has_new_field:
        off += 6  # NEW: u32 + u16

    _, _, off = rcs(d, off)
    _, _, off = rcs(d, off)
    _, off = r32(d, off)
    cnt, off = r32(d, off)
    for j in range(cnt): _, _, off = rcs(d, off)
    _, off = r32(d, off)
    _, off = r8(d, off)
    _, off = r8(d, off)
    for lst in range(5):
        cnt, off = r32(d, off)
        for j in range(cnt):
            tt, off = r8(d, off); _, off = r32(d, off); _, off = r64(d, off)
            if tt in (0,1,3,4): _, off = r32(d, off)
            elif tt == 2: _, _, off = rcs(d, off)
            else: raise ValueError(f"bad sealable type {tt}")
    cnt, off = r32(d, off); off += cnt * 4
    _, off = r8(d, off)
    _, off = r32(d, off)
    _, off = r8(d, off)
    cnt, off = r32(d, off); off += cnt * 4
    cnt, off = r32(d, off); off += cnt * 4
    cnt, off = r32(d, off); off += cnt * 2
    _, off = r8(d, off)
    cnt, off = r32(d, off); off += cnt * 4
    _, off = r8(d, off)
    _, off = r8(d, off)
    _, off = r8(d, off)
    _, off = r8(d, off)
    _, off = r8(d, off)
    _, off = r8(d, off)
    cnt, off = r32(d, off); off += cnt * 8
    _, off = r8(d, off)
    _, off = r8(d, off)
    _, off = r8(d, off)
    # DropDefaultData
    _, off = r16(d, off)
    cnt, off = r32(d, off); off += cnt * 4
    cnt, off = r32(d, off); off += cnt * 12
    ti, off = r8(d, off)
    if ti in (0,3,9): _, off = r32(d, off)
    elif ti != 14: raise ValueError(f"bad SubItem type {ti}")
    _, off = r8(d, off)
    _, off = r8(d, off)
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
        _, off = r16(d, off)
        for k in range(3):
            sc, off = r32(d, off); off += sc * 12
        sc, off = r32(d, off); off += sc * 5
        pc, off = r32(d, off); off += pc * 20
        bc, off = r32(d, off); off += bc * 8
    # gimmick_visual_prefab_data_list
    cnt, off = r32(d, off)
    for j in range(cnt):
        _, off = r32(d, off); off += 12
        pc, off = r32(d, off); off += pc * 4
        ac, off = r32(d, off); off += ac * 4
        _, off = r8(d, off)
    # price_list
    cnt, off = r32(d, off); off += cnt * 20
    # docking_child_data
    flag, off = r8(d, off)
    if flag:
        _, off = r32(d, off); _, off = r32(d, off); _, off = r32(d, off)
        _, _, off = rcs(d, off); _, _, off = rcs(d, off)
        off += 16
        _, off = r16(d, off); _, off = r32(d, off)
        for f in range(8): _, off = r8(d, off)
        _, off = r32(d, off)
        for f in range(5): _, off = r8(d, off)
        _, _, off = rcs(d, off)
    # inventory_change_data
    flag, off = r8(d, off)
    if flag:
        _, off = r8(d, off); _, off = r32(d, off); _, off = r32(d, off); _, off = r32(d, off)
        _, off = r16(d, off)
    _, _, off = rcs(d, off)
    for lst in range(2):
        cnt, off = r32(d, off)
        for j in range(cnt):
            _, _, off = rcs(d, off); _, _, off = rcs(d, off)
            _, off = r32(d, off); _, off = r32(d, off)
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
    _, off = r32(d, off); _, off = r32(d, off)
    _, _, off = rcs(d, off); _, _, off = rcs(d, off)
    ti, off = r8(d, off)
    if ti in (0,3,9): _, off = r32(d, off)
    elif ti != 14: raise ValueError(f"bad SubItem type {ti}")
    _, off = r64(d, off)
    _, off = r8(d, off)
    _, off = r16(d, off); _, off = r16(d, off)
    for k in range(3):
        sc, off = r32(d, off); off += sc * 12
    sc, off = r32(d, off); off += sc * 5
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
    flag, off = r8(d, off)
    if flag:
        _, off = r64(d, off)
        mc, off = r32(d, off)
        for j in range(mc):
            _, off = r32(d, off)
            _, _, off = rcs(d, off); _, off = r32(d, off); _, off = r32(d, off)
            _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
            _, off = r8(d, off); _, off = r64(d, off); _, _, off = rcs(d, off)
    _, _, off = rcs(d, off)
    for f in range(3): _, off = r8(d, off)
    _, off = r64(d, off)
    _, off = r16(d, off)
    cnt, off = r32(d, off); off += cnt * 15

    return off, key, skey, off - start

# Parse ALL items from NEW file
print("=== NEW FILE (with new 6-byte field) ===")
off = 0
count = 0
try:
    while off < len(new):
        off, key, skey, sz = parse_full_item(new, off, has_new_field=True)
        count += 1
        if count <= 5 or count % 1000 == 0:
            print(f"  Item {count}: key={key} ({skey[:25]}) size={sz}")
except Exception as e:
    print(f"  FAIL at item {count+1}, offset 0x{off:X}: {e}")
print(f"  Total parsed: {count} items, final offset: 0x{off:X} / 0x{len(new):X}")

# Parse ALL items from OLD file
print("\n=== OLD FILE (without new field) ===")
off = 0
count = 0
try:
    while off < len(old):
        off, key, skey, sz = parse_full_item(old, off, has_new_field=False)
        count += 1
        if count <= 5 or count % 1000 == 0:
            print(f"  Item {count}: key={key} ({skey[:25]}) size={sz}")
except Exception as e:
    print(f"  FAIL at item {count+1}, offset 0x{off:X}: {e}")
print(f"  Total parsed: {count} items, final offset: 0x{off:X} / 0x{len(old):X}")
