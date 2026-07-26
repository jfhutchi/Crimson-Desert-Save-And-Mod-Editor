
import struct
import os
import logging

log = logging.getLogger(__name__)

DMM_TABLE_NAME = 'wanted_info'


def parse_all_dmm(pabgb: bytes, pabgh: bytes):
    try:
        from crimson.game_mods import dmm_parser as dmm_parser
        return dmm_parser.parse_table(DMM_TABLE_NAME, pabgb, pabgh)
    except Exception:
        return None


def serialize_all_dmm(items: list) -> bytes | None:
    try:
        from crimson.game_mods import dmm_parser as dmm_parser
        return bytes(dmm_parser.serialize_table(DMM_TABLE_NAME, items))
    except Exception:
        return None

FACTION_NAMES = {1: 'Kweiden', 2: 'Hernand', 3: 'Calphade', 4: 'Delesyia', 5: 'Demeniss'}
CRIME_TIERS = {0: 'Petty', 1: 'Minor', 2: 'Moderate', 3: 'Serious', 4: 'Major', 5: 'Severe', 6: 'Capital'}


def parse_pabgh_index(pabgh_data):
    count = struct.unpack_from('<H', pabgh_data, 0)[0]
    entries = {}
    pos = 2
    for _ in range(count):
        key = struct.unpack_from('<H', pabgh_data, pos)[0]
        offset = struct.unpack_from('<I', pabgh_data, pos + 2)[0]
        entries[key] = offset
        pos += 6
    return entries


def parse_entry(data, offset, end):
    p = offset
    result = {}

    try:
        result['_key'] = struct.unpack_from('<H', data, p)[0]
        p += 2

        slen = struct.unpack_from('<I', data, p)[0]
        p += 4
        if slen > 10000:
            return None
        result['_stringKey'] = data[p:p + slen].decode('utf-8', errors='replace')
        p += slen

        result['_isBlocked'] = data[p]
        result['_isBlocked_offset'] = p
        p += 1

        result['_increasePrice'] = struct.unpack_from('<Q', data, p)[0]
        result['_increasePrice_offset'] = p
        p += 8

        result['_useTargetPrice'] = data[p]
        p += 1

        key = result['_key']
        result['_faction'] = (key >> 8) & 0xFF
        result['_crimeTier'] = key & 0xFF
        result['_factionName'] = FACTION_NAMES.get(result['_faction'], f'Faction_{result["_faction"]}')
        result['_crimeTierName'] = CRIME_TIERS.get(result['_crimeTier'], f'Tier_{result["_crimeTier"]}')

        result['_parsed_bytes'] = p - offset
        result['_entry_size'] = end - offset

    except (struct.error, IndexError) as e:
        log.debug("Parse error at offset %d: %s", p, e)
        return None

    return result


def parse_all_entries(pabgb_data, pabgh_data):
    idx = parse_pabgh_index(pabgh_data)
    sorted_entries = sorted(idx.items(), key=lambda x: x[1])

    results = []
    for i, (key, eoff) in enumerate(sorted_entries):
        if i + 1 < len(sorted_entries):
            end = sorted_entries[i + 1][1]
        else:
            end = len(pabgb_data)
        r = parse_entry(pabgb_data, eoff, end)
        if r:
            results.append(r)

    return results


def main():
    base = os.environ.get('EXTRACTED_PAZ', 'C:/Users/Coding/CrimsonDesertModding/extractedpaz/0008_full')
    with open(os.path.join(base, 'wantedinfo.pabgb'), 'rb') as f:
        pabgb = f.read()
    with open(os.path.join(base, 'wantedinfo.pabgh'), 'rb') as f:
        pabgh = f.read()

    entries = parse_all_entries(pabgb, pabgh)
    print(f"Parsed {len(entries)} / {struct.unpack_from('<H', pabgh, 0)[0]} entries")
    print(f"\n{'Key':>6s}  {'Faction':<12s} {'Crime Tier':<12s} {'Blocked':>7s} {'Price':>8s} {'UseTarget':>9s}")
    print("-" * 60)
    for e in sorted(entries, key=lambda x: (x['_faction'], x['_crimeTier'])):
        print(f"0x{e['_key']:04X}  {e['_factionName']:<12s} {e['_crimeTierName']:<12s} {e['_isBlocked']:>7d} {e['_increasePrice']:>8d} {e['_useTargetPrice']:>9d}")


if __name__ == '__main__':
    main()
