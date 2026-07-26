
import os
import struct
import fnmatch
from dataclasses import dataclass


@dataclass
class PazEntry:
    path: str
    paz_file: str
    offset: int
    comp_size: int
    orig_size: int
    flags: int
    paz_index: int

    @property
    def compressed(self) -> bool:
        return self.comp_size != self.orig_size

    @property
    def compression_type(self) -> int:
        return (self.flags >> 16) & 0x0F

    @property
    def encrypted(self) -> bool:
        return self.path.lower().endswith('.xml')


def parse_pamt(pamt_path: str, paz_dir: str = None) -> list[PazEntry]:
    with open(pamt_path, 'rb') as f:
        data = f.read()

    if paz_dir is None:
        paz_dir = os.path.dirname(pamt_path) or '.'

    pamt_stem = os.path.splitext(os.path.basename(pamt_path))[0]

    off = 0
    off += 4

    paz_count = struct.unpack_from('<I', data, off)[0]; off += 4
    off += 8

    for i in range(paz_count):
        off += 4
        off += 4
        if i < paz_count - 1:
            off += 4

    folder_size = struct.unpack_from('<I', data, off)[0]; off += 4
    folder_end = off + folder_size
    folder_prefix = ""
    while off < folder_end:
        parent = struct.unpack_from('<I', data, off)[0]
        slen = data[off + 4]
        name = data[off + 5:off + 5 + slen].decode('utf-8', errors='replace')
        if parent == 0xFFFFFFFF:
            folder_prefix = name
        off += 5 + slen

    node_size = struct.unpack_from('<I', data, off)[0]; off += 4
    node_start = off
    nodes = {}
    while off < node_start + node_size:
        rel = off - node_start
        parent = struct.unpack_from('<I', data, off)[0]
        slen = data[off + 4]
        name = data[off + 5:off + 5 + slen].decode('utf-8', errors='replace')
        nodes[rel] = (parent, name)
        off += 5 + slen

    def build_path(node_ref):
        parts = []
        cur = node_ref
        while cur != 0xFFFFFFFF and len(parts) < 64:
            if cur not in nodes:
                break
            p, n = nodes[cur]
            parts.append(n)
            cur = p
        return ''.join(reversed(parts))

    folder_count = struct.unpack_from('<I', data, off)[0]; off += 4
    off += 4
    off += folder_count * 16

    entries = []
    while off + 20 <= len(data):
        node_ref, paz_offset, comp_size, orig_size, flags = \
            struct.unpack_from('<IIIII', data, off)
        off += 20

        paz_index = flags & 0xFF
        node_path = build_path(node_ref)
        full_path = f"{folder_prefix}/{node_path}" if folder_prefix else node_path

        paz_num = int(pamt_stem) + paz_index
        paz_file = os.path.join(paz_dir, f"{paz_num}.paz")

        entries.append(PazEntry(
            path=full_path,
            paz_file=paz_file,
            offset=paz_offset,
            comp_size=comp_size,
            orig_size=orig_size,
            flags=flags,
            paz_index=paz_index,
        ))

    return entries


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Parse PAMT index and list PAZ archive contents")
    parser.add_argument("pamt", help="Path to .pamt file")
    parser.add_argument("--paz-dir", help="Directory containing .paz files (default: same as .pamt)")
    parser.add_argument("--filter", help="Filter entries by glob pattern (e.g. '*.xml', '*renderconfig*')")
    parser.add_argument("--stats", action="store_true", help="Show summary statistics")
    args = parser.parse_args()

    entries = parse_pamt(args.pamt, paz_dir=args.paz_dir)

    if args.filter:
        pattern = args.filter.lower()
        entries = [e for e in entries if fnmatch.fnmatch(e.path.lower(), f"*{pattern}*")
                   or fnmatch.fnmatch(os.path.basename(e.path).lower(), pattern)]

    if args.stats:
        compressed = sum(1 for e in entries if e.compressed)
        encrypted = sum(1 for e in entries if e.encrypted)
        total_comp = sum(e.comp_size for e in entries)
        total_orig = sum(e.orig_size for e in entries)
        print(f"Entries:     {len(entries):,}")
        print(f"Compressed:  {compressed:,}")
        print(f"Encrypted:   {encrypted:,} (XML files)")
        print(f"Total stored: {total_comp:,} bytes ({total_comp / 1024 / 1024:.1f} MB)")
        print(f"Total orig:   {total_orig:,} bytes ({total_orig / 1024 / 1024:.1f} MB)")
        return

    for e in entries:
        comp = "LZ4" if e.compression_type == 2 else "   "
        enc = "ENC" if e.encrypted else "   "
        print(f"[{comp}] [{enc}] {e.comp_size:>10,} -> {e.orig_size:>10,}  "
              f"paz:{e.paz_index} @0x{e.offset:08X}  {e.path}")

    print(f"\n{len(entries):,} entries")


if __name__ == "__main__":
    main()
