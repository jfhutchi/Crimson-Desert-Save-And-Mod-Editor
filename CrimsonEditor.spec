from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()
PKG = ROOT / "crimson"

# The package carries its own runtime data (item names, quest tables, native
# parsers). Each app resolves those via dirname(__file__), so the tree is
# shipped with the same layout it has in the repo.
DATA_SUFFIXES = {".json", ".tsv", ".dll", ".pyd", ".pabgb", ".pabgh",
                 ".ico", ".png", ".webp", ".gz", ".txt", ".csv", ".zip"}
datas = [
    (str(path), str(path.parent.relative_to(ROOT)))
    for path in PKG.rglob("*")
    if path.is_file() and path.suffix.lower() in DATA_SUFFIXES
    and "__pycache__" not in path.parts
]

# Both editors import tab modules dynamically by name.
datas.append((str(ROOT / "app_icon.ico"), "."))

hiddenimports = (
    collect_submodules("crimson.save_editor")
    + collect_submodules("crimson.game_mods")
    + collect_submodules("crimson.common")
    + ["lz4", "lz4.block", "cryptography",
       "cryptography.hazmat.primitives.ciphers",
       "cryptography.hazmat.primitives.ciphers.algorithms",
       "crimson_rs", "crimson_rs.enums", "crimson_rs.create_pack",
       "crimson_rs.pack_mod", "crimson_rs.validate_game_dir"]
)

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT), str(PKG / "game_mods")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

splash = Splash(
    "splash.png",
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(150, 276),
    text_size=8,
    text_color="#C6A058",
    text_default="Starting...",
    always_on_top=True,
)

# onedir, not onefile: a onefile build re-extracts ~80MB to a temp directory
# on every launch, which is most of the startup wait. onedir starts straight
# from disk; ship the folder (or a zip of it).
exe = EXE(
    pyz,
    a.scripts,
    splash,
    [],
    exclude_binaries=True,
    name="CrimsonEditor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon="app_icon.ico",
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    splash.binaries,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CrimsonEditor",
)
