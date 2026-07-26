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

from PySide6.QtCore import Qt  # noqa: E402

from crimson.common.crimson_shell import (  # noqa: E402
    ShellCommand,
    ShellDestination,
    ShellRoute,
    install_crimson_application_shell,
    normalize_legacy_button_styles,
)

WORKSPACES = (
    ("save_editor", "Save", "Edit a character save file"),
    ("game_mods", "Game Files", "Patch installed game data"),
)

# Both editors name their sections the same way (SAVE, MOUNTS, INVENTORY,
# WORLD), so those merge into one section each. Editing a save and patching
# the installed game are still different operations, so every game-side route
# carries a GAME badge rather than getting its own near-duplicate section.
GAME_ROUTE_BADGE = "game"

# Two routes genuinely differ but share a name across the editors: one edits
# the save, the other patches installed game data. Say which is which.
# The mod editor grouped some pages oddly for a combined window: its item and
# character editors sat under SAVE, and Field & Regions under MOUNTS. File
# them where a reader would look.
ROUTE_SECTIONS = {
    "Item Buffs": "INVENTORY",
    "Game Item Table": "INVENTORY",
    "Mercenaries & Pets": "MOUNTS",
    "Field & Regions": "WORLD",
}

ROUTE_RENAMES = {
    ("save_editor", "Blackstar"): "Blackstar Unlock",
    ("game_mods", "Blackstar"): "Blackstar Timers",
    ("game_mods", "Item Database"): "Game Item Table",
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
        self._docks: list[object] = []
        self._commands: list[ShellCommand] = []

        merged: dict[str, ShellDestination] = {}
        for module_attr, label, caption in WORKSPACES:
            _splash(f"Loading {label}...")
            window, groups = self._absorb(module_attr, label)
            if window is None:
                continue
            self._sources.append(window)
            is_game = module_attr == "game_mods"
            for group in groups:
                name = group.label.upper()
                routes = tuple(
                    replace(
                        route,
                        label=ROUTE_RENAMES.get((module_attr, route.label), route.label),
                        badge=GAME_ROUTE_BADGE if is_game else route.badge,
                    )
                    for route in group.routes
                )
                for route in routes:
                    section = ROUTE_SECTIONS.get(route.label, name)
                    existing = merged.get(section)
                    if existing is None:
                        merged[section] = replace(
                            group, label=section, routes=(route,),
                            caption=group.caption or caption,
                        )
                    else:
                        merged[section] = replace(
                            existing, routes=existing.routes + (route,)
                        )

        if not merged:
            raise RuntimeError("neither editor could be loaded")
        destinations = self._deduplicate(merged.values())

        self._merge_menus()

        _splash("Preparing the workspace...")
        self._shell = install_crimson_application_shell(
            self,
            product="CRIMSON",
            router_tabs=self._router,
            destinations=tuple(destinations),
            commands=self._commands,
            docks=self._docks,
        )

        # Pages built lazily - the Stacker Tool and the rest of the mod
        # editor's tabs - miss the one-shot restyle the shell does at install
        # time, and keep their hardcoded green/blue/red buttons. Re-run it
        # whenever a page is shown; it is a no-op once a page is normalized.
        self._router.currentChanged.connect(self._normalize_page)
        seen_sections = set()
        for destination in destinations:
            for route in destination.routes:
                section = route.section_tabs
                if section is None or id(section) in seen_sections:
                    continue
                seen_sections.add(id(section))
                section.currentChanged.connect(
                    lambda _i, tabs=section: self._normalize_widget(tabs.currentWidget())
                )
        self._normalize_page(self._router.currentIndex())

    def _normalize_page(self, index: int) -> None:
        self._normalize_widget(self._router.widget(index))

    @staticmethod
    def _normalize_widget(page) -> None:
        if page is not None:
            normalize_legacy_button_styles(page)

    @staticmethod
    def _deduplicate(groups) -> list[ShellDestination]:
        """Drop routes that lead somewhere already listed.

        Both editors alias the same page under several sections - the mod
        editor reaches its patches page as both "Blackstar" and "Game
        Patches", and the save editor lists Backup & Restore under two
        sections. Identical entries are dropped outright; a page reachable
        under a different name stays, because the second name is the point.
        """
        seen_named: set[tuple] = set()
        result: list[ShellDestination] = []
        for group in groups:
            seen_here: set[tuple] = set()
            routes = []
            for route in group.routes:
                target = (route.primary_index, route.section_index)
                named = (route.label.casefold(),) + target
                if target in seen_here or named in seen_named:
                    continue
                seen_here.add(target)
                seen_named.add(named)
                routes.append(route)
            if routes:
                result.append(replace(group, routes=tuple(routes)))
        return result

    def _merge_menus(self) -> None:
        """Lift both editors' menus onto the shared window.

        Each editor built its menus on its own window, which is now hidden, so
        File > Open, Save, Undo and the rest would be unreachable. Menus with
        the same name are merged into one, with each workspace's entries under
        a heading, and their shortcuts start working again because the actions
        now belong to the visible window.
        """
        bar = self.menuBar()
        merged: dict[str, object] = {}
        for window, (_attr, workspace, _caption) in zip(self._sources, WORKSPACES):
            for action in list(window.menuBar().actions()):
                menu = action.menu()
                if menu is None:
                    continue
                name = action.text()
                target = merged.get(name)
                if target is None:
                    bar.addMenu(menu)
                    merged[name] = menu
                    self._label_section(menu, workspace, first=True)
                    continue
                target.addSeparator()
                self._label_section(target, workspace)
                target.addActions(menu.actions())
        self.addActions(bar.actions())

    @staticmethod
    def _label_section(menu, workspace: str, *, first: bool = False) -> None:
        heading = menu.addSection(workspace.upper()) if not first else None
        if heading is None and first:
            # Put the first workspace's heading above its existing entries.
            actions = menu.actions()
            marker = menu.addSection(workspace.upper())
            if actions:
                menu.removeAction(marker)
                menu.insertAction(actions[0], marker)

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
            # Both editors defaulted to "editor_config.json" beside the
            # executable. Sharing one process means sharing that directory, so
            # each workspace gets its own file instead of clobbering the other.
            MainWindow._CONFIG_FILE = f"config_{module_attr}.json"
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
        for attr, command_label in (("_save_dock", "SAVES"), ("_pack_dock", "PACKS")):
            dock = getattr(window, attr, None)
            if dock is None:
                continue
            self.addDockWidget(Qt.LeftDockWidgetArea, dock)
            dock.hide()
            self._docks.append(dock)
            toggle = getattr(window, f"_toggle{attr.replace('_dock', '')}_sidebar", None)
            if toggle is None or any(c.label == command_label for c in self._commands):
                continue
            self._commands.append(
                ShellCommand(command_label, toggle, f"Open the {command_label.lower()} browser")
            )

        window.hide()
        return window, groups


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    app = QApplication.instance() or QApplication(sys.argv)

    # The taskbar shows the window icon, not the one compiled into the exe.
    icon_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    icon_file = icon_path / "app_icon.ico"
    if icon_file.is_file():
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(str(icon_file)))

    # Adopt any icon set left beside an older build before the workspaces ask
    # for icons, so a reinstall never re-downloads what is already on disk.
    try:
        from crimson.common.icon_cache import IconCache

        IconCache().migrate_legacy_icons()
    except Exception:
        log.debug("icon migration skipped", exc_info=True)

    window = CrimsonWindow()
    window.show()
    _splash_close()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
