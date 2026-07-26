import struct

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

off = 0

# Same trace as before but with debug prints up to DropDefaultData
key, off = r32(new, off)
skey, _, off = rcs(new, off)
_, off = r8(new, off)
_, off = r64(new, off)
_, off = r8(new, off); _, off = r64(new, off); _, _, off = rcs(new, off)
_, off = r32(new, off)
_, off = r16(new, off)
_, off = r32(new, off)
cnt, off = r32(new, off)
for i in range(cnt):
    _, off = r32(new, off); c2, off = r32(new, off); off += c2
cnt, off = r32(new, off); off += cnt * 4
_, off = r32(new, off)
cnt, off = r32(new, off); off += cnt * 4
cnt, off = r32(new, off); off += cnt * 4
cnt, off = r32(new, off)
for i in range(cnt):
    _, off = r32(new, off); _, off = r8(new, off); gc, off = r32(new, off); off += gc * 4
_, off = r32(new, off); _, off = r32(new, off)
_, off = r8(new, off); _, off = r8(new, off)
_, off = r32(new, off); _, off = r32(new, off)
_, off = r8(new, off); _, off = r64(new, off); _, _, off = rcs(new, off)
_, off = r8(new, off); _, off = r64(new, off); _, _, off = rcs(new, off)
_, off = r32(new, off)
_, off = r16(new, off)
_, off = r32(new, off)
_, off = r8(new, off)
_, off = r32(new, off)
cnt, off = r32(new, off); off += cnt * 8
_, off = r8(new, off)
_, off = r8(new, off)
_, off = r32(new, off)
print("after extract_multi_change 0x%X" % off)

# NEW FIELD
off += 6
print("after new field 0x%X" % off)

_, _, off = rcs(new, off)  # item_memo
_, _, off = rcs(new, off)  # filter_type
print("after filter_type 0x%X" % off)

_, off = r32(new, off)  # gimmick_info
cnt, off = r32(new, off)
for j in range(cnt): _, _, off = rcs(new, off)
_, off = r32(new, off)  # max_drop_result
_, off = r8(new, off)   # use_drop_set_target
_, off = r8(new, off)   # is_all_gimmick_sealable
print("before sealable 0x%X" % off)

for lst in range(5):
    cnt, off = r32(new, off)
    for j in range(cnt):
        tt, off = r8(new, off); _, off = r32(new, off); _, off = r64(new, off)
        if tt in (0, 1, 3, 4): _, off = r32(new, off)
        elif tt == 2: _, _, off = rcs(new, off)
        else: raise ValueError("bad sealable type %d" % tt)
    print("  sealable[%d] cnt=%d" % (lst, cnt))

cnt, off = r32(new, off); off += cnt * 4  # sealable_money
_, off = r8(new, off)
_, off = r32(new, off)
_, off = r8(new, off)
cnt, off = r32(new, off); off += cnt * 4
cnt, off = r32(new, off); off += cnt * 4
cnt, off = r32(new, off); off += cnt * 2
_, off = r8(new, off)
cnt, off = r32(new, off); off += cnt * 4
print("after multi_change 0x%X" % off)

for f in range(6): _, off = r8(new, off)
cnt, off = r32(new, off); off += cnt * 8  # reserve_slot
for f in range(3): _, off = r8(new, off)
print("before DropDefaultData 0x%X" % off)

# DropDefaultData
v, off = r16(new, off)
print("  drop_enchant_level=%d" % v)
cnt, off = r32(new, off)
print("  socket_item_list count=%d" % cnt)
off += cnt * 4
cnt, off = r32(new, off)
print("  add_socket_material count=%d" % cnt)
off += cnt * 12
print("  before SubItem 0x%X" % off)
print("  next bytes: %s" % new[off:off+8].hex())
ti, off = r8(new, off)
print("  SubItem type=%d" % ti)
if ti in (0, 3, 9):
    _, off = r32(new, off)
elif ti == 14:
    pass
else:
    print("  BAD SubItem type!")
_, off = r8(new, off)
_, off = r8(new, off)
print("after DropDefaultData 0x%X" % off)

# prefab_data_list
cnt, off = r32(new, off)
print("prefab count=%d" % cnt)
for j in range(cnt):
    pc, off = r32(new, off); off += pc * 4
    ec, off = r32(new, off); off += ec * 2
    tc, off = r32(new, off); off += tc * 4
    _, off = r8(new, off)

# enchant_data_list
cnt, off = r32(new, off)
print("enchant count=%d" % cnt)

# Continue...
for j in range(cnt):
    _, off = r16(new, off)
    for k in range(3):
        sc, off = r32(new, off); off += sc * 12
    sc, off = r32(new, off); off += sc * 5
    pc, off = r32(new, off); off += pc * 20
    bc, off = r32(new, off); off += bc * 8

cnt, off = r32(new, off)
print("gimmick_visual count=%d" % cnt)
for j in range(cnt):
    _, off = r32(new, off); off += 12
    pc, off = r32(new, off); off += pc * 4
    ac, off = r32(new, off); off += ac * 4
    _, off = r8(new, off)

cnt, off = r32(new, off)
print("price_list count=%d" % cnt)
off += cnt * 20

flag, off = r8(new, off)
print("docking flag=%d" % flag)
if flag:
    _, off = r32(new, off); _, off = r32(new, off); _, off = r32(new, off)
    _, _, off = rcs(new, off); _, _, off = rcs(new, off)
    off += 16
    _, off = r16(new, off); _, off = r32(new, off)
    for f in range(8): _, off = r8(new, off)
    _, off = r32(new, off)
    for f in range(5): _, off = r8(new, off)
    _, _, off = rcs(new, off)

flag, off = r8(new, off)
print("inv_change flag=%d" % flag)
if flag:
    _, off = r8(new, off); _, off = r32(new, off); _, off = r32(new, off); _, off = r32(new, off)
    _, off = r16(new, off)

_, _, off = rcs(new, off)
print("after unk_texture 0x%X" % off)

for lst in range(2):
    cnt, off = r32(new, off)
    for j in range(cnt):
        _, _, off = rcs(new, off); _, _, off = rcs(new, off)
        _, off = r32(new, off); _, off = r32(new, off)

cnt, off = r32(new, off)
print("inspect_data count=%d" % cnt)
for j in range(cnt):
    for f in range(4): _, off = r32(new, off)
    _, _, off = rcs(new, off)
    _, off = r32(new, off); _, off = r32(new, off)
    _, off = r8(new, off); _, off = r32(new, off)
    _, off = r8(new, off); _, off = r64(new, off); _, _, off = rcs(new, off)
    _, off = r32(new, off); _, off = r8(new, off); _, off = r32(new, off); _, off = r32(new, off)
    _, off = r8(new, off); _, off = r32(new, off)
    _, off = r8(new, off); _, off = r8(new, off)
    _, off = r32(new, off); _, off = r32(new, off)

_, off = r32(new, off); _, off = r32(new, off)
_, _, off = rcs(new, off); _, _, off = rcs(new, off)
print("after inspect_action 0x%X" % off)

ti, off = r8(new, off)
print("top SubItem type=%d" % ti)
if ti in (0, 3, 9): _, off = r32(new, off)
elif ti != 14: raise ValueError("bad")

_, off = r64(new, off)
_, off = r8(new, off)
_, off = r16(new, off); _, off = r16(new, off)
for k in range(3):
    sc, off = r32(new, off); off += sc * 12
sc, off = r32(new, off); off += sc * 5
print("after sharpness 0x%X" % off)

_, off = r32(new, off)
cnt, off = r32(new, off); off += cnt * 2
cnt, off = r32(new, off); off += cnt * 2
off += 4
for f in range(4): _, off = r8(new, off)
for f in range(3): _, off = r32(new, off)
_, off = r32(new, off); _, off = r32(new, off)
for f in range(4): _, off = r8(new, off)
_, off = r32(new, off)
cnt, off = r32(new, off); off += cnt * 12
print("before money_type 0x%X" % off)

flag, off = r8(new, off)
print("money_type flag=%d" % flag)
if flag:
    _, off = r64(new, off)
    mc, off = r32(new, off)
    for j in range(mc):
        _, off = r32(new, off)
        _, _, off = rcs(new, off); _, off = r32(new, off); _, off = r32(new, off)
        _, off = r8(new, off); _, off = r64(new, off); _, _, off = rcs(new, off)
        _, off = r8(new, off); _, off = r64(new, off); _, _, off = rcs(new, off)

_, _, off = rcs(new, off)
for f in range(3): _, off = r8(new, off)
_, off = r64(new, off)
_, off = r16(new, off)
cnt, off = r32(new, off); off += cnt * 15
print("ITEM END 0x%X (size %d)" % (off, off))

# Check next item
key2, _ = r32(new, off)
print("next key=%d" % key2)
