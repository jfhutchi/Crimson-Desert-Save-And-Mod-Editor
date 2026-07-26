
import json
import logging
import os
import struct
import sys
from crimson.game_mods.data_db import get_connection
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


@dataclass
class PabgbRecord:
    index: int
    key: int
    offset: int
    size: int
    record_id: int = 0
    name: str = ""
    name_offset: int = 0
    payload_offset: int = 0


@dataclass
class PabgbFile:
    file_name: str
    game_file: str
    header_bytes: bytes
    body_bytes: bytearray
    records: List[PabgbRecord] = field(default_factory=list)
    header_format: str = "u16"
    paz_file: str = ""
    paz_offset: int = 0
    comp_size: int = 0
    orig_size: int = 0
    compressed: bool = False
    paz_dir: str = ""
    pamt_table_offset: int = 0


class GameDataEditor:

    KNOWN_FILES = {
        "buffinfo":       "Buff definitions — duration, stat values, levels",
        "skill":          "Skill data — cooldowns, damage, SP costs",
        "dropsetinfo":    "Drop rates — loot tables, drop chances",
        "characterinfo":  "NPC/enemy stats — HP, damage, behavior",
        "faction":        "Faction reputation — thresholds, rewards",
        "conditioninfo":  "Conditions/triggers — unlock requirements",
        "knowledgeinfo":  "Knowledge entries — unlock conditions",
        "questinfo":      "Quest data — requirements, rewards",
        "equipslotinfo":  "Equipment slots — what slots exist",
        "equiptypeinfo":  "Equipment types — weapon/armor categories",
        "storeinfo":      "Vendor shops — items, prices, limits",
        "iteminfo":       "Item database — all item definitions",
        "vehicleinfo":    "Mount/vehicle data",
        "inventory":      "Inventory rules — stack sizes",
    }

    def __init__(self, game_path: str):
        self.game_path = game_path
        self._pamt_index = None
        self._loaded_files: Dict[str, PabgbFile] = {}
        self._name_lookup: Dict[int, str] = {}

    def _get_pamt_index(self):
        if self._pamt_index is not None:
            return self._pamt_index

        for d in (os.path.join(os.path.dirname(__file__), 'Includes', 'BestCrypto'),
                  os.path.join(os.path.dirname(__file__), 'tools')):
            if os.path.isdir(d) and d not in sys.path:
                sys.path.insert(0, d)

        from crimson.game_mods.paz_parse import parse_pamt

        self._pamt_index = {}
        for entry in os.scandir(self.game_path):
            if not entry.is_dir() or not entry.name.isdigit():
                continue
            pamt_path = os.path.join(entry.path, "0.pamt")
            if not os.path.isfile(pamt_path):
                pamt_bak = pamt_path + ".sebak"
                if os.path.isfile(pamt_bak):
                    log.warning("Using backup PAMT: %s", pamt_bak)
                    pamt_path = pamt_bak
                else:
                    continue
            try:
                entries = parse_pamt(pamt_path, paz_dir=entry.path)
                for e in entries:
                    key = e.path.lower().replace("\\", "/")
                    self._pamt_index[key] = {
                        'path': e.path,
                        'paz_file': e.paz_file,
                        'paz_dir': entry.name,
                        'offset': e.offset,
                        'comp_size': e.comp_size,
                        'orig_size': e.orig_size,
                        'compressed': e.compressed,
                        'compression_type': e.compression_type,
                        'table_offset': e.table_offset,
                    }
            except Exception as ex:
                log.warning("Failed to parse PAMT %s: %s", pamt_path, ex)

        return self._pamt_index

    def list_available(self) -> List[Tuple[str, str, bool]]:
        index = self._get_pamt_index()
        result = []
        for name, desc in self.KNOWN_FILES.items():
            key = f"gamedata/{name}.pabgb"
            available = key in index
            loaded = name in self._loaded_files
            if available:
                result.append((name, desc, loaded))
        return result

    def extract_file(self, file_name: str) -> Optional[PabgbFile]:
        import lz4.block

        index = self._get_pamt_index()
        body_key = f"gamedata/{file_name}.pabgb"
        header_key = f"gamedata/{file_name}.pabgh"

        body_info = index.get(body_key)
        header_info = index.get(header_key)

        if not body_info:
            log.error("File not found in PAMT: %s", body_key)
            return None

        if not os.path.isfile(body_info['paz_file']):
            bak = body_info['paz_file'] + ".sebak"
            if os.path.isfile(bak):
                log.warning("PAZ missing, using backup: %s", bak)
                body_info['paz_file'] = bak
            else:
                log.error("PAZ file missing: %s", body_info['paz_file'])
                return None

        with open(body_info['paz_file'], 'rb') as f:
            f.seek(body_info['offset'])
            raw_body = f.read(body_info['comp_size'])

        if body_info['compressed']:
            body_data = bytearray(lz4.block.decompress(
                raw_body, uncompressed_size=body_info['orig_size']))
        else:
            body_data = bytearray(raw_body)

        header_data = b''
        if header_info:
            with open(header_info['paz_file'], 'rb') as f:
                f.seek(header_info['offset'])
                raw_hdr = f.read(header_info['comp_size'])
            if header_info['compressed']:
                header_data = lz4.block.decompress(
                    raw_hdr, uncompressed_size=header_info['orig_size'])
            else:
                header_data = bytes(raw_hdr)

        pf = PabgbFile(
            file_name=file_name,
            game_file=body_key,
            header_bytes=header_data,
            body_bytes=body_data,
            paz_file=body_info['paz_file'],
            paz_offset=body_info['offset'],
            comp_size=body_info['comp_size'],
            orig_size=body_info['orig_size'],
            compressed=body_info['compressed'],
            paz_dir=body_info['paz_dir'],
            pamt_table_offset=body_info.get('table_offset', 0),
        )

        self._parse_records(pf)
        self._loaded_files[file_name] = pf

        log.info("Loaded %s: %d records, %d bytes",
                 file_name, len(pf.records), len(pf.body_bytes))
        return pf

    def _parse_records(self, pf: PabgbFile) -> None:
        hdr = pf.header_bytes
        body = pf.body_bytes
        if not hdr or len(hdr) < 4:
            return

        count_u16 = struct.unpack_from('<H', hdr, 0)[0]
        expected_u16_8 = 2 + count_u16 * 8

        count_u32 = struct.unpack_from('<I', hdr, 0)[0]
        expected_u32_8 = 4 + count_u32 * 8

        if abs(expected_u16_8 - len(hdr)) <= 4 and count_u16 > 0:
            count = count_u16
            entry_size = 8
            offset_base = 2
            pf.header_format = "u16"
        elif abs(expected_u32_8 - len(hdr)) <= 4 and 0 < count_u32 < 100000:
            count = count_u32
            entry_size = 8
            offset_base = 4
            pf.header_format = "u32"
        else:
            expected_u16_6 = 2 + count_u16 * 6
            if abs(expected_u16_6 - len(hdr)) <= 4:
                count = count_u16
                entry_size = 6
                offset_base = 2
                pf.header_format = "u16"
            else:
                log.warning("Unknown header format for %s (hdr=%d bytes)", pf.file_name, len(hdr))
                return

        pf.records.clear()
        for i in range(count):
            base = offset_base + i * entry_size
            if base + entry_size > len(hdr):
                break

            if entry_size == 6:
                rec_key = struct.unpack_from('<H', hdr, base)[0]
                rec_off = struct.unpack_from('<I', hdr, base + 2)[0]
            else:
                rec_key = struct.unpack_from('<I', hdr, base)[0]
                rec_off = struct.unpack_from('<I', hdr, base + 4)[0]

            if i + 1 < count:
                next_base = offset_base + (i + 1) * entry_size
                if entry_size == 6:
                    next_off = struct.unpack_from('<I', hdr, next_base + 2)[0]
                else:
                    next_off = struct.unpack_from('<I', hdr, next_base + 4)[0]
                rec_size = next_off - rec_off
            else:
                rec_size = len(body) - rec_off

            rec = PabgbRecord(index=i, key=rec_key, offset=rec_off, size=rec_size)

            if rec_off + 8 < len(body):
                try:
                    rec.record_id = struct.unpack_from('<I', body, rec_off)[0]
                    name_len = struct.unpack_from('<I', body, rec_off + 4)[0]
                    if 0 < name_len < 300 and rec_off + 8 + name_len <= len(body):
                        raw_name = body[rec_off + 8:rec_off + 8 + name_len]
                        if all(32 <= b < 127 or b == 0 for b in raw_name):
                            rec.name = raw_name.decode('ascii', errors='replace').rstrip('\x00')
                            rec.name_offset = rec_off + 8
                            rec.payload_offset = rec_off + 8 + name_len
                            if rec.payload_offset < len(body) and body[rec.payload_offset] == 0:
                                rec.payload_offset += 1
                except (struct.error, IndexError):
                    pass

            if not rec.name:
                rec.payload_offset = rec_off

            pf.records.append(rec)

    def get_file(self, file_name: str) -> Optional[PabgbFile]:
        return self._loaded_files.get(file_name)

    def search_records(self, file_name: str, query: str) -> List[PabgbRecord]:
        pf = self._loaded_files.get(file_name)
        if not pf:
            return []
        q = query.lower().strip()
        if not q:
            return pf.records[:500]
        results = []
        for rec in pf.records:
            if q in rec.name.lower() or q in str(rec.key) or q in str(rec.record_id):
                results.append(rec)
        return results[:500]

    def get_record_hex(self, file_name: str, record_index: int,
                       max_bytes: int = 512) -> str:
        pf = self._loaded_files.get(file_name)
        if not pf or record_index >= len(pf.records):
            return ""
        rec = pf.records[record_index]
        start = rec.offset
        end = min(start + rec.size, start + max_bytes)
        data = pf.body_bytes[start:end]

        lines = []
        for row in range((len(data) + 15) // 16):
            addr = row * 16
            hex_part = ' '.join(f'{data[addr+j]:02X}' for j in range(min(16, len(data) - addr)))
            ascii_part = ''.join(
                chr(b) if 32 <= b < 127 else '.'
                for b in data[addr:addr + min(16, len(data) - addr)]
            )
            lines.append(f'{start + addr:06X}  {hex_part:<48s}  {ascii_part}')
        return '\n'.join(lines)

    def patch_bytes(self, file_name: str, offset: int, new_bytes: bytes) -> bool:
        pf = self._loaded_files.get(file_name)
        if not pf:
            return False
        if offset + len(new_bytes) > len(pf.body_bytes):
            return False
        pf.body_bytes[offset:offset + len(new_bytes)] = new_bytes
        return True

    def apply_to_game(self, file_name: str) -> Tuple[bool, str]:
        pf = self._loaded_files.get(file_name)
        if not pf:
            return False, f"{file_name} not loaded"

        if not pf.compressed:
            import shutil
            paz_backup = pf.paz_file + ".sebak"
            if not os.path.isfile(paz_backup):
                shutil.copy2(pf.paz_file, paz_backup)
            with open(pf.paz_file, 'r+b') as f:
                f.seek(pf.paz_offset)
                f.write(bytes(pf.body_bytes))
            return True, f"Written {len(pf.body_bytes):,} bytes to {os.path.basename(pf.paz_file)}"

        import lz4.block
        import shutil

        if len(pf.body_bytes) != pf.orig_size:
            return False, (
                f"Size changed ({pf.orig_size} -> {len(pf.body_bytes)}). "
                f"In-place patching requires same-size edits."
            )

        recompressed = lz4.block.compress(
            bytes(pf.body_bytes), mode='high_compression', store_size=False
        )

        if len(recompressed) > pf.comp_size:
            return False, (
                f"Recompressed ({len(recompressed)}) > original ({pf.comp_size}). "
                f"Cannot fit in-place."
            )

        padded = recompressed + b'\x00' * (pf.comp_size - len(recompressed))

        paz_backup = pf.paz_file + ".sebak"
        if not os.path.isfile(paz_backup):
            shutil.copy2(pf.paz_file, paz_backup)

        with open(pf.paz_file, 'r+b') as f:
            f.seek(pf.paz_offset)
            f.write(padded)

        checksum_msg = self._update_checksums(
            pf.paz_file, pf.paz_dir,
            pamt_table_offset=pf.pamt_table_offset,
            new_comp_size=len(recompressed))

        return True, (
            f"In-place patch: {file_name}.pabgb\n"
            f"  Recompressed: {len(recompressed):,} bytes (was {pf.comp_size:,})\n"
            f"  {checksum_msg}"
        )

    def _update_checksums(self, paz_file: str, paz_dir_name: str,
                          pamt_table_offset: int = 0, new_comp_size: int = 0) -> str:
        try:
            from crimson.game_mods import crimson_rs as crimson_rs

            with open(paz_file, 'rb') as f:
                chunk_data = f.read()
            new_chunk_checksum = crimson_rs.calculate_checksum(chunk_data)

            paz_dir_path = os.path.dirname(paz_file)
            pamt_path = os.path.join(paz_dir_path, '0.pamt')
            if not os.path.isfile(pamt_path):
                return "PAMT not found — checksums not updated"

            import shutil
            pamt_backup = pamt_path + ".sebak"
            if not os.path.isfile(pamt_backup):
                shutil.copy2(pamt_path, pamt_backup)

            with open(pamt_path, 'rb') as f:
                pamt_data = bytearray(f.read())

            if pamt_table_offset > 0 and new_comp_size > 0:
                if pamt_table_offset + 12 <= len(pamt_data):
                    struct.pack_into('<I', pamt_data, pamt_table_offset + 8, new_comp_size)
                    log.info("Updated PAMT comp_size to %d at offset %d",
                             new_comp_size, pamt_table_offset + 8)

            struct.pack_into('<I', pamt_data, 12 + 4, new_chunk_checksum)
            struct.pack_into('<I', pamt_data, 12 + 8, len(chunk_data))

            new_pamt_checksum = crimson_rs.calculate_checksum(bytes(pamt_data[12:]))
            struct.pack_into('<I', pamt_data, 0, new_pamt_checksum)

            with open(pamt_path, 'wb') as f:
                f.write(pamt_data)

            papgt_path = os.path.join(self.game_path, "meta", "0.papgt")
            if os.path.isfile(papgt_path):
                papgt_backup = papgt_path + ".sebak"
                if not os.path.isfile(papgt_backup):
                    shutil.copy2(papgt_path, papgt_backup)

                papgt = crimson_rs.parse_papgt_file(papgt_path)
                for entry in papgt['entries']:
                    if entry['group_name'] == paz_dir_name:
                        entry['pack_meta_checksum'] = new_pamt_checksum
                        break
                crimson_rs.write_papgt_file(papgt, papgt_path)

            return "Checksums updated (PAMT + PAPGT)"

        except ImportError:
            return "crimson_rs unavailable — checksums NOT updated"
        except Exception as ex:
            return f"Checksum update error: {ex}"

    def restore_file(self, file_name: str) -> Tuple[bool, str]:
        pf = self._loaded_files.get(file_name)
        if not pf:
            return False, f"{file_name} not loaded"

        import shutil
        restored = []

        for path in [pf.paz_file,
                     os.path.join(os.path.dirname(pf.paz_file), '0.pamt'),
                     os.path.join(self.game_path, "meta", "0.papgt")]:
            backup = path + ".sebak"
            if os.path.isfile(backup):
                shutil.copy2(backup, path)
                restored.append(os.path.basename(path))

        if restored:
            return True, f"Restored: {', '.join(restored)}"
        return False, "No backups found"

    def load_item_names(self) -> None:
        _db = get_connection()
        for row in _db.execute("SELECT item_key, name FROM items"):
            self._name_lookup[row['item_key']] = row['name']

        self._localization: Dict[str, str] = {}
        for loc_path in [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'localization_eng.json'),
            os.path.join(os.path.dirname(__file__), 'localization_eng.json'),
        ]:
            if os.path.isfile(loc_path):
                try:
                    with open(loc_path, 'r', encoding='utf-8') as f:
                        self._localization = json.load(f)
                    log.info("Loaded %d localization strings from %s",
                             len(self._localization), loc_path)
                except Exception as e:
                    log.warning("Failed to load localization: %s", e)
                break

    def get_item_name(self, key: int) -> str:
        return self._name_lookup.get(key, "")

    def get_localized_name(self, key: int) -> str:
        return self._localization.get(str(key), "")

    def resolve_record_display_name(self, file_name: str, rec) -> str:
        loc = self.get_localized_name(rec.record_id)
        if loc and len(loc) < 100:
            return f"{loc}  ({rec.name})"

        name = rec.name
        if name:
            for prefix in ['Faction_', 'DropSet_', 'Skill_', 'Knowledge_',
                           'BuffLevel_', 'Condition_', 'Quest_', 'Store_']:
                if name.startswith(prefix):
                    name = name[len(prefix):]
                    break
            return name.replace('_', ' ')
        return f"ID:{rec.record_id}"
