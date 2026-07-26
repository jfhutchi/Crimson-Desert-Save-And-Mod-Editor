#!/usr/bin/env python3
"""
pabgb_auto_decoder.py — Automatic PABGB field layout decoder.

Takes a pabgb + pabgh file pair, analyzes the raw bytes across ALL entries,
and outputs a field-by-field layout guess with confidence scores.

Approach: At each byte position in the payload, try every plausible type
interpretation (u8, u16, u32, i32, f32, string, list, flag+string, etc.),
score each across all entries, pick the best, advance.

Usage:
  # From game PAZ archives
  python pabgb_auto_decoder.py --game "C:/path/to/game" --table skill

  # From extracted files
  python pabgb_auto_decoder.py skill.pabgb skill.pabgh

  # Output JSON schema
  python pabgb_auto_decoder.py skill.pabgb skill.pabgh --json schema.json
"""

import struct
import sys
import os
import json
import math
from dataclasses import dataclass, field
from typing import Optional
from collections import Counter


# ── PABGH Index ──────────────────────────────────────────────────────────

def parse_pabgh(pabgh: bytes) -> list[tuple[int, int]]:
    """Auto-detect pabgh format and return [(key, offset)] sorted by offset."""
    count_u16 = struct.unpack_from('<H', pabgh, 0)[0]

    if count_u16 > 0 and 2 + count_u16 * 6 == len(pabgh):
        entries = []
        for i in range(count_u16):
            k = struct.unpack_from('<H', pabgh, 2 + i * 6)[0]
            o = struct.unpack_from('<I', pabgh, 2 + i * 6 + 2)[0]
            entries.append((k, o))
        return sorted(entries, key=lambda x: x[1])

    if count_u16 > 0 and 2 + count_u16 * 8 <= len(pabgh):
        entries = []
        for i in range(count_u16):
            k = struct.unpack_from('<I', pabgh, 2 + i * 8)[0]
            o = struct.unpack_from('<I', pabgh, 2 + i * 8 + 4)[0]
            entries.append((k, o))
        return sorted(entries, key=lambda x: x[1])

    if count_u16 > 0 and 2 + count_u16 * 5 == len(pabgh):
        entries = []
        for i in range(count_u16):
            k = pabgh[2 + i * 5]
            o = struct.unpack_from('<I', pabgh, 2 + i * 5 + 1)[0]
            entries.append((k, o))
        return sorted(entries, key=lambda x: x[1])

    count_u32 = struct.unpack_from('<I', pabgh, 0)[0]
    if count_u32 > 0 and abs(4 + count_u32 * 8 - len(pabgh)) <= 16:
        entries = []
        for i in range(count_u32):
            k = struct.unpack_from('<I', pabgh, 4 + i * 8)[0]
            o = struct.unpack_from('<I', pabgh, 4 + i * 8 + 4)[0]
            entries.append((k, o))
        return sorted(entries, key=lambda x: x[1])

    raise ValueError(f"Cannot detect pabgh format (size={len(pabgh)})")


# ── Entry Extraction ─────────────────────────────────────────────────────

def extract_entries(pabgb: bytes, pabgh: bytes) -> list[dict]:
    """Extract all entries with their payloads."""
    idx = parse_pabgh(pabgh)
    entries = []
    for i, (key, off) in enumerate(idx):
        end = idx[i + 1][1] if i + 1 < len(idx) else len(pabgb)
        ekey = struct.unpack_from('<I', pabgb, off)[0]
        name_len = struct.unpack_from('<I', pabgb, off + 4)[0]
        if name_len > 500:
            continue
        name = pabgb[off + 8:off + 8 + name_len].decode('ascii', errors='replace')
        header_end = off + 8 + name_len
        has_null = header_end < end and pabgb[header_end] == 0
        if has_null:
            header_end += 1
        payload = pabgb[header_end:end]
        entries.append({
            'key': key,
            'name': name,
            'payload': payload,
            'offset': off,
            'size': end - off,
        })
    return entries


# ── Type Probes ──────────────────────────────────────────────────────────

@dataclass
class FieldGuess:
    offset: int
    type_name: str
    size: int  # bytes consumed from file
    confidence: float  # 0.0 - 1.0
    values: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def summary(self) -> str:
        s = self.stats
        if self.type_name == 'u8':
            return f"u8  values={s.get('unique',0)} unique, range=[{s.get('min',0)},{s.get('max',0)}]"
        elif self.type_name == 'u8_bool':
            return f"u8 (bool)  {s.get('zeros',0)} zeros, {s.get('ones',0)} ones"
        elif self.type_name == 'pad':
            return f"pad[{self.size}]  always zero"
        elif self.type_name in ('u16', 'i16'):
            return f"{self.type_name}  values={s.get('unique',0)} unique, range=[{s.get('min',0)},{s.get('max',0)}]"
        elif self.type_name in ('u32', 'i32'):
            return f"{self.type_name}  values={s.get('unique',0)} unique, range=[{s.get('min',0)},{s.get('max',0)}]"
        elif self.type_name == 'u32_hash':
            return f"u32 (hash)  {s.get('unique',0)} unique values"
        elif self.type_name == 'f32':
            return f"f32  range=[{s.get('min',0):.3f},{s.get('max',0):.3f}]"
        elif self.type_name in ('i64', 'u64'):
            return f"{self.type_name}  range=[{s.get('min',0)},{s.get('max',0)}]"
        elif self.type_name == 'string':
            return f"string (u32 len + bytes)  max_len={s.get('max_len',0)}, {s.get('non_empty',0)}/{s.get('total',0)} non-empty"
        elif self.type_name == 'flag_string':
            return f"flag+string (u8 flag + u32 len + bytes)  {s.get('flagged',0)}/{s.get('total',0)} flagged"
        elif self.type_name == 'list_u32':
            return f"list<u32> (u32 count + N×u32)  max_count={s.get('max_count',0)}"
        elif self.type_name == 'list_u16':
            return f"list<u16> (u32 count + N×u16)  max_count={s.get('max_count',0)}"
        elif self.type_name == 'list_u32_hash':
            return f"list<u32_hash> (u32 count + N×u32)  max_count={s.get('max_count',0)}"
        elif self.type_name == 'struct_28':
            return f"struct[28B] (i64+i64+i64+u32)"
        elif self.type_name == 'variable':
            return f"VARIABLE — entries have different sizes from here"
        return self.type_name


def _is_valid_utf8(data: bytes) -> bool:
    try:
        data.decode('utf-8')
        return True
    except UnicodeDecodeError:
        return False


def _is_printable_ish(data: bytes) -> bool:
    if not data:
        return True
    try:
        text = data.decode('utf-8')
        printable = sum(1 for c in text if c.isprintable() or c in '\n\r\t ')
        return printable / len(text) > 0.8
    except UnicodeDecodeError:
        return False


def probe_u8(payloads: list[bytes], pos: int) -> Optional[FieldGuess]:
    vals = []
    for p in payloads:
        if pos >= len(p):
            return None
        vals.append(p[pos])
    unique = len(set(vals))
    if unique <= 2 and set(vals) <= {0, 1}:
        return FieldGuess(
            offset=pos, type_name='u8_bool', size=1,
            confidence=0.9, values=vals,
            stats={'zeros': vals.count(0), 'ones': vals.count(1)})
    return FieldGuess(
        offset=pos, type_name='u8', size=1,
        confidence=0.3, values=vals,
        stats={'unique': unique, 'min': min(vals), 'max': max(vals)})


def probe_pad(payloads: list[bytes], pos: int, max_pad: int = 8) -> Optional[FieldGuess]:
    for pad_len in range(1, max_pad + 1):
        all_zero = True
        for p in payloads:
            if pos + pad_len > len(p):
                return None
            if any(b != 0 for b in p[pos:pos + pad_len]):
                all_zero = False
                break
        if all_zero and pad_len >= 2:
            return FieldGuess(
                offset=pos, type_name='pad', size=pad_len,
                confidence=0.85, stats={})
    return None


def probe_u16(payloads: list[bytes], pos: int) -> Optional[FieldGuess]:
    vals = []
    for p in payloads:
        if pos + 2 > len(p):
            return None
        vals.append(struct.unpack_from('<H', p, pos)[0])
    unique = len(set(vals))
    return FieldGuess(
        offset=pos, type_name='u16', size=2,
        confidence=0.3, values=vals,
        stats={'unique': unique, 'min': min(vals), 'max': max(vals)})


def probe_u32(payloads: list[bytes], pos: int) -> Optional[FieldGuess]:
    vals = []
    for p in payloads:
        if pos + 4 > len(p):
            return None
        vals.append(struct.unpack_from('<I', p, pos)[0])
    unique = len(set(vals))
    mn, mx = min(vals), max(vals)
    is_hash = unique > len(vals) * 0.3 and mn > 0x10000 and mx > 0x1000000
    if is_hash:
        return FieldGuess(
            offset=pos, type_name='u32_hash', size=4,
            confidence=0.6, values=vals,
            stats={'unique': unique})
    return FieldGuess(
        offset=pos, type_name='u32', size=4,
        confidence=0.4, values=vals,
        stats={'unique': unique, 'min': mn, 'max': mx})


def probe_i32(payloads: list[bytes], pos: int) -> Optional[FieldGuess]:
    vals = []
    for p in payloads:
        if pos + 4 > len(p):
            return None
        vals.append(struct.unpack_from('<i', p, pos)[0])
    has_neg = any(v < 0 for v in vals)
    if not has_neg:
        return None
    unique = len(set(vals))
    return FieldGuess(
        offset=pos, type_name='i32', size=4,
        confidence=0.5, values=vals,
        stats={'unique': unique, 'min': min(vals), 'max': max(vals)})


def probe_f32(payloads: list[bytes], pos: int) -> Optional[FieldGuess]:
    vals = []
    for p in payloads:
        if pos + 4 > len(p):
            return None
        v = struct.unpack_from('<f', p, pos)[0]
        if math.isnan(v) or math.isinf(v) or abs(v) > 1e15:
            return None
        vals.append(v)
    non_zero = sum(1 for v in vals if v != 0.0)
    if non_zero == 0:
        return None
    looks_float = sum(1 for v in vals if v != 0.0 and abs(v) < 10000 and v != int(v)) / max(non_zero, 1)
    if looks_float < 0.3:
        return None
    return FieldGuess(
        offset=pos, type_name='f32', size=4,
        confidence=0.5 + looks_float * 0.3, values=vals,
        stats={'min': min(vals), 'max': max(vals)})


def probe_i64(payloads: list[bytes], pos: int) -> Optional[FieldGuess]:
    vals = []
    for p in payloads:
        if pos + 8 > len(p):
            return None
        vals.append(struct.unpack_from('<q', p, pos)[0])
    unique = len(set(vals))
    return FieldGuess(
        offset=pos, type_name='i64', size=8,
        confidence=0.3, values=vals,
        stats={'unique': unique, 'min': min(vals), 'max': max(vals)})


def probe_string(payloads: list[bytes], pos: int) -> Optional[FieldGuess]:
    """u32 length + bytes[length] — CString format."""
    lengths = []
    valid = 0
    for p in payloads:
        if pos + 4 > len(p):
            return None
        slen = struct.unpack_from('<I', p, pos)[0]
        if slen > 10000:
            return None
        if pos + 4 + slen > len(p):
            return None
        lengths.append(slen)
        if slen == 0:
            valid += 1
        elif _is_valid_utf8(p[pos + 4:pos + 4 + slen]):
            valid += 1

    if valid < len(payloads) * 0.95:
        return None

    max_len = max(lengths)
    non_empty = sum(1 for l in lengths if l > 0)

    if max_len == 0 and not non_empty:
        return None

    # Variable size — all entries must have same position for this to work
    # but the consumed bytes differ per entry
    return FieldGuess(
        offset=pos, type_name='string', size=-1,  # variable
        confidence=0.85, values=lengths,
        stats={'max_len': max_len, 'non_empty': non_empty, 'total': len(payloads)})


def probe_flag_string(payloads: list[bytes], pos: int) -> Optional[FieldGuess]:
    """u8 flag + u32 length + bytes[length] — Potter's pattern."""
    flags = []
    valid = 0
    for p in payloads:
        if pos + 5 > len(p):
            return None
        flag = p[pos]
        if flag > 3:
            return None
        slen = struct.unpack_from('<I', p, pos + 1)[0]
        if slen > 10000:
            return None
        if pos + 5 + slen > len(p):
            return None
        flags.append(flag)
        if slen == 0:
            valid += 1
        elif _is_valid_utf8(p[pos + 5:pos + 5 + slen]):
            valid += 1

    if valid < len(payloads) * 0.9:
        return None

    flagged = sum(1 for f in flags if f != 0)
    return FieldGuess(
        offset=pos, type_name='flag_string', size=-1,
        confidence=0.8, values=flags,
        stats={'flagged': flagged, 'total': len(payloads)})


def probe_list_u32(payloads: list[bytes], pos: int) -> Optional[FieldGuess]:
    """u32 count + count × u32."""
    counts = []
    for p in payloads:
        if pos + 4 > len(p):
            return None
        count = struct.unpack_from('<I', p, pos)[0]
        if count > 1000:
            return None
        if pos + 4 + count * 4 > len(p):
            return None
        counts.append(count)

    if max(counts) == 0 and sum(counts) == 0:
        return None

    return FieldGuess(
        offset=pos, type_name='list_u32', size=-1,
        confidence=0.6, values=counts,
        stats={'max_count': max(counts), 'avg_count': sum(counts) / len(counts)})


def probe_list_u16(payloads: list[bytes], pos: int) -> Optional[FieldGuess]:
    """u32 count + count × u16."""
    counts = []
    for p in payloads:
        if pos + 4 > len(p):
            return None
        count = struct.unpack_from('<I', p, pos)[0]
        if count > 1000:
            return None
        if pos + 4 + count * 2 > len(p):
            return None
        counts.append(count)

    if max(counts) == 0 and sum(counts) == 0:
        return None

    return FieldGuess(
        offset=pos, type_name='list_u16', size=-1,
        confidence=0.5, values=counts,
        stats={'max_count': max(counts), 'avg_count': sum(counts) / len(counts)})


def probe_struct_28(payloads: list[bytes], pos: int) -> Optional[FieldGuess]:
    """Fixed 28-byte struct: i64 + i64 + i64 + u32."""
    for p in payloads:
        if pos + 28 > len(p):
            return None
    # Check if the u32 at +24 looks like a small int (typical for this struct)
    vals = []
    for p in payloads:
        v = struct.unpack_from('<I', p, pos + 24)[0]
        vals.append(v)
    if max(vals) > 100000:
        return None
    return FieldGuess(
        offset=pos, type_name='struct_28', size=28,
        confidence=0.4, stats={})


# ── Main Decoder ─────────────────────────────────────────────────────────

def compute_consumed(guess: FieldGuess, payloads: list[bytes], pos: int) -> list[int]:
    """Compute how many bytes this guess consumes per entry."""
    if guess.size > 0:
        return [guess.size] * len(payloads)

    consumed = []
    for p in payloads:
        if guess.type_name == 'string':
            slen = struct.unpack_from('<I', p, pos)[0]
            consumed.append(4 + slen)
        elif guess.type_name == 'flag_string':
            slen = struct.unpack_from('<I', p, pos + 1)[0]
            consumed.append(1 + 4 + slen)
        elif guess.type_name == 'list_u32' or guess.type_name == 'list_u32_hash':
            count = struct.unpack_from('<I', p, pos)[0]
            consumed.append(4 + count * 4)
        elif guess.type_name == 'list_u16':
            count = struct.unpack_from('<I', p, pos)[0]
            consumed.append(4 + count * 2)
        else:
            consumed.append(0)
    return consumed


def decode_fixed_region(payloads: list[bytes], start: int = 0,
                        end: Optional[int] = None,
                        max_fields: int = 200) -> list[FieldGuess]:
    """Decode the fixed-offset region of payloads.

    Stops when entries have variable sizes (due to strings/lists) or
    when we reach end.
    """
    if not payloads:
        return []

    min_len = min(len(p) for p in payloads)
    if end is None:
        end = min_len

    fields = []
    pos = start
    field_count = 0

    while pos < end and field_count < max_fields:
        remaining = end - pos
        if remaining <= 0:
            break

        # Try all probes, pick best confidence
        candidates = []

        # Pad (check first — high confidence if all zeros)
        pad = probe_pad(payloads, pos)
        if pad:
            candidates.append(pad)

        # u8 / bool
        u8 = probe_u8(payloads, pos)
        if u8:
            candidates.append(u8)

        if remaining >= 2:
            u16 = probe_u16(payloads, pos)
            if u16:
                candidates.append(u16)

        if remaining >= 4:
            u32 = probe_u32(payloads, pos)
            if u32:
                candidates.append(u32)
            i32 = probe_i32(payloads, pos)
            if i32:
                candidates.append(i32)
            f32 = probe_f32(payloads, pos)
            if f32:
                candidates.append(f32)

            # String
            string = probe_string(payloads, pos)
            if string:
                candidates.append(string)

            # Lists
            list_u32 = probe_list_u32(payloads, pos)
            if list_u32:
                candidates.append(list_u32)
            list_u16 = probe_list_u16(payloads, pos)
            if list_u16:
                candidates.append(list_u16)

        if remaining >= 5:
            flag_str = probe_flag_string(payloads, pos)
            if flag_str:
                candidates.append(flag_str)

        if remaining >= 8:
            i64 = probe_i64(payloads, pos)
            if i64:
                candidates.append(i64)

        if remaining >= 28:
            s28 = probe_struct_28(payloads, pos)
            if s28:
                candidates.append(s28)

        if not candidates:
            # Can't decode — emit raw byte
            fields.append(FieldGuess(
                offset=pos, type_name='u8_unknown', size=1,
                confidence=0.0, stats={}))
            pos += 1
            field_count += 1
            continue

        # Sort by confidence descending, prefer larger types on tie
        candidates.sort(key=lambda g: (g.confidence, g.size if g.size > 0 else 100), reverse=True)
        best = candidates[0]

        # For variable-size types, check if all entries consume the same bytes
        if best.size < 0:
            consumed = compute_consumed(best, payloads, pos)
            if len(set(consumed)) == 1:
                best.size = consumed[0]
                best.stats['fixed_consumed'] = consumed[0]
            else:
                best.stats['min_consumed'] = min(consumed)
                best.stats['max_consumed'] = max(consumed)
                fields.append(best)
                field_count += 1
                # Variable size — can't continue fixed decoding
                # Return what we have; caller handles the rest
                return fields

        fields.append(best)
        pos += best.size
        field_count += 1

    return fields


def decode_full(payloads: list[bytes], max_fields: int = 200) -> list[FieldGuess]:
    """Decode payloads with variable-size field support.

    After hitting a variable-size field, advance each payload by its
    consumed amount and continue decoding from the new position.
    """
    if not payloads:
        return []

    fields = []
    positions = [0] * len(payloads)
    field_count = 0

    while field_count < max_fields:
        # Check if all payloads have data remaining
        remaining = [len(p) - positions[i] for i, p in enumerate(payloads)]
        if min(remaining) <= 0:
            break

        # Slice payloads from current positions
        slices = [p[positions[i]:] for i, p in enumerate(payloads)]
        min_remaining = min(len(s) for s in slices)
        if min_remaining <= 0:
            break

        # Try all probes at position 0 of the slices
        candidates = []

        pad = probe_pad(slices, 0)
        if pad:
            candidates.append(pad)

        u8 = probe_u8(slices, 0)
        if u8:
            candidates.append(u8)

        if min_remaining >= 2:
            u16 = probe_u16(slices, 0)
            if u16:
                candidates.append(u16)

        if min_remaining >= 4:
            u32 = probe_u32(slices, 0)
            if u32:
                candidates.append(u32)
            i32 = probe_i32(slices, 0)
            if i32:
                candidates.append(i32)
            f32 = probe_f32(slices, 0)
            if f32:
                candidates.append(f32)
            string = probe_string(slices, 0)
            if string:
                candidates.append(string)
            list_u32 = probe_list_u32(slices, 0)
            if list_u32:
                candidates.append(list_u32)
            list_u16 = probe_list_u16(slices, 0)
            if list_u16:
                candidates.append(list_u16)

        if min_remaining >= 5:
            flag_str = probe_flag_string(slices, 0)
            if flag_str:
                candidates.append(flag_str)

        if min_remaining >= 8:
            i64 = probe_i64(slices, 0)
            if i64:
                candidates.append(i64)

        if min_remaining >= 28:
            s28 = probe_struct_28(slices, 0)
            if s28:
                candidates.append(s28)

        if not candidates:
            fields.append(FieldGuess(
                offset=positions[0], type_name='u8_unknown', size=1,
                confidence=0.0, stats={}))
            for i in range(len(positions)):
                positions[i] += 1
            field_count += 1
            continue

        candidates.sort(key=lambda g: (g.confidence, g.size if g.size > 0 else 100), reverse=True)
        best = candidates[0]
        best.offset = positions[0]

        if best.size > 0:
            for i in range(len(positions)):
                positions[i] += best.size
        else:
            consumed = compute_consumed(best, slices, 0)
            for i in range(len(positions)):
                positions[i] += consumed[i]
            best.stats['min_consumed'] = min(consumed)
            best.stats['max_consumed'] = max(consumed)

        fields.append(best)
        field_count += 1

    return fields


# ── Output ───────────────────────────────────────────────────────────────

def print_layout(fields: list[FieldGuess], table_name: str = "unknown"):
    print(f"\n{'='*70}")
    print(f"  Auto-decoded layout for {table_name}")
    print(f"  {len(fields)} fields detected")
    print(f"{'='*70}\n")

    for i, f in enumerate(fields):
        conf = f"{'*' * int(f.confidence * 5):5s}"
        off = f"+0x{f.offset:04X}" if f.offset >= 0 else "  var"
        sz = f"{f.size:3d}B" if f.size > 0 else "var "
        print(f"  [{i:3d}] {off}  {sz}  {conf}  {f.summary()}")

    print(f"\n{'='*70}")
    print(f"  Confidence: * = 20%, ** = 40%, *** = 60%, **** = 80%, ***** = 100%")
    print(f"{'='*70}\n")


def to_json(fields: list[FieldGuess], table_name: str = "unknown") -> dict:
    return {
        'table': table_name,
        'field_count': len(fields),
        'fields': [
            {
                'index': i,
                'offset': f.offset,
                'type': f.type_name,
                'size': f.size,
                'confidence': round(f.confidence, 2),
                'summary': f.summary(),
                'stats': {k: v for k, v in f.stats.items()
                          if not isinstance(v, (list, bytes))},
            }
            for i, f in enumerate(fields)
        ],
    }


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    import argparse
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser(
        description="Auto-decode PABGB field layout by statistical analysis")
    ap.add_argument("pabgb", nargs="?", help="Path to .pabgb file")
    ap.add_argument("pabgh", nargs="?", help="Path to .pabgh file")
    ap.add_argument("--game", help="Game directory (extract from PAZ)")
    ap.add_argument("--table", help="Table name (with --game)")
    ap.add_argument("--json", "-j", help="Output JSON schema to file")
    ap.add_argument("--sample", "-s", type=int, default=500,
                    help="Max entries to sample (default: 500)")
    ap.add_argument("--full", action="store_true",
                    help="Decode full payload (handles variable-size fields)")
    args = ap.parse_args()

    if args.game and args.table:
        from crimson.game_mods import crimson_rs as crimson_rs
        pabgb = bytes(crimson_rs.extract_file(
            args.game, '0008', 'gamedata/binary__/client/bin',
            f'{args.table}.pabgb'))
        pabgh = bytes(crimson_rs.extract_file(
            args.game, '0008', 'gamedata/binary__/client/bin',
            f'{args.table}.pabgh'))
        table_name = args.table
    elif args.pabgb and args.pabgh:
        pabgb = open(args.pabgb, 'rb').read()
        pabgh = open(args.pabgh, 'rb').read()
        table_name = os.path.splitext(os.path.basename(args.pabgb))[0]
    else:
        ap.error("Provide pabgb + pabgh files, or --game + --table")
        return

    print(f"Loading {table_name}...")
    print(f"  pabgb: {len(pabgb):,} bytes")
    print(f"  pabgh: {len(pabgh):,} bytes")

    entries = extract_entries(pabgb, pabgh)
    print(f"  Entries: {len(entries)}")

    sizes = [len(e['payload']) for e in entries]
    print(f"  Payload sizes: min={min(sizes)}, max={max(sizes)}, "
          f"median={sorted(sizes)[len(sizes)//2]}")

    # Sample entries for analysis
    sample = entries[:args.sample] if len(entries) > args.sample else entries
    payloads = [e['payload'] for e in sample]
    print(f"  Sampling {len(sample)} entries for analysis...")

    if args.full:
        fields = decode_full(payloads)
    else:
        fields = decode_fixed_region(payloads)

    print_layout(fields, table_name)

    if args.json:
        schema = to_json(fields, table_name)
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(schema, f, indent=2, ensure_ascii=False)
        print(f"Schema written to {args.json}")


if __name__ == "__main__":
    main()
