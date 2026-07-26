"""Deploy SkillTree + Universal Proficiency overlays."""
import sys, os, shutil, tempfile, struct
sys.path.insert(0, os.path.dirname(__file__))
from crimson.game_mods import crimson_rs as crimson_rs
from crimson.game_mods import equipslotinfo_parser as esp
from crimson.game_mods.skilltreeinfo_parser import parse_all, serialize_all, parse_groups, serialize_groups

game_path = r'C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert'
dp = 'gamedata/binary__/client/bin'

def deploy_group(group_id, builder_fn):
    with tempfile.TemporaryDirectory() as tmp:
        gdir = os.path.join(tmp, group_id)
        os.makedirs(gdir)
        b = crimson_rs.PackGroupBuilder(gdir, crimson_rs.Compression.NONE, crimson_rs.Crypto.NONE)
        builder_fn(b)
        pamt_bytes = bytes(b.finish())
        ck = crimson_rs.parse_pamt_bytes(pamt_bytes)['checksum']
        dest = os.path.join(game_path, group_id)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(dest)
        shutil.copy2(os.path.join(gdir, '0.paz'), os.path.join(dest, '0.paz'))
        shutil.copy2(os.path.join(gdir, '0.pamt'), os.path.join(dest, '0.pamt'))
    return ck

# === 1. SKILL TREE (0063) ===
print('=== SkillTree overlay (0063) ===')
st_gb = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'skilltreeinfo.pabgb'))
st_gh = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'skilltreeinfo.pabgh'))
grp_gb = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'skilltreegroupinfo.pabgb'))
grp_gh = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'skilltreegroupinfo.pabgh'))

records = parse_all(st_gh, st_gb)
groups = parse_groups(grp_gh, grp_gb)

for rec in records:
    if rec.key in (22, 52):
        t = bytearray(rec.tail_data)
        struct.pack_into('<H', t, 1, 1)
        rec.tail_data = bytes(t)
        print(f'  _characterInfo: {rec.display_name} -> Kliff(1)')

for grp in groups:
    if grp.key == 1000007:
        if 22 not in grp.tree_keys:
            grp.tree_keys.append(22)
        print(f'  Weapon: {grp.tree_keys}')
    elif grp.key == 1000000:
        if 52 not in grp.tree_keys:
            grp.tree_keys.append(52)
        print(f'  Main: {grp.tree_keys}')

new_st_gh, new_st_gb = serialize_all(records)
new_grp_gh, new_grp_gb = serialize_groups(groups)

def build_63(b):
    b.add_file(dp, 'skilltreeinfo.pabgb', new_st_gb)
    b.add_file(dp, 'skilltreeinfo.pabgh', new_st_gh)
    b.add_file(dp, 'skilltreegroupinfo.pabgb', new_grp_gb)
    b.add_file(dp, 'skilltreegroupinfo.pabgh', new_grp_gh)

ck63 = deploy_group('0063', build_63)
print('  0063/ deployed')

# === 2. EQUIPSLOTINFO (0059) ===
print('\n=== EquipSlot overlay (0059) ===')
es_gh = crimson_rs.extract_file(game_path, '0008', dp, 'equipslotinfo.pabgh')
es_gb = crimson_rs.extract_file(game_path, '0008', dp, 'equipslotinfo.pabgb')
es_records = esp.parse_all(es_gh, es_gb)

PLAYER_KEYS = {1, 4, 6}
cat_hashes = {}
for rec in [r for r in es_records if r.key in PLAYER_KEYS]:
    for e in rec.entries:
        cat_hashes.setdefault((e.category_a, e.category_b), set()).update(e.etl_hashes)

total_slot = 0
for rec in es_records:
    if rec.key not in PLAYER_KEYS:
        continue
    for e in rec.entries:
        pool = cat_hashes.get((e.category_a, e.category_b), set())
        to_add = sorted(pool - set(e.etl_hashes))
        if to_add:
            e.etl_hashes.extend(to_add)
            total_slot += len(to_add)

new_es_gh, new_es_gb = esp.serialize_all(es_records)
print(f'  +{total_slot} equip hashes')

def build_59(b):
    b.add_file(dp, 'equipslotinfo.pabgb', new_es_gb)
    b.add_file(dp, 'equipslotinfo.pabgh', new_es_gh)

ck59 = deploy_group('0059', build_59)
print('  0059/ deployed')

# === 3. ITEMINFO (0058) ===
print('\n=== ItemInfo overlay (0058) ===')
ii_raw = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'iteminfo.pabgb'))
ii_items = crimson_rs.parse_iteminfo_from_bytes(ii_raw)
print(f'  {len(ii_items)} items loaded')

PLAYER_TRIBES = {
    0x96D07B6B, 0xD72F6F9E, 0xD56F5A11, 0xEFE62E3D,
    0x4AB39D48, 0x2764C03A, 0x0D5B93F3, 0x8AD5BE6E,
    0x3C4E7F88, 0x7C72DAF5, 0xCC78A23C, 0xAA741E08,
}

tg_count = 0
for it in ii_items:
    if not it.get('equip_type_info'):
        continue
    for pd in (it.get('prefab_data_list') or []):
        tg = pd.get('tribe_gender_list')
        if not tg:
            continue
        existing = set(tg)
        to_add = sorted(PLAYER_TRIBES - existing)
        if to_add:
            pd['tribe_gender_list'] = list(tg) + to_add
            tg_count += 1

final_ii = crimson_rs.serialize_iteminfo(ii_items)
print(f'  Tribe union: {tg_count} items, serialized {len(final_ii)} bytes')

def build_58(b):
    b.add_file(dp, 'iteminfo.pabgb', final_ii)

ck58 = deploy_group('0058', build_58)
print('  0058/ deployed')

# === 4. PAPGT ===
print('\n=== PAPGT ===')
papgt_path = os.path.join(game_path, 'meta', '0.papgt')
papgt = crimson_rs.parse_papgt_file(papgt_path)
papgt['entries'] = [
    e for e in papgt['entries']
    if e.get('group_name') not in ('0058', '0059', '0063')
]
papgt = crimson_rs.add_papgt_entry(papgt, '0058', ck58, 0, 16383)
papgt = crimson_rs.add_papgt_entry(papgt, '0059', ck59, 0, 16383)
papgt = crimson_rs.add_papgt_entry(papgt, '0063', ck63, 0, 16383)
crimson_rs.write_papgt_file(papgt, papgt_path)

check = crimson_rs.parse_papgt_file(papgt_path)
mods = sorted(e['group_name'] for e in check['entries'] if int(e['group_name']) >= 33)
print(f'  Mod groups: {mods}')

print('\n' + '='*60)
print('ALL DEPLOYED:')
print(f'  0058/ ItemInfo: UP tribe union ({tg_count} items)')
print(f'  0059/ EquipSlot: UP slot expansion (+{total_slot} hashes)')
print(f'  0063/ SkillTree: Kliff keeps all + Pistol(22) + Damiane main(52)')
print('Restart game to test.')
print('='*60)
