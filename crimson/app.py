"""One window hosting both editors.

The save editor and the mod editor were separate applications with separate
windows. Their pages are plain widgets, so this builds each app's window
hidden, moves every page into a single router, and installs one Crimson shell
over the result. Each hidden window stays alive because it owns the signal
handlers its pages are wired to.
"""
from __future__ import annotations

import logging
import socket
import sys
from dataclasses import replace
from pathlib import Path

# urlretrieve takes no timeout argument, so a stalled server would hang the
# window forever. One default covers every download path in the app.
socket.setdefaulttimeout(30)

log = logging.getLogger(__name__)


def _prepare_path() -> None:
    """Make the package importable and expose the bundled native extensions."""
    root = Path(__file__).resolve().parents[1]
    for entry in (root, root / "crimson" / "game_mods"):
        value = str(entry)
        if value not in sys.path:
            sys.path.insert(0, value)


_prepare_path()

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget  # noqa: E402

from crimson.common.crimson_shell import (  # noqa: E402
    ShellDestination,
    ShellRoute,
    install_crimson_application_shell,
)

WORKSPACES = (
    ("save_editor", "Save", "Edit a character save file"),
    ("game_mods", "Game Files", "Patch installed game data"),
)

# Both editors name their sections the same way (SAVE, MOUNTS, INVENTORY,
# WORLD). Editing your save and patching the installed game are different
# operations with different risk, so the game-side sections say so.
GAME_SECTION_NAMES = {
    "SAVE": "GAME DATA",
    "MOUNTS": "GAME MOUNTS",
    "INVENTORY": "GAME ITEMS",
    "WORLD": "GAME WORLD",
}


def _splash(text: str) -> None:
    """Report startup progress on the PyInstaller splash, if there is one."""
    try:
        import pyi_splash  # only present in a frozen build
        pyi_splash.update_text(text)
    except Exception:
        pass


def _splash_close() -> None:
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass


class CrimsonWindow(QMainWindow):
    """The unified application window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Crimson Desert — Save & Mod Editor")
        self.resize(1500, 900)
        self.setMinimumSize(900, 560)

        self._router = QTabWidget()
        self._router.tabBar().hide()
        self._sources: list[QMainWindow] = []

        destinations: list[ShellDestination] = []
        seen: set[str] = set()
        for module_attr, label, caption in WORKSPACES:
            _splash(f"Loading {label}...")
            window, groups = self._absorb(module_attr, label)
            if window is None:
                continue
            self._sources.append(window)
            for group in groups:
                name = group.label
                if module_attr == "game_mods":
                    name = GAME_SECTION_NAMES.get(name.upper(), name)
                if name in seen:
                    name = f"{name} ({label})"
                seen.add(name)
                destinations.append(
                    replace(group, label=name, caption=group.caption or caption)
                )

        if not destinations:
            raise RuntimeError("neither editor could be loaded")

        _splash("Preparing the workspace...")
        self._shell = install_crimson_application_shell(
            self,
            product="CRIMSON",
            router_tabs=self._router,
            destinations=tuple(destinations),
        )

    def _absorb(self, module_attr: str, label: str):
        """Build one editor hidden and move its pages into the shared router.

        Each editor already describes its own navigation - labels, captions,
        icons, game art and sub-tab targets - so that metadata is reused and
        only the page indices are remapped onto the shared router.
        """
        try:
            if module_attr == "save_editor":
                from crimson.save_editor.gui import MainWindow
            else:
                from crimson.game_mods.gui.main_window import MainWindow
            window = MainWindow()
        except Exception:
            log.exception("%s workspace failed to load", label)
            return None, []

        tabs = getattr(window, "_real_tabs", None) or getattr(window, "_tabs", None)
        if tabs is None:
            log.error("%s workspace exposes no page router", label)
            return None, []

        try:
            native = tuple(window._shell_destinations())
        except Exception:
            log.exception("%s workspace has no navigation metadata", label)
            native = ()

        # Capture page order before moving, so old indices can be remapped.
        pages = [(tabs.widget(i), tabs.tabText(i)) for i in range(tabs.count())]
        remap: dict[int, int] = {}
        for old_index, (page, name) in enumerate(pages):
            remap[old_index] = self._router.addTab(page, name)
        while tabs.count():
            tabs.removeTab(0)

        groups: list[ShellDestination] = []
        for destination in native:
            routes = [
                replace(route, primary_index=remap[route.primary_index])
                for route in destination.routes
                if route.primary_index in remap
            ]
            if routes:
                groups.append(replace(destination, routes=tuple(routes)))
        if not groups:
            groups = [
                ShellDestination(
                    label=label,
                    routes=tuple(
                        ShellRoute(label=name, primary_index=remap[i])
                        for i, (_page, name) in enumerate(pages)
                    ),
                )
            ]
        window.hide()
        return window, groups


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    app = QApplication.instance() or QApplication(sys.argv)
    window = CrimsonWindow()
    window.show()
    _splash_close()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
