"""Full pipeline test: clone item, max stats, inject into store, deploy."""
import sys, os, struct, shutil, tempfile
sys.path.insert(0, os.path.dirname(__file__))
from crimson.game_mods import crimson_rs as crimson_rs
from crimson.game_mods.item_creator import clone_item_bytes, append_items_to_iteminfo, find_next_free_key
from crimson.game_mods.storeinfo_parser import StoreinfoParser

game_path = r'C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert'
dp = 'gamedata/binary__/client/bin'

print('='*60)
print('CUSTOM ITEM PIPELINE TEST')
print('='*60)

# =====================================================================
# 1. CLONE ITEM — Leinstead_OneHandSword -> key 999001
# =====================================================================
print('\n[1] Extracting vanilla iteminfo...')
body = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'iteminfo.pabgb'))
head = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'iteminfo.pabgh'))

count = struct.unpack_from('<H', head, 0)[0]
existing_keys = set()
offsets = []
for i in range(count):
    k = struct.unpack_from('<I', head, 2 + i*8)[0]
    o = struct.unpack_from('<I', head, 2 + i*8 + 4)[0]
    existing_keys.add(k)
    offsets.append((o, k))
offsets.sort()

# Find donor: Leinstead_OneHandSword (key 200021)
DONOR_KEY = 200021
donor_off = donor_end = None
for i, (o, k) in enumerate(offsets):
    if k == DONOR_KEY:
        donor_off = o
        donor_end = offsets[i+1][0] if i+1 < len(offsets) else len(body)
        break

if donor_off is None:
    print(f'Donor key {DONOR_KEY} not found! Trying first weapon...')
    # Fallback: use first item
    donor_off, DONOR_KEY = offsets[0]
    donor_end = offsets[1][0]

donor_bytes = body[donor_off:donor_end]
name_len = struct.unpack_from('<I', donor_bytes, 4)[0]
donor_name = donor_bytes[8:8+name_len].decode('ascii', errors='replace')
print(f'  Donor: {donor_name} (key={DONOR_KEY}, {len(donor_bytes)}B)')

NEW_KEY = find_next_free_key(existing_keys)
NEW_NAME = 'RicePaddy_GodSword'
print(f'  New key: {NEW_KEY}')

cloned = clone_item_bytes(donor_bytes, DONOR_KEY, NEW_KEY, NEW_NAME)
print(f'  Cloned: {len(cloned)}B')

# =====================================================================
# 2. MAX OUT STATS via crimson_rs
# =====================================================================
print('\n[2] Maxing out stats...')
# Parse the cloned item through crimson_rs to modify stats
# We need to build a temporary iteminfo with just the cloned item to parse it
temp_head = struct.pack('<H', 1) + struct.pack('<II', NEW_KEY, 0)
temp_items = crimson_rs.parse_iteminfo_from_bytes(cloned)
if temp_items:
    item = temp_items[0]
    edl = item.get('enchant_data_list', [])
    if edl:
        for level_idx, ed in enumerate(edl):
            sd = ed.get('enchant_stat_data', {})
            # Max flat stats (attack, defense, etc.)
            for s in sd.get('stat_list_static', []):
                old = s['change_mb']
                if s['stat'] == 1000002:  # Attack
                    s['change_mb'] = 999000000  # 999k attack
                elif s['stat'] == 1000003:  # Defense
                    s['change_mb'] = 999000000
                elif s['stat'] == 1000000:  # HP
                    s['change_mb'] = 999000000
                elif s['stat'] == 1000006:  # Crit Damage
                    s['change_mb'] = 999000000
            # Max rate stats
            for s in sd.get('stat_list_static_level', []):
                s['change_mb'] = 15  # max level

    # Serialize back
    final_item_data = crimson_rs.serialize_iteminfo(temp_items)
    print(f'  Stats maxed: Attack=999k, Defense=999k, HP=999k, CritDmg=999k, all rates=15')
    print(f'  Serialized: {len(final_item_data)}B')
else:
    print('  WARNING: crimson_rs could not parse cloned item, using raw clone')
    final_item_data = cloned

# =====================================================================
# 3. APPEND TO VANILLA ITEMINFO
# =====================================================================
print('\n[3] Appending to iteminfo...')
new_body, new_head = append_items_to_iteminfo(body, head, [(NEW_KEY, final_item_data)])
new_count = struct.unpack_from('<H', new_head, 0)[0]
print(f'  Items: {count} -> {new_count}')
print(f'  Body: {len(body):,} -> {len(new_body):,} (+{len(new_body)-len(body)})')

# =====================================================================
# 4. ADD TO HERNANDEL GENERAL STORE
# =====================================================================
print('\n[4] Adding to Hernandel general store...')
store_gb = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'storeinfo.pabgb'))
store_gh = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'storeinfo.pabgh'))

sp = StoreinfoParser()
sp.load_from_bytes(store_gh, store_gb)

# Store_Her_General = key 3101 (Hernandel general store, 36 items)
STORE_KEY = 3101
store = sp.get_store_by_key(STORE_KEY)
if store:
    print(f'  Store: {store.name} ({store.item_count} items)')
    # Use the first item in the store as donor template for the store entry
    donor_store_item = store.items[0].item_key
    success = sp.add_item(STORE_KEY, donor_store_item, NEW_KEY, buy_price=1, sell_price=0)
    if success:
        store = sp.get_store_by_key(STORE_KEY)
        print(f'  Added! New count: {store.item_count}')
        # Verify the item is in the list
        found = any(it.item_key == NEW_KEY for it in store.items)
        print(f'  Verified in store: {found}')
    else:
        print('  FAILED to add item to store')
else:
    print(f'  Store {STORE_KEY} not found!')

new_store_gb = bytes(sp._body_data)
new_store_gh = sp._header_data

# =====================================================================
# 5. DEPLOY ALL OVERLAYS
# =====================================================================
print('\n[5] Deploying overlays...')

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

# Deploy iteminfo to 0058
ck58 = deploy_group('0058', [
    (dp, 'iteminfo.pabgb', new_body),
    (dp, 'iteminfo.pabgh', new_head),
])
print(f'  0058/ deployed (iteminfo with {NEW_NAME} key={NEW_KEY})')

# Deploy storeinfo to 0060
ck60 = deploy_group('0060', [
    (dp, 'storeinfo.pabgb', new_store_gb),
    (dp, 'storeinfo.pabgh', new_store_gh),
])
print(f'  0060/ deployed (Store_Her_General has item {NEW_KEY})')

# Update PAPGT
print('\n[6] Updating PAPGT...')
papgt_path = os.path.join(game_path, 'meta', '0.papgt')
papgt = crimson_rs.parse_papgt_file(papgt_path)
papgt['entries'] = [
    e for e in papgt['entries']
    if e.get('group_name') not in ('0058', '0060')
]
papgt = crimson_rs.add_papgt_entry(papgt, '0058', ck58, 0, 16383)
papgt = crimson_rs.add_papgt_entry(papgt, '0060', ck60, 0, 16383)
crimson_rs.write_papgt_file(papgt, papgt_path)

check = crimson_rs.parse_papgt_file(papgt_path)
mods = sorted(e['group_name'] for e in check['entries'] if int(e['group_name']) >= 33)
print(f'  PAPGT mod groups: {mods}')

print('\n' + '='*60)
print('DEPLOYED:')
print(f'  Item: {NEW_NAME} (key={NEW_KEY})')
print(f'  Stats: Attack=999k, Defense=999k, HP=999k (GOD TIER)')
print(f'  Store: Hernandel General Store (key={STORE_KEY})')
print(f'  Price: 1 silver')
print(f'  Overlays: 0058 (iteminfo), 0060 (storeinfo)')
print()
print('NOTE: Item will show as BLANK NAME in store (no paloc patch).')
print('You will recognize it by the 1-silver price.')
print('Buy it, equip it, check if stats are maxed.')
print('='*60)
