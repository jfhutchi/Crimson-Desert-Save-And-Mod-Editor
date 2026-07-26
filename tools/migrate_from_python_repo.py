"""Import both editors from the two-app repo into one namespaced package.

The two applications each ship top-level modules with the same names
(``models``, ``save_crypto``, ``item_scanner``, ``parc_inserter3`` ...) whose
contents differ, so they cannot coexist in one process on ``sys.path``. This
script copies each app into its own subpackage and rewrites sibling imports to
package-relative form, which is what lets a single window host both.

Re-runnable: it wipes and recreates the generated packages.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1]
PKG = TARGET / "crimson"

# (source dir, destination subpackage)
APPS = [
    ("CrimsonSaveEditor", "save_editor"),
    ("CrimsonGameMods", "game_mods"),
    ("crimson_common", "common"),
]

SKIP_DIRS = {"__pycache__", "dist", "build", "icons_local", "icons_mercenary",
             ".pytest_cache", "_legacy", "testing"}
SKIP_FILES = {"main.py"}  # each app's entry point is replaced by crimson/app.py


def top_level_modules(app_dir: Path) -> set[str]:
    """Names importable as `import X` from inside that app."""
    names = {p.stem for p in app_dir.glob("*.py") if p.stem != "__init__"}
    names |= {p.name for p in app_dir.iterdir()
              if p.is_dir() and (p / "__init__.py").exists()
              and p.name not in SKIP_DIRS}
    return names


def rewrite_imports(text: str, siblings: set[str], subpkg: str) -> tuple[str, int]:
    """Point `import X` / `from X import` at the sibling inside this package."""
    count = 0
    names = "|".join(sorted(re.escape(n) for n in siblings))
    if not names:
        return text, 0

    def from_repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f"{m.group('indent')}from crimson.{subpkg}.{m.group('mod')} import"

    def import_repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        alias = m.group("alias") or m.group("mod").split(".")[-1]
        return (f"{m.group('indent')}from crimson.{subpkg} import "
                f"{m.group('mod').split('.')[0]} as {alias}")

    text = re.sub(
        rf"(?P<indent>^[ \t]*)from (?P<mod>(?:{names})(?:\.[A-Za-z0-9_.]+)?) import",
        from_repl, text, flags=re.MULTILINE)
    text = re.sub(
        rf"(?P<indent>^[ \t]*)import (?P<mod>(?:{names})(?:\.[A-Za-z0-9_.]+)?)"
        rf"(?: as (?P<alias>[A-Za-z0-9_]+))?[ \t]*$",
        import_repl, text, flags=re.MULTILINE)
    return text, count


def copy_app(source_root: Path, app_dir_name: str, subpkg: str) -> tuple[int, int]:
    src = source_root / app_dir_name
    dst = PKG / subpkg
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)

    siblings = top_level_modules(src)
    files = rewrites = 0
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.is_dir():
            (dst / rel).mkdir(parents=True, exist_ok=True)
            continue
        if path.suffix != ".py":
            continue
        if rel.name in SKIP_FILES and len(rel.parts) == 1:
            continue
        text = path.read_text(encoding="utf-8-sig")
        text, n = rewrite_imports(text, siblings, subpkg)
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        (dst / rel).write_text(text, encoding="utf-8")
        files += 1
        rewrites += n

    init = dst / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    return files, rewrites


# Runtime data resolves via dirname(__file__), so it rides along with its
# package and needs no code change. Icon sets are excluded: they are large and
# the app downloads them on demand.
DATA_SUFFIXES = {".json", ".tsv", ".dll", ".pyd", ".pabgb", ".pabgh", ".ico",
                 ".png", ".webp", ".zip", ".txt", ".db", ".gz", ".csv"}
DATA_DIRS = {"locale", "language", "knowledge_packs", "vanilla_tables", "data",
             "crimson_rs", "dmm_parser", "game_baselines", "quest_packs",
             "dropset_packs", "desktopeditor"}
# Generated or redundant payloads: mod packs are user output, and the app
# reads the gzipped database, not the expanded one.
SKIP_DATA_FILES = {"crimson_data.db"}


def copy_data(source_root: Path, app_dir_name: str, subpkg: str) -> int:
    src = source_root / app_dir_name
    dst = PKG / subpkg
    copied = 0
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DATA_SUFFIXES:
            continue
        if path.name in SKIP_DATA_FILES:
            continue
        rel = path.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        # Only top-level data files, or files inside a known data directory.
        if len(rel.parts) > 1 and rel.parts[0] not in DATA_DIRS:
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path,
                    help="path to the CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS repo")
    args = ap.parse_args()
    source = args.source.resolve()
    if not (source / "CrimsonSaveEditor").is_dir():
        print(f"not a source repo: {source}", file=sys.stderr)
        return 1

    PKG.mkdir(parents=True, exist_ok=True)
    (PKG / "__init__.py").write_text(
        '"""Crimson Desert Save And Mod Editor."""\n', encoding="utf-8")

    total_files = total_rewrites = total_data = 0
    for app_dir, subpkg in APPS:
        files, rewrites = copy_app(source, app_dir, subpkg)
        data = copy_data(source, app_dir, subpkg)
        print(f"{app_dir:>20} -> crimson/{subpkg:<12} {files:4d} py, "
              f"{rewrites:4d} imports, {data:4d} data files")
        total_files += files
        total_rewrites += rewrites
        total_data += data
    print(f"{'total':>20}    {total_files:4d} py, {total_rewrites:4d} imports, "
          f"{total_data:4d} data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
