import struct

old = open('C:/Users/Coding/CrimsonDesertModding/extractedpaz/0008_full/iteminfo.pabgb', 'rb').read()
new = open('_new_iteminfo.pabgb', 'rb').read()

# We know the first item in both files starts at offset 0.
# Old item 1 ends at 0x247 (from Rust test).
# We need to find every byte insertion in the new format.

# Strategy: sliding alignment. Walk old and new in parallel.
# When they match, advance both. When they differ, the new file
# has an insertion - find where old data resumes in new.

old_start = 0
old_end = 0x247  # first item end in old file
new_start = 0

oi = old_start
ni = new_start
insertions = []

while oi < old_end:
    if old[oi] == new[ni]:
        oi += 1
        ni += 1
    else:
        # Found a divergence. Scan ahead in new to find where old data resumes.
        # Look for a match of at least 8 consecutive bytes
        match_len_required = 8
        found = False
        for skip in range(1, 64):
            match = True
            for k in range(match_len_required):
                if oi + k >= old_end or ni + skip + k >= len(new):
                    match = False
                    break
                if old[oi + k] != new[ni + skip + k]:
                    match = False
                    break
            if match:
                inserted_bytes = new[ni:ni+skip]
                insertions.append({
                    'old_offset': oi,
                    'new_offset': ni,
                    'length': skip,
                    'bytes': inserted_bytes.hex(),
                    'values': []
                })
                # Decode the inserted bytes
                ins = insertions[-1]
                if skip == 1:
                    ins['values'].append('u8=%d' % inserted_bytes[0])
                elif skip == 2:
                    ins['values'].append('u16=%d (0x%04X)' % (
                        struct.unpack_from('<H', inserted_bytes, 0)[0],
                        struct.unpack_from('<H', inserted_bytes, 0)[0]))
                elif skip == 4:
                    ins['values'].append('u32=%d (0x%08X)' % (
                        struct.unpack_from('<I', inserted_bytes, 0)[0],
                        struct.unpack_from('<I', inserted_bytes, 0)[0]))
                elif skip == 6:
                    ins['values'].append('u32=%d + u16=%d (0x%04X)' % (
                        struct.unpack_from('<I', inserted_bytes, 0)[0],
                        struct.unpack_from('<H', inserted_bytes, 4)[0],
                        struct.unpack_from('<H', inserted_bytes, 4)[0]))
                elif skip == 8:
                    ins['values'].append('u64=%d' % struct.unpack_from('<Q', inserted_bytes, 0)[0])
                else:
                    # Try multiple interpretations
                    ins['values'].append('raw=%d bytes' % skip)

                ni += skip
                found = True
                break

        if not found:
            # Maybe old data was CHANGED (not just inserted)
            # Check if it's a value change
            print("Could not find realignment at old=0x%X new=0x%X" % (oi, ni))
            print("  old bytes: %s" % old[oi:oi+16].hex())
            print("  new bytes: %s" % new[ni:ni+16].hex())
            # Try treating as a value change + advance both
            oi += 1
            ni += 1

print("=== Insertions found in first item ===")
for ins in insertions:
    print("  at old=0x%X new=0x%X: +%d bytes [%s] %s" % (
        ins['old_offset'], ins['new_offset'], ins['length'],
        ins['bytes'], ', '.join(ins['values'])))

total_new = sum(i['length'] for i in insertions)
print("\nTotal new bytes: %d" % total_new)
print("Expected new item end: 0x%X (old 0x247 + %d = 0x%X)" % (
    0x247 + total_new, total_new, 0x247 + total_new))

# Verify: check that old[0x247] == next item key, and new[0x247+total_new] == same key
old_key2 = struct.unpack_from('<I', old, 0x247)[0]
new_key2 = struct.unpack_from('<I', new, 0x247 + total_new)[0]
print("Old item 2 key: %d" % old_key2)
print("New item 2 key: %d" % new_key2)
if old_key2 == new_key2:
    print("MATCH! Alignment confirmed.")
else:
    print("MISMATCH! Need to check further.")
