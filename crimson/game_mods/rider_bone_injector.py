"""Rider bone injector — makes any skeleton rideable.

Injects B_Rider_01 from a reference skeleton (Blackstar) into a target
skeleton that lacks it, enabling the game's mount/ride system.

Usage:
    from crimson.game_mods.rider_bone_injector import make_rideable, deploy_overlay, restore_overlay

    # Make a skeleton rideable and deploy
    result = make_rideable(
        game_path="C:/Program Files (x86)/Steam/steamapps/common/Crimson Desert",
        target_skeleton_paz_path="character/model/2_mon/cd_m0004_00_dragon/cd_m0004_00_golemdragon/cd_m0004_00_golemdragon.pab",
        rider_y=8.0,          # height above Spine1 (tune per mount)
        overlay_group="0062",  # PAZ group for the overlay
    )

    # Restore (remove overlay)
    restore_overlay(game_path, overlay_group="0062")

Tested in-game 2026-04-17: Golden Star (golemdragon) confirmed rideable.
"""

from __future__ import annotations

import os
import struct
import sys
import shutil
import tempfile
from pathlib import Path
from typing import Optional

# Reference rider bone data from Blackstar (cd_m0004_00_dragon.pab, bone index 19)
# Extracted once, reused for all injections.
RIDER_BONE_HASH = 0xF882D467
RIDER_BONE_NAME = "B_Rider_01"
RIDER_BONE_PARENT_NAME = "Bip01 Spine1"

# Default rider bone transforms (from Blackstar)
# Position Y is overrideable — the rest are Blackstar's original values
DEFAULT_RIDER_POSITION = (0.19621708989143372, 0.0, -0.7507870197296143)
DEFAULT_RIDER_ROTATION = (-0.4132637083530426, -0.4132637679576874, 0.5737670063972473, 0.5737749338150024)
DEFAULT_RIDER_SCALE = (0.9999991655349731, 0.9999992251396179, 0.999999463558197)

# Reference file for full bone data (matrices etc.)
_BLACKSTAR_PAB = os.path.join(os.path.dirname(__file__), "..", "_dragon_research", "blackstar_cd_m0004_00_dragon.pab")


def _parse_bones_minimal(data: bytes):
    """Parse .pab bones — returns list of dicts with name, parent, raw bytes."""
    bone_count = struct.unpack_from('<H', data, 0x14)[0]
    off = 0x16
    bones = []
    for i in range(bone_count):
        start = off
        off += 4  # hash
        nl = data[off]; off += 1  # name length
        name = data[off:off + nl].decode('ascii', 'replace'); off += nl
        parent = struct.unpack_from('<i', data, off)[0]; off += 4
        off += 256 + 12 + 16 + 12  # matrices + scale + rot + pos
        bones.append({
            'index': i, 'name': name, 'parent': parent,
            'start': start, 'end': off, 'raw': data[start:off]
        })
    return bones, off, bone_count


def _get_reference_rider_bone(blackstar_pab_path: str = None) -> bytes:
    """Get B_Rider_01 raw bone bytes from Blackstar reference."""
    path = blackstar_pab_path or _BLACKSTAR_PAB
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Blackstar reference .pab not found at: {path}\n"
            f"Provide blackstar_pab_path or place blackstar_cd_m0004_00_dragon.pab "
            f"in _dragon_research/")
    data = open(path, 'rb').read()
    bones, _, _ = _parse_bones_minimal(data)
    for b in bones:
        if b['name'] == RIDER_BONE_NAME:
            return b['raw']
    raise ValueError(f"B_Rider_01 not found in reference skeleton: {path}")


def has_rider_bone(pab_data: bytes) -> bool:
    """Check if a .pab skeleton already has B_Rider_01."""
    return b'B_Rider_01' in pab_data


def inject_rider_bone(pab_data: bytes, rider_y: float = 8.0,
                      blackstar_pab_path: str = None) -> bytes:
    """Inject B_Rider_01 into a .pab skeleton.

    Args:
        pab_data: Raw bytes of the target .pab file
        rider_y: Y offset for rider position (height above Spine1).
                 Higher = rider sits higher. Tune per mount.
        blackstar_pab_path: Path to Blackstar .pab (reference bone source)

    Returns:
        Modified .pab bytes with B_Rider_01 appended
    """
    if has_rider_bone(pab_data):
        return pab_data  # Already has it

    bones, tail_start, bone_count = _parse_bones_minimal(pab_data)

    # Find Bip01 Spine1 as parent
    spine1_idx = -1
    for b in bones:
        if b['name'] == RIDER_BONE_PARENT_NAME:
            spine1_idx = b['index']
            break
    if spine1_idx < 0:
        raise ValueError(f"'{RIDER_BONE_PARENT_NAME}' not found in target skeleton. "
                         f"Available bones: {[b['name'] for b in bones[:20]]}...")

    # Get reference rider bone
    rider_raw = bytearray(_get_reference_rider_bone(blackstar_pab_path))

    # Patch parent index to target's Spine1
    name_len = rider_raw[4]
    parent_off = 5 + name_len
    struct.pack_into('<i', rider_raw, parent_off, spine1_idx)

    # Patch Y position
    pos_off = parent_off + 4 + 256 + 12 + 16  # after parent + matrices + scale + rot
    orig_pos = struct.unpack_from('<3f', rider_raw, pos_off)
    struct.pack_into('<3f', rider_raw, pos_off, orig_pos[0], rider_y, orig_pos[2])

    # Build new file
    # Header with updated bone count
    new_count = bone_count + 1
    header = bytearray(pab_data[:0x14]) + struct.pack('<H', new_count)

    # Original bone data
    bone_data = pab_data[0x16:tail_start]

    # Tail: per-bone flags + volume data
    # Append flag=0 (no volume) for new bone
    flags = bytearray(pab_data[tail_start:tail_start + bone_count])
    volume = pab_data[tail_start + bone_count:]
    flags.append(0)  # No bone volume for rider bone

    return bytes(header) + bone_data + bytes(rider_raw) + bytes(flags) + volume


def _write_scaled_appearance(game_path: str, appearance_paz_path: str,
                             character_scale: float, mod_root: str):
    """Extract an appearance XML from PAZ, modify CharacterScale, write to mod folder.

    IMPORTANT: Always extracts the original XML first to preserve all attributes
    (Prefab Name, Head, Hair, Armor, Customization, Audio, etc.). Only the
    CharacterScale value is modified via regex replacement.

    Args:
        game_path: Game install directory
        appearance_paz_path: Full PAZ internal path to .app.xml
        character_scale: New scale value
        mod_root: Root of the mod folder tree to write into
    """
    import re
    from crimson.game_mods import crimson_rs as crimson_rs

    # Game update changed .app.xml -> .app_xml
    if appearance_paz_path.endswith('.app.xml'):
        appearance_paz_path = appearance_paz_path[:-len('.app.xml')] + '.app_xml'

    app_dir_parts = appearance_paz_path.split('/')[:-1]
    app_file = appearance_paz_path.split('/')[-1]
    app_paz_dir = '/'.join(app_dir_parts)

    # Extract original from PAZ — preserves all sections and attributes
    try:
        raw = bytes(crimson_rs.extract_file(game_path, '0009', app_paz_dir, app_file))
        app_content = raw.decode('utf-8', errors='replace')
        if app_content.startswith('﻿'):
            app_content = app_content[1:]
    except Exception as e:
        raise RuntimeError(
            f"Failed to extract appearance XML from PAZ:\n"
            f"  Path: {appearance_paz_path}\n"
            f"  Error: {e}\n"
            f"  Tip: verify the PAZ path exists in 0009/0.pamt") from e

    # Regex-replace ONLY the CharacterScale value
    if 'CharacterScale' not in app_content:
        raise ValueError(
            f"No CharacterScale attribute found in {appearance_paz_path}.\n"
            f"Content: {app_content[:200]}...")

    app_content = re.sub(
        r'CharacterScale="[^"]*"',
        f'CharacterScale="{character_scale}"',
        app_content)

    # Write to mod folder with correct internal PAZ path
    out_dir = os.path.join(mod_root, *app_dir_parts)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, app_file), 'w', encoding='utf-8') as f:
        f.write(app_content)


def scale_appearance(game_path: str, appearance_paz_path: str,
                     character_scale: float, overlay_group: str = "0063") -> dict:
    """Standalone: override CharacterScale for any creature and deploy as overlay.

    This is a standalone function that ONLY changes the scale — no skeleton
    modification. Use make_rideable() if you also need B_Rider_01 injection.

    Args:
        game_path: Game install directory
        appearance_paz_path: Full PAZ path to the .app.xml
        character_scale: New scale value (e.g. 2.0)
        overlay_group: PAZ group for overlay

    Returns:
        dict with deployment info
    """
    from crimson.game_mods import crimson_rs as pack_mod

    gp = Path(game_path)

    with tempfile.TemporaryDirectory(prefix='scale_mod_') as tmp_dir:
        _write_scaled_appearance(game_path, appearance_paz_path,
                                 character_scale, tmp_dir)

        pack_out = os.path.join(tmp_dir, 'output')
        os.makedirs(pack_out, exist_ok=True)
        crimson_rs.pack_mod.pack_mod(
            game_dir=game_path, mod_folder=tmp_dir,
            output_dir=pack_out, group_name=overlay_group)

        # Backup PAPGT
        papgt = gp / 'meta' / '0.papgt'
        backup = papgt.with_suffix(f'.papgt.scale_{overlay_group}_bak')
        if papgt.exists() and not backup.exists():
            shutil.copy2(papgt, backup)

        # Deploy
        dest = gp / overlay_group
        dest.mkdir(exist_ok=True)
        shutil.copyfile(os.path.join(pack_out, overlay_group, '0.paz'), dest / '0.paz')
        shutil.copyfile(os.path.join(pack_out, overlay_group, '0.pamt'), dest / '0.pamt')
        shutil.copyfile(os.path.join(pack_out, 'meta', '0.papgt'), papgt)

    return {
        'overlay_group': overlay_group,
        'appearance': appearance_paz_path,
        'scale': character_scale,
        'paz_size': os.path.getsize(dest / '0.paz'),
    }


def make_rideable(game_path: str,
                  target_skeleton_paz_path: str,
                  rider_y: float = 8.0,
                  overlay_group: str = "0062",
                  appearance_paz_path: str = None,
                  character_scale: float = None,
                  blackstar_pab_path: str = None) -> dict:
    """Extract a skeleton from PAZ, inject B_Rider_01, and deploy as overlay.

    Args:
        game_path: Game install directory
        target_skeleton_paz_path: Full PAZ internal path to the .pab
            e.g. "character/model/2_mon/.../cd_m0004_00_golemdragon.pab"
        rider_y: Height offset for rider bone
        overlay_group: PAZ group number for overlay (must not be vanilla)
        appearance_paz_path: Optional PAZ path to an .app.xml to override CharacterScale
        character_scale: If set with appearance_paz_path, overrides CharacterScale
        blackstar_pab_path: Path to Blackstar reference .pab

    Returns:
        dict with deployment info
    """
    # Lazy import — crimson_rs may not be on path in all environments
    from crimson.game_mods import crimson_rs as crimson_rs
    from crimson.game_mods import crimson_rs as pack_mod

    gp = Path(game_path)

    # Split PAZ path into directory + filename
    paz_dir = '/'.join(target_skeleton_paz_path.split('/')[:-1])
    paz_file = target_skeleton_paz_path.split('/')[-1]

    # Extract the skeleton from PAZ
    with tempfile.TemporaryDirectory(prefix='extract_') as ext_dir:
        crimson_rs.extract_file(game_path, '0009', paz_dir, paz_file, output_dir=ext_dir)
        extracted = os.path.join(ext_dir, paz_file)
        if not os.path.exists(extracted):
            raise FileNotFoundError(f"Failed to extract {target_skeleton_paz_path}")
        pab_data = open(extracted, 'rb').read()

    # Inject rider bone
    modified = inject_rider_bone(pab_data, rider_y=rider_y,
                                 blackstar_pab_path=blackstar_pab_path)

    # Build and deploy overlay
    with tempfile.TemporaryDirectory(prefix='rider_mod_') as tmp_dir:
        # Write modified skeleton
        skel_dir = os.path.join(tmp_dir, *target_skeleton_paz_path.split('/')[:-1])
        os.makedirs(skel_dir, exist_ok=True)
        with open(os.path.join(skel_dir, paz_file), 'wb') as f:
            f.write(modified)

        # Optionally override CharacterScale in appearance XML
        if appearance_paz_path and character_scale is not None:
            _write_scaled_appearance(
                game_path, appearance_paz_path, character_scale, tmp_dir)

        # Pack
        pack_out = os.path.join(tmp_dir, 'output')
        os.makedirs(pack_out, exist_ok=True)
        crimson_rs.pack_mod.pack_mod(
            game_dir=game_path, mod_folder=tmp_dir,
            output_dir=pack_out, group_name=overlay_group)

        # Backup PAPGT
        papgt = gp / 'meta' / '0.papgt'
        backup = papgt.with_suffix(f'.papgt.rider_{overlay_group}_bak')
        if papgt.exists() and not backup.exists():
            shutil.copy2(papgt, backup)

        # Deploy
        dest = gp / overlay_group
        dest.mkdir(exist_ok=True)
        shutil.copyfile(os.path.join(pack_out, overlay_group, '0.paz'), dest / '0.paz')
        shutil.copyfile(os.path.join(pack_out, overlay_group, '0.pamt'), dest / '0.pamt')
        shutil.copyfile(os.path.join(pack_out, 'meta', '0.papgt'), papgt)

    return {
        'overlay_group': overlay_group,
        'skeleton': target_skeleton_paz_path,
        'rider_y': rider_y,
        'character_scale': character_scale,
        'paz_size': os.path.getsize(dest / '0.paz'),
        'backup': str(backup) if backup.exists() else None,
    }


def restore_overlay(game_path: str, overlay_group: str = "0062"):
    """Remove a rider bone overlay and restore original PAPGT."""
    gp = Path(game_path)

    # Restore PAPGT
    papgt = gp / 'meta' / '0.papgt'
    backup = papgt.with_suffix(f'.papgt.rider_{overlay_group}_bak')
    if backup.exists():
        shutil.copy2(backup, papgt)

    # Remove overlay group
    dest = gp / overlay_group
    if dest.exists():
        shutil.rmtree(dest)

    return True


# ── Known skeleton mappings ─────────────────────────────────────────────
# Maps character display name → (skeleton PAZ path, suggested rider_y, appearance PAZ path)
KNOWN_SKELETONS = {
    "Golden Star (Golemdragon)": {
        "skeleton": "character/model/2_mon/cd_m0004_00_dragon/cd_m0004_00_golemdragon/cd_m0004_00_golemdragon.pab",
        "appearance": "character/appearance/2_mon/cd_m0004_00_dragon/cd_m0004_00_golemdragon_0001/cd_m0004_00_golemdragon_0001_00000.app.xml",
        "rider_y": 8.0,
        "default_scale": 4.0,
        "suggested_scale": 2.0,
    },
    "Dragon (Blackstar)": {
        "skeleton": "character/model/2_mon/cd_m0004_00_dragon/cd_m0004_00_dragon.pab",
        "rider_y": 0.0,  # Already has B_Rider_01
        "note": "Already rideable — no injection needed",
    },
    "Wyvern": {
        "skeleton": "character/model/2_mon/cd_m0004_00_dragon/cd_m0004_00_wyvern/cd_m0004_00_wyvern.pab",
        "rider_y": 4.0,  # Estimate — needs testing
    },
    "Wolf/Dog": {
        "skeleton": "character/model/2_mon/cd_m0002_00_fourfeet/cd_m0002_00_dog/cd_m0011_00_dog.pab",
        "rider_y": 2.0,  # Estimate — needs testing
    },
    "Bear": {
        "skeleton": "character/model/2_mon/cd_m0001_00_twofeet/cd_m0001_00_bear/cd_m0001_00_bear.pab",
        "rider_y": 3.0,  # Estimate — needs testing
    },
    "T-Rex Dragon": {
        "skeleton": "character/model/2_mon/cd_m0004_00_dragon/cd_m0004_00_trexdragon/cd_m0004_00_trexdragon.pab",
        "rider_y": 6.0,  # Estimate — needs testing
    },
    "Carmabirdsaurus": {
        "skeleton": "character/model/2_mon/cd_m0004_00_dragon/cd_m0004_00_carmabirdsaurus/cd_m0004_00_carmabirdsaurus.pab",
        "rider_y": 4.0,  # Estimate — needs testing
    },
}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inject B_Rider_01 into a skeleton")
    parser.add_argument("--game", default=r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
    parser.add_argument("--skeleton", required=True, help="PAZ path to target .pab")
    parser.add_argument("--rider-y", type=float, default=8.0, help="Rider Y position")
    parser.add_argument("--group", default="0062", help="Overlay PAZ group")
    parser.add_argument("--restore", action="store_true", help="Remove overlay instead")
    args = parser.parse_args()

    if args.restore:
        restore_overlay(args.game, args.group)
        print(f"Restored (removed group {args.group})")
    else:
        result = make_rideable(args.game, args.skeleton,
                               rider_y=args.rider_y, overlay_group=args.group)
        print(f"Deployed to group {result['overlay_group']}:")
        print(f"  Skeleton: {result['skeleton']}")
        print(f"  Rider Y: {result['rider_y']}")
        print(f"  PAZ size: {result['paz_size']:,} bytes")
