
import configparser
import json
import logging
import os
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


@dataclass
class ModChange:
    offset_in_file: int
    original_hex: str
    patched_hex: str
    label: str = ""
    paz_file: str = ""
    paz_offset: int = 0

    @property
    def original_bytes(self) -> bytes:
        return bytes.fromhex(self.original_hex)

    @property
    def patched_bytes(self) -> bytes:
        return bytes.fromhex(self.patched_hex)


@dataclass
class ModPatch:
    game_file: str
    changes: List[ModChange] = field(default_factory=list)
    paz_path: str = ""
    paz_base_offset: int = 0
    compressed: bool = False
    comp_size: int = 0
    orig_size: int = 0


@dataclass
class CommunityMod:
    filename: str
    name: str
    version: str = ""
    description: str = ""
    author: str = ""
    patches: List[ModPatch] = field(default_factory=list)
    enabled: bool = True
    status: str = "pending"
    error_msg: str = ""
    disabled_indices: List[int] = field(default_factory=list)


@dataclass
class PamtFileEntry:
    path: str
    paz_file: str
    paz_dir_rel: str
    offset: int
    comp_size: int
    orig_size: int
    compression_type: int

    @property
    def compressed(self) -> bool:
        return self.comp_size != self.orig_size


class PamtIndex:

    def __init__(self, game_path: str):
        self.game_path = game_path
        self._entries: Dict[str, PamtFileEntry] = {}
        self._built = False

    def build(self) -> int:
        import sys as _sys
        my_dir = os.path.dirname(os.path.abspath(__file__))
        for d in (os.path.join(my_dir, 'Includes', 'BestCrypto'),
                  os.path.join(my_dir, 'tools')):
            if os.path.isdir(d) and d not in _sys.path:
                _sys.path.insert(0, d)

        try:
            from crimson.game_mods.paz_parse import parse_pamt
        except ImportError:
            log.error("paz_parse module not found — cannot build PAMT index")
            return 0

        total = 0
        for entry in os.scandir(self.game_path):
            if not entry.is_dir() or not entry.name.isdigit():
                continue
            pamt_path = os.path.join(entry.path, "0.pamt")
            if not os.path.isfile(pamt_path):
                continue
            try:
                entries = parse_pamt(pamt_path, paz_dir=entry.path)
                for e in entries:
                    key = e.path.lower().replace("\\", "/")
                    self._entries[key] = PamtFileEntry(
                        path=e.path,
                        paz_file=e.paz_file,
                        paz_dir_rel=entry.name,
                        offset=e.offset,
                        comp_size=e.comp_size,
                        orig_size=e.orig_size,
                        compression_type=e.compression_type,
                    )
                    total += 1
            except Exception as ex:
                log.warning("Failed to parse PAMT %s: %s", pamt_path, ex)

        self._built = True
        log.info("PAMT index built: %d file entries from %s", total, self.game_path)
        return total

    def lookup(self, game_file: str) -> Optional[PamtFileEntry]:
        if not self._built:
            self.build()
        key = game_file.lower().replace("\\", "/")
        return self._entries.get(key)

    @property
    def entries(self) -> Dict[str, PamtFileEntry]:
        if not self._built:
            self.build()
        return self._entries


class CommunityModLoader:

    MODLOAD_DIR = os.path.join("bin64", "SEModLoad")
    JSON_DIR = os.path.join("bin64", "SEModLoad", "Json")
    ASI_DIR = os.path.join("bin64", "SEModLoad", "ASI")
    CONFIG_FILE = "semodload.json"

    def __init__(self, game_path: str):
        self.game_path = game_path
        self.mods: List[CommunityMod] = []
        self._pamt_index: Optional[PamtIndex] = None
        self._config_path = os.path.join(
            game_path, self.MODLOAD_DIR, self.CONFIG_FILE)

    @property
    def json_dir(self) -> str:
        return os.path.join(self.game_path, self.JSON_DIR)

    @property
    def asi_dir(self) -> str:
        return os.path.join(self.game_path, self.ASI_DIR)

    def ensure_folders(self) -> None:
        os.makedirs(self.json_dir, exist_ok=True)
        os.makedirs(self.asi_dir, exist_ok=True)

    def get_pamt_index(self) -> PamtIndex:
        if self._pamt_index is None:
            self._pamt_index = PamtIndex(self.game_path)
            self._pamt_index.build()
        return self._pamt_index


    def load_config(self) -> dict:
        if os.path.isfile(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as ex:
                log.warning("Failed to load mod config: %s", ex)
        return {"mods": {}}

    def save_config(self) -> None:
        config = {"mods": {}}
        for mod in self.mods:
            config["mods"][mod.filename] = {
                "enabled": mod.enabled,
                "disabled_indices": mod.disabled_indices,
            }
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)


    def scan_mods(self) -> List[CommunityMod]:
        self.mods.clear()

        if not os.path.isdir(self.json_dir):
            return self.mods

        config = self.load_config()
        mod_configs = config.get("mods", {})

        for fname in sorted(os.listdir(self.json_dir)):
            if not fname.lower().endswith(".json"):
                continue
            fpath = os.path.join(self.json_dir, fname)
            try:
                mod = self._parse_mod_file(fpath, fname)
                if mod:
                    if fname in mod_configs:
                        mod.enabled = mod_configs[fname].get("enabled", True)
                        mod.disabled_indices = mod_configs[fname].get(
                            "disabled_indices", [])
                    self.mods.append(mod)
            except Exception as ex:
                log.warning("Failed to parse mod %s: %s", fname, ex)
                err_mod = CommunityMod(
                    filename=fname,
                    name=fname,
                    status="error",
                    error_msg=str(ex),
                )
                self.mods.append(err_mod)

        log.info("Scanned %d community mods from %s", len(self.mods), self.json_dir)
        return self.mods

    def _parse_mod_file(self, path: str, filename: str) -> Optional[CommunityMod]:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            log.warning("Mod %s: root is not an object, skipping", filename)
            return None

        mod = CommunityMod(
            filename=filename,
            name=data.get("name", filename.replace(".json", "")),
            version=data.get("version", ""),
            description=data.get("description", ""),
            author=data.get("author", ""),
        )

        patches_data = data.get("patches", [])
        if not isinstance(patches_data, list):
            mod.status = "error"
            mod.error_msg = "Missing or invalid 'patches' array"
            return mod

        for patch_data in patches_data:
            game_file = patch_data.get("game_file", "")
            if not game_file:
                continue

            mod_patch = ModPatch(game_file=game_file)
            changes_data = patch_data.get("changes", [])

            for change_data in changes_data:
                change = ModChange(
                    offset_in_file=change_data.get("offset", 0),
                    original_hex=change_data.get("original", ""),
                    patched_hex=change_data.get("patched", ""),
                    label=change_data.get("label", ""),
                )
                mod_patch.changes.append(change)

            mod.patches.append(mod_patch)

        return mod


    def resolve_mod(self, mod: CommunityMod) -> bool:
        index = self.get_pamt_index()
        all_ok = True

        for patch in mod.patches:
            entry = index.lookup(patch.game_file)
            if entry is None:
                mod.status = "error"
                mod.error_msg = f"File not found in PAMT: {patch.game_file}"
                all_ok = False
                continue

            patch.paz_path = entry.paz_file
            patch.paz_base_offset = entry.offset
            patch.compressed = entry.compressed
            patch.comp_size = entry.comp_size
            patch.orig_size = entry.orig_size

            for change in patch.changes:
                change.paz_file = entry.paz_file
                change.paz_offset = entry.offset + change.offset_in_file

                if change.offset_in_file + len(change.patched_bytes) > entry.orig_size:
                    mod.status = "error"
                    mod.error_msg = (
                        f"Change at offset {change.offset_in_file} exceeds "
                        f"file size {entry.orig_size} in {patch.game_file}"
                    )
                    all_ok = False

        return all_ok

    def validate_change(self, change: ModChange) -> Tuple[str, str]:
        try:
            with open(change.paz_file, "rb") as f:
                f.seek(change.paz_offset)
                current = f.read(max(len(change.original_bytes),
                                     len(change.patched_bytes)))
        except Exception as ex:
            return "error", str(ex)

        orig = change.original_bytes
        patched = change.patched_bytes
        check_len = len(patched)

        if current[:check_len] == patched:
            return "patched", "Already applied"
        elif current[:len(orig)] == orig:
            return "original", "Ready to apply"
        else:
            return "modified", f"Current: {current[:check_len].hex().upper()}"


    def apply_mod(self, mod: CommunityMod,
                  backup_callback=None) -> Tuple[bool, str]:
        if not mod.enabled:
            return False, "Mod is disabled"

        if not self.resolve_mod(mod):
            return False, mod.error_msg

        applied = 0
        skipped = 0
        errors = []

        changes_by_paz: Dict[str, List[ModChange]] = {}
        flat_idx = 0
        for patch in mod.patches:
            for change in patch.changes:
                if flat_idx in mod.disabled_indices:
                    skipped += 1
                    flat_idx += 1
                    continue
                changes_by_paz.setdefault(change.paz_file, []).append(change)
                flat_idx += 1

        compressed_patches: Dict[str, ModPatch] = {}
        for patch in mod.patches:
            if patch.compressed:
                compressed_patches[patch.game_file] = patch

        for paz_path, changes in changes_by_paz.items():
            is_compressed = any(
                cp.paz_path == paz_path
                for cp in compressed_patches.values()
            )
            if is_compressed:
                continue

            if not os.path.isfile(paz_path):
                errors.append(f"PAZ file not found: {paz_path}")
                continue

            if backup_callback:
                backup_callback(paz_path)

            try:
                with open(paz_path, "r+b") as f:
                    for change in changes:
                        f.seek(change.paz_offset)
                        current = f.read(len(change.patched_bytes))

                        if current == change.patched_bytes:
                            applied += 1
                            continue

                        if change.original_hex:
                            orig = change.original_bytes
                            f.seek(change.paz_offset)
                            check = f.read(len(orig))
                            if check != orig:
                                errors.append(
                                    f"Byte mismatch at {change.label or hex(change.paz_offset)}: "
                                    f"expected {change.original_hex}, got {check.hex().upper()}"
                                )
                                continue

                        f.seek(change.paz_offset)
                        f.write(change.patched_bytes)
                        applied += 1

            except Exception as ex:
                errors.append(f"Failed to patch {paz_path}: {ex}")

        for game_file, patch in compressed_patches.items():
            try:
                c_applied, c_msg = self._apply_compressed_patch(patch, backup_callback)
                applied += c_applied
                if c_msg:
                    errors.append(c_msg)
            except Exception as ex:
                errors.append(f"Compressed patch failed for {game_file}: {ex}")

        if errors:
            mod.status = "error"
            mod.error_msg = "; ".join(errors[:3])
            if applied > 0:
                mod.status = "applied"
                msg = f"Applied {applied} changes, {len(errors)} errors, {skipped} skipped"
            else:
                msg = f"Failed: {errors[0]}"
            return applied > 0, msg

        mod.status = "applied"
        msg = f"Applied {applied} changes"
        if skipped:
            msg += f", {skipped} skipped"
        return True, msg

    def _apply_compressed_patch(self, patch: ModPatch,
                               backup_callback=None) -> Tuple[int, str]:
        import lz4.block
        import shutil

        with open(patch.paz_path, 'rb') as f:
            f.seek(patch.paz_base_offset)
            compressed = f.read(patch.comp_size)

        decompressed = bytearray(
            lz4.block.decompress(compressed, uncompressed_size=patch.orig_size)
        )

        applied = 0
        for change in patch.changes:
            off = change.offset_in_file
            if off + len(change.patched_bytes) > len(decompressed):
                continue

            current = bytes(decompressed[off:off + len(change.patched_bytes)])
            if current == change.patched_bytes:
                applied += 1
                continue

            if change.original_hex:
                orig = change.original_bytes
                if bytes(decompressed[off:off + len(orig)]) != orig:
                    log.warning("Compressed patch mismatch at %d in %s",
                                off, patch.game_file)
                    continue

            decompressed[off:off + len(change.patched_bytes)] = change.patched_bytes
            applied += 1

        if applied == 0:
            return 0, ""

        recompressed = lz4.block.compress(
            bytes(decompressed), mode='high_compression', store_size=False
        )

        if len(recompressed) > patch.comp_size:
            return 0, (
                f"Recompressed ({len(recompressed)}) > original ({patch.comp_size}) "
                f"for {patch.game_file}. Cannot fit in-place."
            )

        padded = recompressed + b'\x00' * (patch.comp_size - len(recompressed))

        paz_backup = patch.paz_path + ".sebak"
        if not os.path.isfile(paz_backup):
            shutil.copy2(patch.paz_path, paz_backup)

        if backup_callback:
            backup_callback(patch.paz_path)

        with open(patch.paz_path, 'r+b') as f:
            f.seek(patch.paz_base_offset)
            f.write(padded)

        self._update_pamt_compressed_size(patch, len(recompressed))
        self._update_checksums_for_paz(patch.paz_path)
        self._verify_compressed_patch(patch, bytes(decompressed), len(recompressed))

        log.info("In-place patch: %d changes applied to %s (%d -> %d bytes compressed)",
                 applied, patch.game_file, len(recompressed), patch.comp_size)
        return applied, ""

    def _update_pamt_compressed_size(self, patch: ModPatch,
                                     new_comp_size: int) -> None:
        pamt_path = os.path.join(os.path.dirname(patch.paz_path), '0.pamt')
        if not os.path.isfile(pamt_path):
            raise FileNotFoundError(f"PAMT not found: {pamt_path}")
        pamt_backup = pamt_path + ".sebak"
        if not os.path.isfile(pamt_backup):
            shutil.copy2(pamt_path, pamt_backup)
        with open(pamt_path, 'rb') as f:
            pamt_data = bytearray(f.read())
        target = struct.pack(
            '<III', patch.paz_base_offset, patch.comp_size, patch.orig_size)
        positions = []
        start = 0
        while True:
            position = pamt_data.find(target, start)
            if position < 0:
                break
            positions.append(position)
            start = position + 1
        if len(positions) != 1:
            raise ValueError(
                f"Expected one PAMT record for {patch.game_file}; "
                f"found {len(positions)}")
        struct.pack_into('<I', pamt_data, positions[0] + 4, new_comp_size)
        with open(pamt_path, 'wb') as f:
            f.write(pamt_data)

    def _verify_compressed_patch(self, patch: ModPatch, expected_body: bytes,
                                 expected_comp_size: int) -> None:
        from crimson.game_mods import crimson_rs as crimson_rs

        pamt_path = os.path.join(os.path.dirname(patch.paz_path), '0.pamt')
        pamt = crimson_rs.parse_pamt_file(pamt_path)
        matches = []
        for directory in pamt.get('directories', []):
            for entry in directory.get('files', []):
                if (int(entry.get('chunk_offset', -1)) == patch.paz_base_offset
                        and int(entry.get('uncompressed_size', -1)) == patch.orig_size):
                    matches.append(entry)
        if len(matches) != 1:
            raise ValueError(
                f"Post-write PAMT record is ambiguous for {patch.game_file}")
        entry = matches[0]
        if int(entry['compressed_size']) != expected_comp_size:
            raise ValueError(
                f"Post-write compressed size is {entry['compressed_size']}; "
                f"expected {expected_comp_size}")
        with open(patch.paz_path, 'rb') as f:
            f.seek(patch.paz_base_offset)
            compressed = f.read(expected_comp_size)
        verified = crimson_rs.decompress_data(
            compressed,
            int(entry['compression']),
            int(entry['uncompressed_size']),
        )
        if bytes(verified) != expected_body:
            raise ValueError(
                f"Post-write decompression mismatch for {patch.game_file}")

    def _update_checksums_for_paz(self, paz_path: str) -> None:
        import struct

        try:
            from crimson.game_mods import crimson_rs as crimson_rs
        except ImportError:
            log.warning("crimson_rs unavailable — checksums NOT updated")
            return

        import shutil

        with open(paz_path, 'rb') as f:
            chunk_data = f.read()
        new_chunk_checksum = crimson_rs.calculate_checksum(chunk_data)

        paz_dir = os.path.dirname(paz_path)
        pamt_path = os.path.join(paz_dir, '0.pamt')
        if not os.path.isfile(pamt_path):
            return

        pamt_backup = pamt_path + ".sebak"
        if not os.path.isfile(pamt_backup):
            shutil.copy2(pamt_path, pamt_backup)

        with open(pamt_path, 'rb') as f:
            pamt_data = bytearray(f.read())

        struct.pack_into('<I', pamt_data, 12 + 4, new_chunk_checksum)
        struct.pack_into('<I', pamt_data, 12 + 8, len(chunk_data))

        new_pamt_checksum = crimson_rs.calculate_checksum(bytes(pamt_data[12:]))
        struct.pack_into('<I', pamt_data, 0, new_pamt_checksum)

        with open(pamt_path, 'wb') as f:
            f.write(pamt_data)

        paz_dir_name = os.path.basename(paz_dir)
        papgt_path = os.path.join(self.game_path, "meta", "0.papgt")
        if not os.path.isfile(papgt_path):
            return

        papgt_backup = papgt_path + ".sebak"
        if not os.path.isfile(papgt_backup):
            shutil.copy2(papgt_path, papgt_backup)

        papgt = crimson_rs.parse_papgt_file(papgt_path)
        for entry in papgt['entries']:
            if entry['group_name'] == paz_dir_name:
                entry['pack_meta_checksum'] = new_pamt_checksum
                break
        crimson_rs.write_papgt_file(papgt, papgt_path)

    def apply_all_enabled(self, backup_callback=None,
                          progress_callback=None) -> Tuple[int, int, List[str]]:
        applied_total = 0
        skipped_total = 0
        errors = []

        for i, mod in enumerate(self.mods):
            if progress_callback:
                progress_callback(i, len(self.mods), mod.name)

            if not mod.enabled:
                skipped_total += 1
                mod.status = "skipped"
                continue

            ok, msg = self.apply_mod(mod, backup_callback=backup_callback)
            if ok:
                applied_total += 1
            else:
                errors.append(f"{mod.name}: {msg}")

        return applied_total, skipped_total, errors

    def get_mod_status_summary(self, mod: CommunityMod) -> str:
        if not mod.enabled:
            return "Disabled"

        if mod.status == "error":
            return f"Error: {mod.error_msg[:60]}"

        if not self.resolve_mod(mod):
            return f"Cannot resolve: {mod.error_msg[:60]}"

        total = 0
        applied = 0
        original = 0
        modified = 0

        for patch in mod.patches:
            if patch.compressed:
                try:
                    import lz4.block
                    with open(patch.paz_path, 'rb') as f:
                        f.seek(patch.paz_base_offset)
                        comp_data = f.read(patch.comp_size)
                    decomp = lz4.block.decompress(comp_data, uncompressed_size=patch.orig_size)
                    c_applied = 0
                    c_original = 0
                    c_mismatch = 0
                    for change in patch.changes:
                        off = change.offset_in_file
                        if off + len(change.patched_bytes) > len(decomp):
                            c_mismatch += 1
                            continue
                        current = decomp[off:off + len(change.patched_bytes)]
                        if current == change.patched_bytes:
                            c_applied += 1
                        elif change.original_hex and current == change.original_bytes:
                            c_original += 1
                        else:
                            c_mismatch += 1
                    c_total = len(patch.changes)
                    if c_applied == c_total:
                        return f"Applied ({c_total} changes in {patch.game_file})"
                    if c_original == c_total:
                        return f"Ready ({c_total} changes in {patch.game_file})"
                    if c_mismatch > 0:
                        return f"Outdated — {c_mismatch}/{c_total} bytes don't match (game updated?)"
                    return f"Partial ({c_applied}/{c_total} applied in {patch.game_file})"
                except Exception as ex:
                    return f"Error reading {patch.game_file}: {str(ex)[:40]}"
            for change in patch.changes:
                status, _ = self.validate_change(change)
                total += 1
                if status == "patched":
                    applied += 1
                elif status == "original":
                    original += 1
                elif status == "modified":
                    modified += 1

        if total == 0:
            return "No changes"
        if applied == total:
            return f"Applied ({total} changes)"
        if original == total:
            return f"Ready ({total} changes)"
        if modified > 0 and original == 0 and applied == 0:
            return f"Outdated — {modified}/{total} bytes don't match (game updated?)"
        if modified > 0:
            return f"Conflict ({modified}/{total} modified)"
        return f"Partial ({applied}/{total} applied)"


    def detect_all_conflicts(self) -> List[str]:
        warnings = []
        enabled_mods = [m for m in self.mods if m.enabled]

        for mod in enabled_mods:
            self.resolve_mod(mod)

        byte_owners: Dict[Tuple[str, int], str] = {}

        for mod in enabled_mods:
            for patch in mod.patches:
                if patch.compressed:
                    continue
                for change in patch.changes:
                    status, detail = self.validate_change(change)
                    if status == "modified":
                        warnings.append(
                            f"{mod.name}: {change.label or patch.game_file} "
                            f"@ offset {change.offset_in_file} — "
                            f"bytes already modified (possible conflict with SE patches). "
                            f"{detail}"
                        )

                    for byte_off in range(len(change.patched_bytes)):
                        key = (change.paz_file, change.paz_offset + byte_off)
                        if key in byte_owners and byte_owners[key] != mod.name:
                            other = byte_owners[key]
                            warnings.append(
                                f"Conflict: {mod.name} and {other} both modify "
                                f"{patch.game_file} @ offset {change.offset_in_file}. "
                                f"Last applied wins ({mod.name} loads after {other})."
                            )
                            break
                        byte_owners[key] = mod.name

        return list(dict.fromkeys(warnings))


ASI_SUFFIX = ".asi"
DISABLED_SUFFIX = ".asi.disabled"
ASI_LOADERS = {"winmm.dll", "version.dll", "dinput8.dll", "dsound.dll"}


@dataclass
class AsiPlugin:
    name: str
    path: Path
    enabled: bool
    installed: bool
    ini_path: Optional[Path] = None


class AsiManager:

    def __init__(self, game_path: str):
        self.game_path = game_path
        self.bin64 = Path(game_path) / "bin64"
        self.asi_source = Path(game_path) / "bin64" / "SEModLoad" / "ASI"
        self.plugins: List[AsiPlugin] = []

    def has_loader(self) -> bool:
        return any((self.bin64 / name).exists() for name in ASI_LOADERS)

    def scan(self) -> List[AsiPlugin]:
        self.plugins.clear()
        seen_names = set()

        if self.bin64.exists():
            for f in sorted(self.bin64.iterdir()):
                if not f.is_file():
                    continue
                if f.suffix.lower() == ASI_SUFFIX:
                    name = f.stem
                    self.plugins.append(AsiPlugin(
                        name=name, path=f, enabled=True, installed=True,
                        ini_path=self._find_ini(f),
                    ))
                    seen_names.add(name.lower())
                elif f.name.lower().endswith(DISABLED_SUFFIX):
                    name = f.name[:-len(DISABLED_SUFFIX)]
                    self.plugins.append(AsiPlugin(
                        name=name, path=f, enabled=False, installed=True,
                        ini_path=self._find_ini(f.with_name(name + ".ini")),
                    ))
                    seen_names.add(name.lower())

        if self.asi_source.exists():
            for f in sorted(self.asi_source.iterdir()):
                if f.is_file() and f.suffix.lower() == ASI_SUFFIX:
                    if f.stem.lower() not in seen_names:
                        self.plugins.append(AsiPlugin(
                            name=f.stem, path=f, enabled=False, installed=False,
                            ini_path=self._find_ini(f),
                        ))

        return self.plugins

    def install_plugin(self, plugin: AsiPlugin) -> str:
        if plugin.installed:
            return f"{plugin.name} already installed"

        dest = self.bin64 / (plugin.name + ASI_SUFFIX)
        shutil.copy2(plugin.path, dest)
        installed_files = [dest.name]

        if plugin.ini_path and plugin.ini_path.exists():
            ini_dest = self.bin64 / plugin.ini_path.name
            shutil.copy2(plugin.ini_path, ini_dest)
            installed_files.append(ini_dest.name)

        if not self.has_loader():
            for loader_name in ASI_LOADERS:
                loader_src = plugin.path.parent / loader_name
                if loader_src.exists():
                    shutil.copy2(loader_src, self.bin64 / loader_name)
                    installed_files.append(loader_name)
                    break

        plugin.path = dest
        plugin.installed = True
        plugin.enabled = True
        log.info("Installed ASI: %s (%s)", plugin.name, installed_files)
        return f"Installed {', '.join(installed_files)}"

    def uninstall_plugin(self, plugin: AsiPlugin) -> str:
        if not plugin.installed:
            return f"{plugin.name} not installed"

        deleted = []
        if plugin.path.exists():
            plugin.path.unlink()
            deleted.append(plugin.path.name)

        for f in self.bin64.iterdir():
            if f.suffix.lower() == ".ini" and f.stem.lower().startswith(plugin.name.lower()):
                f.unlink()
                deleted.append(f.name)

        plugin.installed = False
        plugin.enabled = False
        log.info("Uninstalled ASI: %s (%s)", plugin.name, deleted)
        return f"Removed {', '.join(deleted)}"

    def enable_plugin(self, plugin: AsiPlugin) -> None:
        if plugin.enabled or not plugin.installed:
            return
        new_path = plugin.path.with_name(plugin.name + ASI_SUFFIX)
        plugin.path.rename(new_path)
        plugin.path = new_path
        plugin.enabled = True

    def disable_plugin(self, plugin: AsiPlugin) -> None:
        if not plugin.enabled or not plugin.installed:
            return
        new_path = plugin.path.with_name(plugin.name + DISABLED_SUFFIX)
        plugin.path.rename(new_path)
        plugin.path = new_path
        plugin.enabled = False

    def _find_ini(self, asi_path: Path) -> Optional[Path]:
        ini = asi_path.with_suffix(".ini")
        if ini.exists():
            return ini
        parent = asi_path.parent
        stem = asi_path.stem.lower()
        for f in parent.iterdir():
            if f.suffix.lower() == ".ini" and f.stem.lower().startswith(stem):
                return f
        return None
