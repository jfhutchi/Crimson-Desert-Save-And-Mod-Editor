"""Full pipeline: clone item + custom name via paloc + add to store + deploy."""
import sys, os, struct, shutil, tempfile, json
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__),
    '..', 'CrimsonSaveEditorGUI - Copy', 'Localization', 'tools'))
from crimson.game_mods import crimson_rs as crimson_rs
from crimson.game_mods.item_creator import (
    clone_item_bytes, append_items_to_iteminfo,
    find_next_free_key, compute_paloc_ids,
)
from crimson.game_mods.storeinfo_parser import StoreinfoParser
import paz_crypto

game_path = r'C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert'
dp = 'gamedata/binary__/client/bin'

NEW_KEY = 999001
NEW_INTERNAL = 'RicePaddy_GodSword'
DISPLAY_NAME = "Rice Paddy's God Sword"
DISPLAY_DESC = "A divine blade forged by RicePaddyMaster. 999k Attack. Legends say it can cut through reality itself."
DONOR_KEY = 200021  # Leinstead_OneHandSword
STORE_KEY = 1       # Store_Her_Equipment (Hernandel)

print('='*60)
print('FULL CUSTOM ITEM PIPELINE')
print('='*60)

# =====================================================================
# 1. CLONE ITEM
# =====================================================================
print('\n[1] Cloning item...')
body = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'iteminfo.pabgb'))
head = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'iteminfo.pabgh'))

count = struct.unpack_from('<H', head, 0)[0]
offsets = []
existing_keys = set()
for i in range(count):
    k = struct.unpack_from('<I', head, 2 + i*8)[0]
    o = struct.unpack_from('<I', head, 2 + i*8 + 4)[0]
    offsets.append((o, k))
    existing_keys.add(k)
offsets.sort()

# Find donor
donor_off = donor_end = None
for i, (o, k) in enumerate(offsets):
    if k == DONOR_KEY:
        donor_off = o
        donor_end = offsets[i+1][0] if i+1 < len(offsets) else len(body)
        break

donor_bytes = body[donor_off:donor_end]
print(f'  Donor: Leinstead_OneHandSword ({len(donor_bytes)}B)')

cloned = clone_item_bytes(donor_bytes, DONOR_KEY, NEW_KEY, NEW_INTERNAL)
print(f'  Cloned as {NEW_INTERNAL} key={NEW_KEY} ({len(cloned)}B)')

# =====================================================================
# 2. MAX STATS via crimson_rs
# =====================================================================
print('\n[2] Maxing stats...')
items = crimson_rs.parse_iteminfo_from_bytes(cloned)
if items:
    it = items[0]
    for ed in it.get('enchant_data_list', []):
        sd = ed.get('enchant_stat_data', {})
        for s in sd.get('stat_list_static', []):
            if s['stat'] in (1000002, 1000003, 1000000, 1000006):
                s['change_mb'] = 999000000
        for s in sd.get('stat_list_static_level', []):
            s['change_mb'] = 15
    cloned = crimson_rs.serialize_iteminfo(items)
    print(f'  Attack/Defense/HP/CritDmg = 999k, rates = 15')

# Append to iteminfo
new_body, new_head = append_items_to_iteminfo(body, head, [(NEW_KEY, cloned)])
print(f'  Iteminfo: {count} -> {count+1} items')

# =====================================================================
# 3. ADD TO STORE
# =====================================================================
print('\n[3] Adding to Hernandel Equipment Store...')
store_gb = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'storeinfo.pabgb'))
store_gh = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'storeinfo.pabgh'))

sp = StoreinfoParser()
sp.load_from_bytes(store_gh, store_gb)
store = sp.get_store_by_key(STORE_KEY)
print(f'  Store: {store.name} ({store.item_count} items)')

already = any(it.item_key == NEW_KEY for it in store.items)
if not already:
    sp.add_item(STORE_KEY, store.items[0].item_key, NEW_KEY, buy_price=1, sell_price=0)
    store = sp.get_store_by_key(STORE_KEY)
    print(f'  Added! Now {store.item_count} items')
else:
    print(f'  Already in store')

new_store_gb = bytes(sp._body_data)
new_store_gh = sp._header_data

# =====================================================================
# 4. PATCH LOCALIZATION (paloc)
# =====================================================================
print('\n[4] Patching localization...')

# Extract paloc from game
paloc_raw = bytes(crimson_rs.extract_file(
    game_path, '0020', 'gamedata/stringtable/binary__',
    'localizationstring_eng.paloc'))
print(f'  Extracted paloc: {len(paloc_raw):,}B (decrypted+decompressed by crimson_rs)')

# Parse paloc entries
from paloc_Tool import extract_paloc  # noqa — we'll do it inline
entries = []
off = 0
data = paloc_raw
while off < len(data) - 16:
    marker = data[off:off+8]
    if off + 12 > len(data):
        break
    k_len = struct.unpack_from('<I', data, off+8)[0]
    if k_len == 0 or k_len > 500:
        break
    if off + 12 + k_len + 4 > len(data):
        break
    key = data[off+12:off+12+k_len].decode('utf-8', errors='replace')
    v_off = off + 12 + k_len
    v_len = struct.unpack_from('<I', data, v_off)[0]
    if v_len > 500000:
        break
    val = data[v_off+4:v_off+4+v_len].decode('utf-8', errors='replace')
    entries.append({'marker': marker, 'key': key, 'value': val})
    off = v_off + 4 + v_len

tail = data[off:]
print(f'  Parsed {len(entries)} entries, tail={len(tail)}B')

# Compute paloc IDs
name_id, desc_id = compute_paloc_ids(NEW_KEY)
name_key = str(name_id)
desc_key = str(desc_id)
print(f'  Name ID: {name_key}')
print(f'  Desc ID: {desc_key}')

# Add entries
ITEM_MARKER = bytes.fromhex('0700000000000000')
# Remove any existing entries for this key
entries = [e for e in entries if e['key'] not in (name_key, desc_key)]
entries.append({'marker': ITEM_MARKER, 'key': name_key, 'value': DISPLAY_NAME})
entries.append({'marker': ITEM_MARKER, 'key': desc_key, 'value': DISPLAY_DESC})

# Sort entries: numeric keys ascending, then non-numeric
def sort_key(e):
    try:
        return (0, int(e['key']), '')
    except ValueError:
        return (1, 0, e['key'])
entries.sort(key=sort_key)
print(f'  Added name + description entries')

# Rebuild paloc binary
paloc_out = bytearray()
for e in entries:
    paloc_out += e['marker']
    kb = e['key'].encode('utf-8')
    paloc_out += struct.pack('<I', len(kb)) + kb
    vb = e['value'].encode('utf-8')
    paloc_out += struct.pack('<I', len(vb)) + vb
paloc_out += tail
print(f'  Rebuilt paloc: {len(paloc_out):,}B (+{len(paloc_out)-len(paloc_raw)})')

# =====================================================================
# 5. DEPLOY ALL OVERLAYS
# =====================================================================
print('\n[5] Deploying...')

def deploy_group(group_id, files):
    with tempfile.TemporaryDirectory() as tmp:
        gdir = os.path.join(tmp, group_id)
        os.makedirs(gdir)
        b = crimson_rs.PackGroupBuilder(
            gdir, crimson_rs.Compression.NONE, crimson_rs.Crypto.NONE)
        for path, name, data in files:
            b.add_file(path, name, data)
        pamt = bytes(b.finish())
        ck = crimson_rs.parse_pamt_bytes(pamt)['checksum']
        dest = os.path.join(game_path, group_id)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(dest)
        shutil.copy2(os.path.join(gdir, '0.paz'), os.path.join(dest, '0.paz'))
        shutil.copy2(os.path.join(gdir, '0.pamt'), os.path.join(dest, '0.pamt'))
        return ck

# 0058: iteminfo (new item)
ck58 = deploy_group('0058', [(dp, 'iteminfo.pabgb', new_body),
                              (dp, 'iteminfo.pabgh', new_head)])
print(f'  0058/ iteminfo ({count+1} items)')

# 0060: storeinfo (store has new item)
ck60 = deploy_group('0060', [(dp, 'storeinfo.pabgb', new_store_gb),
                              (dp, 'storeinfo.pabgh', new_store_gh)])
print(f'  0060/ storeinfo (Hernandel equip store)')

# 0064: paloc (item name + description)
ck64 = deploy_group('0064', [('gamedata/stringtable/binary__',
                               'localizationstring_eng.paloc',
                               bytes(paloc_out))])
print(f'  0064/ localization (custom item name)')

# PAPGT
papgt_path = os.path.join(game_path, 'meta', '0.papgt')
papgt = crimson_rs.parse_papgt_file(papgt_path)
papgt['entries'] = [
    e for e in papgt['entries']
    if e.get('group_name') not in ('0058', '0060', '0064')
]
papgt = crimson_rs.add_papgt_entry(papgt, '0058', ck58, 0, 16383)
papgt = crimson_rs.add_papgt_entry(papgt, '0060', ck60, 0, 16383)
papgt = crimson_rs.add_papgt_entry(papgt, '0064', ck64, 0, 16383)
crimson_rs.write_papgt_file(papgt, papgt_path)

mods = sorted(e['group_name'] for e in crimson_rs.parse_papgt_file(papgt_path)['entries']
              if int(e['group_name']) >= 33)
print(f'  PAPGT: {mods}')

print('\n' + '='*60)
print('DEPLOYED — BRAND NEW CUSTOM ITEM:')
print(f'  Name: "{DISPLAY_NAME}"')
print(f'  Key:  {NEW_KEY}')
print(f'  Stats: Attack=999k, Defense=999k, HP=999k, CritDmg=999k')
print(f'  Store: Hernandel Equipment Store, price=1 silver')
print(f'  Overlays: 0058 (item), 0060 (store), 0064 (name)')
print()
print('  Restart game -> Hernandel Equipment Store')
print('  Look for "Rice Paddy\'s God Sword" at 1 silver')
print('='*60)
