"""Pipeline test v2: swap an existing store item with a maxed-stats version.
No new keys, no paloc needed — just modify the item's stats and put it in a store."""
import sys, os, struct, shutil, tempfile
sys.path.insert(0, os.path.dirname(__file__))
from crimson.game_mods import crimson_rs as crimson_rs
from crimson.game_mods.storeinfo_parser import StoreinfoParser

game_path = r'C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert'
dp = 'gamedata/binary__/client/bin'

print('='*60)
print('STORE ITEM SWAP TEST')
print('='*60)

# =====================================================================
# 1. Load iteminfo, find Leinstead_OneHandSword, max its stats
# =====================================================================
print('\n[1] Loading iteminfo and maxing stats on Leinstead_OneHandSword...')
body = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'iteminfo.pabgb'))
items = crimson_rs.parse_iteminfo_from_bytes(body)

TARGET_KEY = 200021  # Leinstead_OneHandSword
target = None
for it in items:
    if it.get('key') == TARGET_KEY:
        target = it
        break

if not target:
    print(f'ERROR: item {TARGET_KEY} not found')
    sys.exit(1)

print(f'  Found: key={TARGET_KEY}')

# Show original stats
edl = target.get('enchant_data_list', [])
if edl:
    sd = edl[0].get('enchant_stat_data', {})
    print('  Original stats:')
    for s in sd.get('stat_list_static', []):
        print(f'    stat={s["stat"]} value={s["change_mb"]}')

# Max out ALL enchant levels
for ed in edl:
    sd = ed.get('enchant_stat_data', {})
    for s in sd.get('stat_list_static', []):
        if s['stat'] == 1000002:  # Attack
            s['change_mb'] = 999000000
        elif s['stat'] == 1000003:  # Defense
            s['change_mb'] = 999000000
        elif s['stat'] == 1000000:  # HP
            s['change_mb'] = 999000000
        elif s['stat'] == 1000006:  # Crit Damage
            s['change_mb'] = 999000000
    for s in sd.get('stat_list_static_level', []):
        s['change_mb'] = 15
    for s in sd.get('regen_stat_list', []):
        s['change_mb'] = 999000000

print('  Stats maxed: Attack/Defense/HP/CritDmg=999k, all rates=15')

# Serialize
new_iteminfo = crimson_rs.serialize_iteminfo(items)
print(f'  Serialized: {len(new_iteminfo):,}B')

# =====================================================================
# 2. Add Leinstead_OneHandSword to Store_Her_Equipment
# =====================================================================
print('\n[2] Adding sword to Hernandel Equipment Store...')
store_gb = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'storeinfo.pabgb'))
store_gh = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'storeinfo.pabgh'))

sp = StoreinfoParser()
sp.load_from_bytes(store_gh, store_gb)

# Store_Her_Equipment = key 1
STORE_KEY = 1
store = sp.get_store_by_key(STORE_KEY)
print(f'  Store: {store.name} ({store.item_count} items)')

# Check if sword already in store
already = any(it.item_key == TARGET_KEY for it in store.items)
if already:
    print(f'  Item {TARGET_KEY} already in store!')
else:
    # Use first item as donor template for store entry format
    donor_store_item = store.items[0].item_key
    success = sp.add_item(STORE_KEY, donor_store_item, TARGET_KEY, buy_price=1, sell_price=0)
    if success:
        store = sp.get_store_by_key(STORE_KEY)
        print(f'  Added! Store now has {store.item_count} items')
    else:
        print('  FAILED')

new_store_gb = bytes(sp._body_data)
new_store_gh = sp._header_data

# =====================================================================
# 3. Deploy overlays
# =====================================================================
print('\n[3] Deploying...')

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

ck58 = deploy_group('0058', [(dp, 'iteminfo.pabgb', new_iteminfo)])
print(f'  0058/ deployed (iteminfo with maxed Leinstead)')

ck60 = deploy_group('0060', [
    (dp, 'storeinfo.pabgb', new_store_gb),
    (dp, 'storeinfo.pabgh', new_store_gh),
])
print(f'  0060/ deployed (Store_Her_Equipment has Leinstead)')

papgt_path = os.path.join(game_path, 'meta', '0.papgt')
papgt = crimson_rs.parse_papgt_file(papgt_path)
papgt['entries'] = [
    e for e in papgt['entries']
    if e.get('group_name') not in ('0058', '0060')
]
papgt = crimson_rs.add_papgt_entry(papgt, '0058', ck58, 0, 16383)
papgt = crimson_rs.add_papgt_entry(papgt, '0060', ck60, 0, 16383)
crimson_rs.write_papgt_file(papgt, papgt_path)

mods = sorted(e['group_name'] for e in crimson_rs.parse_papgt_file(papgt_path)['entries']
              if int(e['group_name']) >= 33)
print(f'  PAPGT: {mods}')

print('\n' + '='*60)
print('TEST DEPLOYED:')
print(f'  Item: Leinstead OneHandSword (key={TARGET_KEY})')
print(f'  Stats: MAXED (Attack=999k, Defense=999k, HP=999k)')
print(f'  Store: Hernandel Equipment Store (sells arrows, bullets, ore)')
print(f'  Price: 1 silver')
print()
print('  Go to Hernandel -> Equipment Store vendor')
print('  Look for "Leinstead One-Hand Sword" at 1 silver')
print('  Buy it, equip it, check stats')
print('='*60)
