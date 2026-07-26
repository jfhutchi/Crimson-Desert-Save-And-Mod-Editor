"""Unified Launcher — CrimsonGameMods + DMM in one package.

This launcher manages the lifecycle of both tools:
1. CrimsonGameMods (PySide6) — the primary application
2. DMM (Tauri/Rust) — launched on demand from the Mod Loader tab

When distributed as a unified package, both executables live in the same
directory. The launcher:
 - Sets up shared config directory
 - Ensures DMM's config.json points to the same game path
 - Launches CrimsonGameMods as the primary window
 - Provides DMM launch capability to the Mod Loader tab

Build instructions:
  1. Build CrimsonGameMods: python -m PyInstaller CrimsonSaveEditor.spec --noconfirm
  2. Build DMM: cd DMMLoader && npx tauri build
  3. Copy DMM exe into dist/dmm/ subfolder
  4. Run bundle_unified.py to create the unified package

For development, this launcher just starts the main app normally.
"""
import os
import sys
import json
import shutil
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _get_app_dir() -> Path:
    """Root directory of the unified package."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _get_dmm_dir() -> Path:
    """DMM subdirectory within the unified package."""
    return _get_app_dir() / "dmm"


def _find_dmm_exe() -> str | None:
    """Find DMM executable in the bundled location."""
    dmm_dir = _get_dmm_dir()
    candidates = [
        dmm_dir / "definitive-mod-manager.exe",
        dmm_dir / "Definitive Mod Manager.exe",
        _get_app_dir() / "definitive-mod-manager.exe",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


def _ensure_shared_config() -> None:
    """Create shared config directory and sync game path between tools."""
    app_dir = _get_app_dir()
    config_path = app_dir / "editor_config.json"

    cgm_config = {}
    if config_path.is_file():
        try:
            with open(config_path, "r") as f:
                cgm_config = json.load(f)
        except Exception:
            pass

    game_path = cgm_config.get("game_install_path", "")

    # Auto-set DMM path if bundled
    dmm_exe = _find_dmm_exe()
    if dmm_exe and not cgm_config.get("dmm_exe_path"):
        cgm_config["dmm_exe_path"] = dmm_exe
        try:
            with open(config_path, "w") as f:
                json.dump(cgm_config, f, indent=2)
        except Exception:
            pass

    # Sync game path to DMM config if both exist
    if dmm_exe and game_path:
        dmm_config_path = Path(dmm_exe).parent / "config.json"
        try:
            dmm_cfg = {}
            if dmm_config_path.is_file():
                with open(dmm_config_path, "r") as f:
                    dmm_cfg = json.load(f)
            if dmm_cfg.get("gamePath") != game_path:
                dmm_cfg["gamePath"] = game_path
                with open(dmm_config_path, "w") as f:
                    json.dump(dmm_cfg, f, indent=2)
        except Exception:
            pass

    # Ensure DMM mods directory exists
    if dmm_exe:
        mods_dir = Path(dmm_exe).parent / "mods"
        mods_dir.mkdir(exist_ok=True)


def main():
    _ensure_shared_config()

    # Import and run the main CrimsonGameMods application
    if getattr(sys, "frozen", False):
        # When frozen, we ARE the main app — just run it
        from crimson.game_mods.gui.main_window import CrimsonDesktopEditor
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QIcon
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "CrimsonGameMods.unified")

        app = QApplication(sys.argv)
        app.setApplicationName("Crimson Desert - Game Mods & Mod Loader")

        icon_path = _get_app_dir() / "icon.ico"
        if icon_path.is_file():
            app.setWindowIcon(QIcon(str(icon_path)))

        window = CrimsonDesktopEditor()
        window.show()
        sys.exit(app.exec())
    else:
        # Development mode — just import and run the normal entry point
        os.system(f'python "{_get_app_dir() / "main.py"}"')


if __name__ == "__main__":
    main()
