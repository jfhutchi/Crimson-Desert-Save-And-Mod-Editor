"""Bundle CrimsonGameMods + DMM into a unified distribution.

Usage:
    python bundle_unified.py

Prerequisites:
    1. CrimsonGameMods built: python -m PyInstaller CrimsonSaveEditor.spec --noconfirm
       → dist/CrimsonSaveEditor.exe (or dist/CrimsonGameMods.exe)
    2. DMM built: cd DMMLoader && npx tauri build
       → DMMLoader/src-tauri/target/release/definitive-mod-manager.exe

This script:
    1. Copies the CrimsonGameMods exe + resources to dist/CrimsonDesktopSuite/
    2. Copies the DMM exe + asi_loader.dll to dist/CrimsonDesktopSuite/dmm/
    3. Creates a shared config stub
    4. Creates a README for the unified package
    5. Optionally zips the result for distribution
"""
import os
import sys
import json
import shutil
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DIST_DIR = SCRIPT_DIR / "dist"
SUITE_DIR = DIST_DIR / "CrimsonDesktopSuite"
DMM_SUBDIR = SUITE_DIR / "dmm"

# Source locations
CGM_EXE_CANDIDATES = [
    DIST_DIR / "CrimsonSaveEditor.exe",
    DIST_DIR / "CrimsonGameMods.exe",
]
DMM_EXE = SCRIPT_DIR / "DMMLoader" / "src-tauri" / "target" / "release" / "definitive-mod-manager.exe"
DMM_ASI = SCRIPT_DIR / "DMMLoader" / "src-tauri" / "asi_loader.dll"

# Extra resources to bundle alongside the main exe
CGM_RESOURCES = [
    "splash.png",
    "icon.ico",
    "editor_version.json",
    "localization",
    "knowledge_packs",
]


def find_cgm_exe() -> Path | None:
    for p in CGM_EXE_CANDIDATES:
        if p.is_file():
            return p
    return None


def bundle():
    print("=== Crimson Desktop Suite — Unified Bundle ===\n")

    # Check prerequisites
    cgm_exe = find_cgm_exe()
    if not cgm_exe:
        print(f"ERROR: CrimsonGameMods exe not found in {DIST_DIR}/")
        print("Build it first: python -m PyInstaller CrimsonSaveEditor.spec --noconfirm")
        sys.exit(1)
    print(f"  CGM exe: {cgm_exe} ({cgm_exe.stat().st_size / 1024 / 1024:.1f} MB)")

    dmm_found = DMM_EXE.is_file()
    if dmm_found:
        print(f"  DMM exe: {DMM_EXE} ({DMM_EXE.stat().st_size / 1024 / 1024:.1f} MB)")
    else:
        print(f"  DMM exe: NOT FOUND at {DMM_EXE}")
        print("  (Will bundle without DMM — users can add it later)")

    # Clean and create output directory
    if SUITE_DIR.is_dir():
        shutil.rmtree(SUITE_DIR)
    SUITE_DIR.mkdir(parents=True)
    DMM_SUBDIR.mkdir()

    # Copy CrimsonGameMods
    dest_cgm = SUITE_DIR / "CrimsonGameMods.exe"
    print(f"\n  Copying CGM exe...")
    shutil.copy2(cgm_exe, dest_cgm)

    # Copy resources
    for res_name in CGM_RESOURCES:
        src = SCRIPT_DIR / res_name
        if src.is_file():
            shutil.copy2(src, SUITE_DIR / res_name)
            print(f"  + {res_name}")
        elif src.is_dir():
            shutil.copytree(src, SUITE_DIR / res_name)
            print(f"  + {res_name}/ (directory)")

    # Copy DMM
    if dmm_found:
        print(f"\n  Copying DMM exe...")
        shutil.copy2(DMM_EXE, DMM_SUBDIR / "definitive-mod-manager.exe")
        if DMM_ASI.is_file():
            shutil.copy2(DMM_ASI, DMM_SUBDIR / "asi_loader.dll")
            print(f"  + asi_loader.dll")
        # Create mods directory
        (DMM_SUBDIR / "mods").mkdir()
        print(f"  + mods/ (directory)")

    # Create shared config stub
    config = {
        "game_install_path": "",
        "dmm_exe_path": str(DMM_SUBDIR / "definitive-mod-manager.exe") if dmm_found else "",
    }
    with open(SUITE_DIR / "editor_config.json", "w") as f:
        json.dump(config, f, indent=2)
    print(f"  + editor_config.json")

    # Create README
    readme_content = """# Crimson Desktop Suite — CrimsonGameMods + Definitive Mod Manager

## What's Inside

- **CrimsonGameMods.exe** — Game data editor (ItemBuffs, Stores, SkillTree,
  DropSets, Spawns, BagSpace, Stacker Tool, Field Editor)
- **dmm/** — Definitive Mod Manager (DLL/ASI plugins, textures, audio,
  language packs, ReShade, JSON byte-patch mods)

## Quick Start

1. Run `CrimsonGameMods.exe`
2. Set your game path (it auto-detects Steam installs)
3. Use the Game Mods tabs for data editing (stats, stores, skills, etc.)
4. Use the **Mod Loader** tab to manage third-party mods via DMM
5. Both tools share the same game path and coordinate overlays automatically

## How It Works

Both tools write PAZ overlay files to your game directory. They use
different overlay groups so they don't conflict:

- CrimsonGameMods uses groups: 0058-0063
- DMM uses groups: dmmsa, dmmgen, dmmequ, dmmlang

The game loads ALL overlays. Both tools preserve each other's PAPGT entries.

## Mod Types

| Mod Type | Handled By |
|----------|-----------|
| ItemInfo stats/buffs | CrimsonGameMods (ItemBuffs tab) |
| Store editing | CrimsonGameMods (Stores tab) |
| Skill tree swaps | CrimsonGameMods (SkillTree tab) |
| Drop rate editing | CrimsonGameMods (DropSets tab) |
| Multi-mod merging | CrimsonGameMods (Stacker Tool) |
| Any PABGB editing | CrimsonGameMods (FieldEdit tab) |
| DLL / ASI plugins | DMM (via Mod Loader tab) |
| Texture mods (.dds) | DMM (via Mod Loader tab) |
| Audio mods (.wem) | DMM (via Mod Loader tab) |
| Language / fonts | DMM (via Mod Loader tab) |
| ReShade presets | DMM (via Mod Loader tab) |
| JSON byte patches | Both (DMM loads, Stacker converts to semantic) |
"""
    with open(SUITE_DIR / "README.txt", "w") as f:
        f.write(readme_content)
    print(f"  + README.txt")

    # Calculate total size
    total = sum(f.stat().st_size for f in SUITE_DIR.rglob("*") if f.is_file())
    print(f"\n  Total size: {total / 1024 / 1024:.1f} MB")

    # Create zip
    zip_name = "CrimsonDesktopSuite"
    if dmm_found:
        zip_name += "_full"
    else:
        zip_name += "_cgm_only"
    zip_path = DIST_DIR / f"{zip_name}.zip"

    print(f"\n  Creating {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in sorted(SUITE_DIR.rglob("*")):
            if fp.is_file():
                arc_name = fp.relative_to(DIST_DIR)
                zf.write(fp, arc_name)

    zip_size = zip_path.stat().st_size
    print(f"  Zip size: {zip_size / 1024 / 1024:.1f} MB")

    print(f"\n=== Done! ===")
    print(f"  Output: {SUITE_DIR}")
    print(f"  Zip:    {zip_path}")
    if not dmm_found:
        print(f"\n  NOTE: DMM was not found. To include it:")
        print(f"    cd DMMLoader && npx tauri build")
        print(f"    Then re-run this script.")


if __name__ == "__main__":
    bundle()
