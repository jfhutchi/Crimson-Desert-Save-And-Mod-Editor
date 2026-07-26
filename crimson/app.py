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

        destinations = []
        for module_attr, label, caption in WORKSPACES:
            window, routes = self._absorb(module_attr, label)
            if window is None:
                continue
            self._sources.append(window)
            destinations.append(
                ShellDestination(label=label, routes=tuple(routes), caption=caption)
            )

        if not destinations:
            raise RuntimeError("neither editor could be loaded")

        self._shell = install_crimson_application_shell(
            self,
            product="CRIMSON",
            router_tabs=self._router,
            destinations=tuple(destinations),
        )

    def _absorb(self, module_attr: str, label: str):
        """Build one editor hidden and move its pages into the shared router."""
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

        routes = []
        while tabs.count():
            page = tabs.widget(0)
            name = tabs.tabText(0)
            tabs.removeTab(0)
            index = self._router.addTab(page, name)
            routes.append(ShellRoute(label=name, primary_index=index))
        window.hide()
        return window, routes


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    app = QApplication.instance() or QApplication(sys.argv)
    window = CrimsonWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
