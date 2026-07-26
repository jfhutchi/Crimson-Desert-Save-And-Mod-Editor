from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .crimson_icons import game_art_icon, inferred_symbol, symbol_icon


_BRIGHT_BUTTON_BACKGROUND = re.compile(
    r"background(?:-color)?\s*:\s*(?:#[0-9a-f]{3,8}|rgba?\s*\()",
    re.IGNORECASE,
)
_PRIMARY_ACTION_TEXT = (
    "apply to game",
    "apply preset",
    "save edit",
    "save changes",
    "write save",
)
_DANGER_ACTION_TEXT = ("delete", "remove", "wipe", "destroy")
_LEGACY_ACTION_LABELS = {
    "transmog (armor / weapon visual swap)": "Transmog",
    "create custom item": "Create Item",
    "add custom item to save": "Add to Save",
    "import mod folder": "Import",
    "export as field json v3": "Export Field JSON",
}


def normalize_legacy_button_styles(root: QWidget) -> None:
    """Fold legacy rainbow button palettes into the shared action hierarchy."""
    for button in root.findChildren(QAbstractButton):
        if button.objectName() in {
            "destinationButton",
            "routeButton",
            "shellUtilityButton",
        }:
            continue
        style = button.styleSheet()
        legacy_accent = button.objectName() == "accentBtn"
        if not legacy_accent and not _BRIGHT_BUTTON_BACKGROUND.search(style):
            continue

        label = button.text().strip().casefold()
        for legacy_label, concise_label in _LEGACY_ACTION_LABELS.items():
            if legacy_label in label:
                button.setText(concise_label)
                label = concise_label.casefold()
                break
        button.setStyleSheet("")
        if legacy_accent:
            button.setObjectName("legacyAction")
        if any(token in label for token in _DANGER_ACTION_TEXT):
            button.setProperty("dangerAction", True)
        elif any(token in label for token in _PRIMARY_ACTION_TEXT):
            button.setProperty("primaryAction", True)
        else:
            button.setProperty("quietAction", True)
        button.setProperty("legacyChromeNormalized", True)
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()


@dataclass(frozen=True)
class ShellRoute:
    """One contextual navigation item backed by the existing tab routers."""

    label: str
    primary_index: int
    section_tabs: QTabWidget | None = None
    section_index: int = 0
    description: str = ""
    icon_name: str = ""
    game_art_id: int | None = None
    badge: str = ""
    immersive: bool = False


@dataclass(frozen=True)
class ShellDestination:
    """A top-level destination and the routes shown in its left navigation."""

    label: str
    routes: tuple[ShellRoute, ...]
    caption: str = ""
    icon_name: str = ""


@dataclass(frozen=True)
class ShellCommand:
    """A quiet utility command in the upper-right command cluster."""

    label: str
    callback: Callable[[], None]
    tooltip: str = ""
    icon_name: str = ""


class CrimsonRouteButton(QPushButton):
    """A registry row that can combine real game art with quiet metadata."""

    def __init__(self, route: ShellRoute, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("routeButton")
        self.setText(route.label)
        self.badge = route.badge
        self.uses_game_art = route.game_art_id is not None
        icon = (
            game_art_icon(route.game_art_id, route.icon_name or "mounts")
            if route.game_art_id is not None
            else symbol_icon(route.icon_name or inferred_symbol(route.label))
        )
        self.setIcon(icon)
        self.setIconSize(QSize(34, 34) if self.uses_game_art else QSize(20, 20))
        self.setMinimumHeight(54 if self.uses_game_art else 46)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 10, 4)
        layout.setSpacing(10)
        halo = QFrame(self)
        halo.setObjectName("routeArtHalo")
        halo.setFixedSize(38, 38)
        halo_layout = QHBoxLayout(halo)
        halo_layout.setContentsMargins(2, 2, 2, 2)
        art = QLabel(halo)
        art.setObjectName("routeArt")
        art.setAlignment(Qt.AlignCenter)
        art.setPixmap(icon.pixmap(self.iconSize()))
        art.setAttribute(Qt.WA_TransparentForMouseEvents)
        halo_layout.addWidget(art)
        halo.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(halo)

        title = QLabel(route.label, self)
        title.setObjectName("routeButtonTitle")
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(title, 1)
        if route.badge:
            badge = QLabel(route.badge.upper(), self)
            badge.setObjectName("routeButtonBadge")
            badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            layout.addWidget(badge)

    def paintEvent(self, _event) -> None:
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        option.icon = QIcon()
        painter.drawControl(QStyle.CE_PushButton, option)


class CrimsonApplicationShell(QWidget):
    """Editorial application frame that keeps the legacy tabs as hidden routers."""

    def __init__(
        self,
        *,
        product: str,
        router_tabs: QTabWidget,
        destinations: Sequence[ShellDestination],
        menu_bar: QMenuBar | None = None,
        context_widget: QWidget | None = None,
        commands: Sequence[ShellCommand] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not destinations:
            raise ValueError("The application shell requires at least one destination")
        if any(not destination.routes for destination in destinations):
            raise ValueError("Every shell destination requires at least one route")

        self.setObjectName("crimsonApplicationShell")
        self._router_tabs = router_tabs
        self._destinations = tuple(destinations)
        self._menu_bar = menu_bar
        self._context_widget = context_widget
        self._active_destination = -1
        self._active_route = -1
        self._routing = False
        self.destination_buttons: list[QToolButton] = []
        self.route_buttons: list[CrimsonRouteButton] = []

        self._hide_legacy_navigation()
        self._build_layout(product, commands)
        self.activate_destination(0)
        self._connect_router_sync()

    def _hide_legacy_navigation(self) -> None:
        self._router_tabs.tabBar().hide()
        seen: set[int] = set()
        for destination in self._destinations:
            for route in destination.routes:
                if route.section_tabs is None or id(route.section_tabs) in seen:
                    continue
                route.section_tabs.tabBar().hide()
                seen.add(id(route.section_tabs))
        if self._menu_bar is not None:
            self._menu_bar.hide()
        if self._context_widget is not None:
            self._context_widget.hide()

    def _build_layout(
        self,
        product: str,
        commands: Sequence[ShellCommand],
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(self)
        header.setObjectName("commandHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(34, 0, 24, 0)
        header_layout.setSpacing(18)

        brand = QWidget(header)
        brand.setObjectName("brandBlock")
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 11, 0, 9)
        brand_layout.setSpacing(0)
        brand_mark = QLabel(
            '<span style="color:#B63A32">CRIMSON</span> DESERT /',
            brand,
        )
        brand_mark.setObjectName("brandMark")
        brand_mark.setTextFormat(Qt.RichText)
        brand_mark.setAccessibleName("Crimson Desert Tools")
        brand_subtitle = QLabel("TOOLS", brand)
        brand_subtitle.setObjectName("brandSubtitle")
        brand_layout.addWidget(brand_mark)
        brand_layout.addWidget(brand_subtitle)
        header_layout.addWidget(brand, 0)

        header_layout.addStretch(1)
        nav = QWidget(header)
        nav.setObjectName("destinationNavigation")
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(24)
        self._destination_group = QButtonGroup(self)
        self._destination_group.setExclusive(True)
        for index, destination in enumerate(self._destinations):
            button = QToolButton(nav)
            button.setObjectName("destinationButton")
            button.setText(destination.label.upper())
            button.setIcon(
                symbol_icon(destination.icon_name or inferred_symbol(destination.label))
            )
            button.setIconSize(QSize(18, 18))
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName(f"Open {destination.label}")
            button.clicked.connect(
                lambda checked=False, destination_index=index: (
                    self.activate_destination(destination_index)
                )
            )
            self._destination_group.addButton(button, index)
            self.destination_buttons.append(button)
            nav_layout.addWidget(button)
        header_layout.addWidget(nav, 0)
        header_layout.addStretch(1)

        utility = QWidget(header)
        utility.setObjectName("shellUtilities")
        utility_layout = QHBoxLayout(utility)
        utility_layout.setContentsMargins(0, 0, 0, 0)
        utility_layout.setSpacing(10)
        if self._menu_bar is not None:
            utility_layout.addWidget(
                self._utility_button(
                    "MENU", self.toggle_menu, "Show the full application menu", "menu"
                )
            )
        if self._context_widget is not None:
            utility_layout.addWidget(
                self._utility_button(
                    "PATH", self.toggle_context, "Show the game path controls", "path"
                )
            )
        for command in commands:
            utility_layout.addWidget(
                self._utility_button(
                    command.label,
                    command.callback,
                    command.tooltip,
                    command.icon_name or inferred_symbol(command.label),
                )
            )
        product_label = QLabel(product.upper(), utility)
        product_label.setObjectName("shellProduct")
        product_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        utility_layout.addWidget(product_label)
        header_layout.addWidget(utility, 0)
        root.addWidget(header)

        if self._context_widget is not None:
            self._context_widget.setParent(self)
            root.addWidget(self._context_widget)
            self._context_widget.hide()

        body = QFrame(self)
        body.setObjectName("shellBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        context_nav = QFrame(body)
        context_nav.setObjectName("contextNavigation")
        context_nav.setMinimumWidth(238)
        context_nav.setMaximumWidth(270)
        context_layout = QVBoxLayout(context_nav)
        context_layout.setContentsMargins(28, 30, 20, 24)
        context_layout.setSpacing(0)
        self.context_eyebrow = QLabel("WORKSPACE", context_nav)
        self.context_eyebrow.setObjectName("contextEyebrow")
        context_layout.addWidget(self.context_eyebrow)
        self.context_title = QLabel("", context_nav)
        self.context_title.setObjectName("contextTitle")
        context_layout.addWidget(self.context_title)
        self._route_container = QWidget(context_nav)
        self._route_container.setObjectName("routeNavigation")
        self._route_layout = QVBoxLayout(self._route_container)
        self._route_layout.setContentsMargins(0, 18, 0, 0)
        self._route_layout.setSpacing(2)
        context_layout.addWidget(self._route_container)
        context_layout.addStretch(1)
        library_note = QLabel("ARCHIVE / 1.14", context_nav)
        library_note.setObjectName("contextFootnote")
        context_layout.addWidget(library_note)
        body_layout.addWidget(context_nav, 0)

        workspace = QFrame(body)
        workspace.setObjectName("editorialWorkspace")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        self.route_header = QFrame(workspace)
        self.route_header.setObjectName("routeHeader")
        route_header_layout = QHBoxLayout(self.route_header)
        route_header_layout.setContentsMargins(28, 14, 30, 12)
        route_header_layout.setSpacing(12)
        title_stack = QVBoxLayout()
        title_stack.setContentsMargins(0, 0, 0, 0)
        title_stack.setSpacing(1)
        self.route_eyebrow = QLabel("", self.route_header)
        self.route_eyebrow.setObjectName("routeEyebrow")
        self.route_title = QLabel("", self.route_header)
        self.route_title.setObjectName("routeTitle")
        title_stack.addWidget(self.route_eyebrow)
        title_stack.addWidget(self.route_title)
        route_header_layout.addLayout(title_stack)
        route_header_layout.addStretch(1)
        self.route_description = QLabel("", self.route_header)
        self.route_description.setObjectName("routeDescription")
        self.route_description.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.route_description.setWordWrap(True)
        self.route_description.setMinimumWidth(320)
        self.route_description.setMaximumWidth(420)
        route_header_layout.addWidget(self.route_description, 0)
        workspace_layout.addWidget(self.route_header)

        self._router_tabs.setParent(workspace)
        self._router_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        workspace_layout.addWidget(self._router_tabs, 1)
        body_layout.addWidget(workspace, 1)
        root.addWidget(body, 1)

        footer = QFrame(self)
        footer.setObjectName("shellFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(34, 0, 30, 0)
        footer_layout.setSpacing(18)
        shortcuts = QLabel("CTRL+O  OPEN     CTRL+S  SAVE     ESC  CLOSE", footer)
        shortcuts.setObjectName("shellShortcuts")
        footer_layout.addWidget(shortcuts)
        footer_layout.addStretch(1)
        safety = QLabel("SOURCE-SAFE  /  BACKUP BEFORE WRITE", footer)
        safety.setObjectName("shellSafety")
        footer_layout.addWidget(safety)
        root.addWidget(footer)

    def _utility_button(
        self,
        label: str,
        callback: Callable[[], None],
        tooltip: str,
        icon_name: str = "menu",
    ) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("shellUtilityButton")
        button.setText(label.upper())
        button.setIcon(symbol_icon(icon_name))
        button.setIconSize(QSize(19, 19))
        button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        button.setAutoRaise(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tooltip)
        button.clicked.connect(lambda checked=False: callback())
        return button

    def activate_destination(self, index: int) -> None:
        if index < 0 or index >= len(self._destinations):
            raise IndexError(f"Destination index out of range: {index}")
        owns_routing_guard = not self._routing
        if owns_routing_guard:
            self._routing = True
        try:
            self._active_destination = index
            destination = self._destinations[index]
            self.destination_buttons[index].setChecked(True)
            self.context_title.setText(destination.label.upper())
            self.context_eyebrow.setText(destination.caption.upper() or "WORKSPACE")
            self.route_eyebrow.setText(destination.caption.upper() or "CRIMSON DESERT TOOLS")
            self._rebuild_route_navigation(destination)
            self.activate_route(0)
        finally:
            if owns_routing_guard:
                self._routing = False

    def _rebuild_route_navigation(self, destination: ShellDestination) -> None:
        previous_group = getattr(self, "_route_group", None)
        for button in self.route_buttons:
            if previous_group is not None:
                previous_group.removeButton(button)
            button.hide()
            button.setParent(None)
            button.deleteLater()
        while self._route_layout.count():
            self._route_layout.takeAt(0)
        if previous_group is not None:
            previous_group.deleteLater()
        self.route_buttons.clear()
        self._route_group = QButtonGroup(self)
        self._route_group.setExclusive(True)
        for index, route in enumerate(destination.routes):
            button = CrimsonRouteButton(route, self._route_container)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName(f"Open {route.label}")
            button.clicked.connect(
                lambda checked=False, route_index=index: self.activate_route(route_index)
            )
            self._route_group.addButton(button, index)
            self.route_buttons.append(button)
            self._route_layout.addWidget(button)

    def activate_route(self, index: int) -> None:
        destination = self._destinations[self._active_destination]
        if index < 0 or index >= len(destination.routes):
            raise IndexError(f"Route index out of range: {index}")
        owns_routing_guard = not self._routing
        if owns_routing_guard:
            self._routing = True
        try:
            self._active_route = index
            route = destination.routes[index]
            self.route_buttons[index].setChecked(True)
            self._router_tabs.setCurrentIndex(route.primary_index)
            if route.section_tabs is not None:
                route.section_tabs.setCurrentIndex(route.section_index)
            self.route_title.setText(route.label)
            self.route_description.setText(route.description)
            self.route_description.setVisible(bool(route.description))
            self.route_header.setVisible(not route.immersive)
        finally:
            if owns_routing_guard:
                self._routing = False

    def _connect_router_sync(self) -> None:
        self._router_tabs.currentChanged.connect(self._sync_from_router)
        connected: set[int] = set()
        for destination in self._destinations:
            for route in destination.routes:
                section = route.section_tabs
                if section is None or id(section) in connected:
                    continue
                section.currentChanged.connect(self._sync_from_router)
                connected.add(id(section))

    def _sync_from_router(self, _index: int) -> None:
        if self._routing:
            return
        sender = self.sender()
        if (
            isinstance(sender, QTabWidget)
            and sender is not self._router_tabs
            and self._router_tabs.currentWidget() is not sender
        ):
            return
        primary_index = self._router_tabs.currentIndex()
        destination_order = [self._active_destination]
        destination_order.extend(
            index
            for index in range(len(self._destinations))
            if index != self._active_destination
        )
        for destination_index in destination_order:
            destination = self._destinations[destination_index]
            for route_index, route in enumerate(destination.routes):
                if route.primary_index != primary_index:
                    continue
                if (
                    route.section_tabs is not None
                    and route.section_tabs.currentIndex() != route.section_index
                ):
                    continue
                self._routing = True
                try:
                    self.activate_destination(destination_index)
                    self.activate_route(route_index)
                finally:
                    self._routing = False
                return

    def toggle_menu(self) -> None:
        if self._menu_bar is not None:
            self._menu_bar.setVisible(self._menu_bar.isHidden())

    def toggle_context(self) -> None:
        if self._context_widget is not None:
            self._context_widget.setVisible(self._context_widget.isHidden())


def install_crimson_application_shell(
    main_window: QMainWindow,
    *,
    product: str,
    router_tabs: QTabWidget,
    destinations: Sequence[ShellDestination],
    context_widget: QWidget | None = None,
    commands: Sequence[ShellCommand] = (),
    docks: Iterable[QWidget] = (),
    preserve_widgets: Iterable[QWidget] = (),
) -> CrimsonApplicationShell:
    """Replace legacy window chrome while preserving every existing page widget."""

    old_central = main_window.centralWidget()
    normalize_legacy_button_styles(router_tabs)
    shell = CrimsonApplicationShell(
        product=product,
        router_tabs=router_tabs,
        destinations=destinations,
        menu_bar=main_window.menuBar(),
        context_widget=context_widget,
        commands=commands,
        parent=main_window,
    )
    for widget in preserve_widgets:
        widget.setParent(shell)
        widget.hide()
    detached = main_window.takeCentralWidget()
    main_window.setCentralWidget(shell)
    if detached is not None and detached is old_central and detached is not shell:
        detached.deleteLater()
    for dock in docks:
        dock.hide()
    return shell
