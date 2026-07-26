from __future__ import annotations

import re
from typing import Mapping


DISPLAY_FONT = 'Georgia, Cambria, "Times New Roman", serif'
BODY_FONT = 'Bahnschrift, "Segoe UI", sans-serif'
BODY_FONT_STYLE = "SemiCondensed"
MONO_FONT = '"Cascadia Mono", Consolas, monospace'

# The legacy token names remain for compatibility with hundreds of existing
# widgets. The additional semantic tokens describe the approved flat mockup.
CRIMSON_DARK_TOKENS = {
    "ink": "#050706",
    "obsidian": "#080B09",
    "charcoal": "#0E1310",
    "ember": "#17150F",
    "bronze": "#29302B",
    "bronze_bright": "#B63A32",
    "parchment": "#E6E1D7",
    "ash": "#7F857D",
    "crimson": "#9F302B",
    "moss": "#78917B",
    "amber": "#A88455",
    "iron_red": "#C05247",
    "selection": "#18201B",
    "scope_save": "#87A6A0",
    "scope_game": "#A88455",
    "surface": "#0D120F",
    "surface_warm": "#17150F",
    "divider": "#29302B",
    "accent_red": "#B63A32",
    "text_bright": "#F2EDE3",
}

CRIMSON_LIGHT_TOKENS = {
    "ink": "#E4DFD5",
    "obsidian": "#F2EEE6",
    "charcoal": "#E8E3DA",
    "ember": "#DDD6CA",
    "bronze": "#B9B2A7",
    "bronze_bright": "#9E302A",
    "parchment": "#1A1D1A",
    "ash": "#62675F",
    "crimson": "#8D2925",
    "moss": "#55705A",
    "amber": "#80633D",
    "iron_red": "#A43F36",
    "selection": "#D8DDD6",
    "scope_save": "#3C6E68",
    "scope_game": "#80633D",
    "surface": "#ECE8DF",
    "surface_warm": "#DDD6CA",
    "divider": "#B9B2A7",
    "accent_red": "#9E302A",
    "text_bright": "#111411",
}


def legacy_colors(tokens: Mapping[str, str]) -> dict[str, str]:
    """Map semantic tokens to keys still used by the established editors."""
    return {
        "bg": tokens["obsidian"],
        "panel": tokens["surface"],
        "header": tokens["surface_warm"],
        "accent": tokens["accent_red"],
        "text": tokens["parchment"],
        "text_dim": tokens["ash"],
        "selected": tokens["selection"],
        "border": tokens["divider"],
        "input_bg": tokens["ink"],
        "success": tokens["moss"],
        "warning": tokens["amber"],
        "error": tokens["iron_red"],
        "scope_save": tokens["scope_save"],
        "scope_game": tokens["accent_red"],
    }


def build_stylesheet(tokens: Mapping[str, str]) -> str:
    c = tokens
    return f"""
QMainWindow#crimsonWindow, QDialog {{
    background-color: {c['obsidian']};
    color: {c['parchment']};
}}
QWidget {{
    background-color: {c['obsidian']};
    color: {c['parchment']};
    font-size: 10pt;
}}
QWidget:disabled {{ color: {c['ash']}; }}
QLabel {{ background: transparent; }}

QWidget#crimsonApplicationShell {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {c['ink']}, stop:0.54 {c['obsidian']}, stop:1 {c['surface_warm']});
}}
QFrame#commandHeader {{
    min-height: 76px;
    max-height: 76px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c['ink']}, stop:0.62 {c['obsidian']}, stop:1 {c['surface_warm']});
    border: 0px;
    border-bottom: 1px solid {c['divider']};
}}
QWidget#destinationNavigation,
QWidget#shellUtilities,
QFrame#shellBody {{
    background: transparent;
    border: 0px;
}}
QToolButton#destinationButton {{
    min-width: 70px;
    min-height: 74px;
    padding: 7px 2px 4px 2px;
    color: {c['ash']};
    background: transparent;
    border: 0px;
    border-bottom: 2px solid transparent;
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 1px;
}}
QToolButton#destinationButton:hover {{
    color: {c['text_bright']};
    background: transparent;
    border: 0px;
    border-bottom: 2px solid {c['divider']};
}}
QToolButton#destinationButton:checked {{
    color: {c['text_bright']};
    background: transparent;
    border: 0px;
    border-bottom: 2px solid {c['accent_red']};
}}
QToolButton#shellUtilityButton {{
    min-width: 52px;
    max-width: 84px;
    min-height: 40px;
    max-height: 44px;
    padding: 2px 6px;
    color: {c['ash']};
    background: transparent;
    border: 0px;
    font-size: 7.5pt;
    font-weight: 700;
}}
QToolButton#shellUtilityButton:hover,
QToolButton#shellUtilityButton:focus {{
    color: {c['text_bright']};
    background-color: {c['surface']};
    border: 0px;
}}
QFrame#contextNavigation {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c['ink']}, stop:1 {c['obsidian']});
    border: 0px;
    border-right: 1px solid {c['divider']};
}}
QLabel#contextEyebrow,
QLabel#routeEyebrow {{
    color: {c['accent_red']};
    font-size: 7.5pt;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#contextTitle {{
    color: {c['text_bright']};
    font-family: Georgia;
    font-size: 13pt;
    padding-top: 3px;
}}
QWidget#routeNavigation {{
    background: transparent;
    border: 0px;
}}
QAbstractButton#routeButton {{
    min-height: 46px;
    padding: 0px;
    color: {c['ash']};
    background: transparent;
    border: 0px;
    border-left: 2px solid transparent;
    text-align: left;
    font-size: 9.5pt;
    font-weight: 500;
}}
QAbstractButton#routeButton:hover {{
    color: {c['parchment']};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c['surface']}, stop:1 transparent);
    border: 0px;
    border-left: 2px solid {c['divider']};
}}
QAbstractButton#routeButton:checked {{
    color: {c['text_bright']};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c['selection']}, stop:1 transparent);
    border: 0px;
    border-left: 2px solid {c['accent_red']};
    font-weight: 700;
}}
QFrame#routeArtHalo {{
    background: qradialgradient(cx:0.5, cy:0.45, radius:0.72,
        stop:0 {c['surface']}, stop:0.72 {c['ink']}, stop:1 transparent);
    border: 0px;
    border-bottom: 1px solid {c['divider']};
}}
QLabel#routeArt {{ background: transparent; border: 0px; }}
QLabel#routeButtonTitle {{
    color: {c['ash']};
    font-family: Georgia;
    font-size: 10pt;
}}
QLabel#routeButtonBadge {{
    color: {c['divider']};
    font-size: 7pt;
    font-weight: 700;
    letter-spacing: 1px;
}}
QAbstractButton#routeButton:hover QLabel#routeButtonTitle,
QAbstractButton#routeButton:checked QLabel#routeButtonTitle {{
    color: {c['text_bright']};
}}
QAbstractButton#routeButton:hover QLabel#routeButtonBadge,
QAbstractButton#routeButton:checked QLabel#routeButtonBadge {{
    color: {c['ash']};
}}
QLabel#contextFootnote {{
    color: {c['divider']};
    font-size: 7pt;
    letter-spacing: 1px;
}}
QFrame#editorialWorkspace {{
    background: qradialgradient(cx:0.78, cy:0.02, radius:0.95,
        stop:0 {c['surface_warm']}, stop:0.34 {c['obsidian']}, stop:1 {c['ink']});
    border: 0px;
}}
QFrame#routeHeader {{
    min-height: 54px;
    max-height: 54px;
    background: transparent;
    border: 0px;
    border-bottom: 1px solid {c['divider']};
}}
QLabel#routeTitle {{
    color: {c['text_bright']};
    font-family: Georgia;
    font-size: 15pt;
}}
QLabel#routeDescription {{
    max-width: 420px;
    color: {c['ash']};
    font-size: 8.5pt;
}}
QFrame#shellFooter {{
    min-height: 39px;
    max-height: 39px;
    background-color: {c['ink']};
    border: 0px;
    border-top: 1px solid {c['divider']};
}}
QLabel#shellShortcuts {{
    color: {c['ash']};
    font-family: "Cascadia Mono";
    font-size: 7.5pt;
    letter-spacing: 1px;
}}
QLabel#shellSafety {{
    color: {c['moss']};
    font-size: 7.5pt;
    font-weight: 700;
    letter-spacing: 1px;
}}

QMenuBar {{
    min-height: 24px;
    background-color: {c['ink']};
    color: {c['ash']};
    border: 0px;
    border-bottom: 1px solid {c['divider']};
    padding: 0px 14px;
    font-size: 9pt;
}}
QMenuBar::item {{
    background: transparent;
    padding: 4px 12px;
}}
QMenuBar::item:selected, QMenuBar::item:pressed {{
    color: {c['text_bright']};
    background-color: {c['surface']};
}}
QMenu {{
    background-color: {c['surface']};
    color: {c['parchment']};
    border: 1px solid {c['divider']};
    padding: 5px;
}}
QMenu::item {{ padding: 7px 28px 7px 12px; }}
QMenu::item:selected {{
    background-color: {c['selection']};
    color: {c['text_bright']};
    border-left: 2px solid {c['accent_red']};
}}
QMenu::separator {{
    height: 1px;
    background-color: {c['divider']};
    margin: 5px 8px;
}}

QTabWidget#primaryNav {{ background-color: {c['ink']}; }}
QTabWidget#primaryNav::pane {{
    border: 0px;
    border-top: 1px solid {c['divider']};
    background-color: {c['obsidian']};
}}
QTabWidget#primaryNav::tab-bar {{ alignment: center; }}
QTabBar#primaryTabBar {{ background-color: {c['ink']}; }}
QTabBar#primaryTabBar::tab {{
    min-height: 40px;
    min-width: 78px;
    padding: 2px 16px;
    margin: 0px 3px;
    background: transparent;
    color: {c['ash']};
    border: 0px;
    border-bottom: 2px solid transparent;
    font-size: 8.5pt;
    font-weight: 700;
}}
QTabBar#primaryTabBar::tab:selected {{
    color: {c['text_bright']};
    border-bottom: 2px solid {c['accent_red']};
}}
QTabBar#primaryTabBar::tab:hover:!selected {{ color: {c['parchment']}; }}

QTabWidget#sectionNav::pane {{
    border: 0px;
    border-top: 1px solid {c['divider']};
    background-color: {c['obsidian']};
}}
QTabWidget#sectionNav::tab-bar {{ alignment: left; }}
QTabBar#sectionTabBar {{ background-color: {c['obsidian']}; }}
QTabBar#sectionTabBar::tab {{
    min-height: 30px;
    padding: 2px 13px;
    margin: 0px;
    background: transparent;
    color: {c['ash']};
    border: 0px;
    border-bottom: 2px solid transparent;
    font-size: 9pt;
    font-weight: 600;
}}
QTabBar#sectionTabBar::tab:selected {{
    color: {c['text_bright']};
    border-bottom: 2px solid {c['accent_red']};
}}
QTabBar#sectionTabBar::tab:hover:!selected {{ color: {c['parchment']}; }}

QWidget#brandBlock {{
    min-width: 210px;
    background-color: {c['ink']};
}}
QLabel#brandMark {{
    color: {c['text_bright']};
    font-family: Georgia;
    font-size: 9pt;
    font-weight: 700;
}}
QWidget#shellIdentity {{
    min-width: 190px;
    background-color: {c['ink']};
}}
QLabel#shellProduct {{
    color: {c['text_bright']};
    font-size: 8pt;
    font-weight: 700;
}}
QLabel#shellDetail {{ color: {c['ash']}; font-size: 8pt; }}

QWidget#contextStrip {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c['obsidian']}, stop:0.55 {c['surface_warm']}, stop:1 {c['obsidian']});
    border: 0px;
    border-bottom: 1px solid {c['divider']};
}}
QFrame#saveBrowser, QFrame#packBrowser {{
    background-color: {c['surface']};
    border: 0px;
}}

QLabel[heading="true"] {{
    color: {c['text_bright']};
    font-family: Georgia;
    font-size: 12pt;
    font-weight: 600;
}}
QLabel[muted="true"] {{ color: {c['ash']}; }}
QLabel[eyebrow="true"] {{
    color: {c['accent_red']};
    font-size: 8pt;
    font-weight: 700;
}}
QLabel[displayTitle="true"] {{
    color: {c['text_bright']};
    font-family: Georgia;
    font-size: 28pt;
}}

QPushButton, QToolButton {{
    min-height: 30px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c['surface']}, stop:0.72 {c['obsidian']}, stop:1 transparent);
    color: {c['ash']};
    border: 0px;
    border-bottom: 1px solid {c['divider']};
    border-radius: 0px;
    padding: 3px 12px;
    font-size: 9pt;
    font-weight: 700;
}}
QPushButton:hover, QToolButton:hover {{
    color: {c['text_bright']};
    border: 0px;
    border-bottom: 1px solid {c['accent_red']};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c['selection']}, stop:0.82 {c['surface']}, stop:1 transparent);
}}
QPushButton:pressed, QToolButton:pressed {{
    color: {c['text_bright']};
    background-color: {c['selection']};
}}
QPushButton:focus, QToolButton:focus {{
    outline: none;
    border: 0px;
    border-bottom: 1px solid {c['accent_red']};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: {c['divider']};
    background: transparent;
    border: 0px;
    border-bottom: 1px solid {c['divider']};
}}
QPushButton#accentBtn,
QPushButton[primaryAction="true"] {{
    background-color: {c['accent_red']};
    color: {c['text_bright']};
    border: 1px solid {c['accent_red']};
}}
QPushButton#accentBtn:hover,
QPushButton[primaryAction="true"]:hover {{
    background-color: {c['iron_red']};
    border-color: {c['iron_red']};
}}
QPushButton[primaryAction="true"]:disabled {{
    color: {c['ash']};
    background-color: {c['surface']};
    border-color: {c['divider']};
}}
QPushButton[dangerAction="true"] {{
    background-color: {c['crimson']};
    color: {c['text_bright']};
    border: 1px solid {c['iron_red']};
}}
QPushButton[quietAction="true"] {{
    background: transparent;
    color: {c['ash']};
}}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QTimeEdit {{
    min-height: 28px;
    background-color: {c['ink']};
    color: {c['parchment']};
    border: 0px;
    border-bottom: 1px solid {c['divider']};
    border-radius: 0px;
    padding: 2px 7px;
    selection-background-color: {c['selection']};
    selection-color: {c['text_bright']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-bottom: 1px solid {c['accent_red']};
}}
QComboBox::drop-down {{
    width: 22px;
    border: 0px;
    background-color: {c['surface']};
}}
QComboBox QAbstractItemView {{
    background-color: {c['surface']};
    color: {c['parchment']};
    border: 1px solid {c['divider']};
    selection-background-color: {c['selection']};
}}

QGroupBox {{
    color: {c['accent_red']};
    border: 0px;
    border-top: 1px solid {c['divider']};
    border-radius: 0px;
    margin-top: 18px;
    padding: 18px 8px 8px 8px;
    font-size: 9pt;
    font-weight: 700;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0px;
    padding: 0px 10px 0px 0px;
    color: {c['accent_red']};
    background-color: {c['obsidian']};
}}

QTableWidget, QTableView, QTreeWidget, QTreeView, QListWidget, QListView,
QTextEdit, QPlainTextEdit {{
    background-color: {c['ink']};
    alternate-background-color: {c['obsidian']};
    color: {c['parchment']};
    border: 0px;
    border-top: 1px solid {c['divider']};
    border-radius: 0px;
    gridline-color: {c['divider']};
    selection-background-color: {c['selection']};
    selection-color: {c['text_bright']};
}}
QTableWidget, QTableView, QTreeWidget, QTreeView {{
    font-size: 9.5pt;
}}
QTableWidget::item, QTableView::item {{ padding: 4px 7px; }}
QTreeWidget::item, QTreeView::item, QListWidget::item, QListView::item {{
    min-height: 25px;
    padding: 2px 6px;
    border-left: 2px solid transparent;
}}
QTreeWidget::item:selected, QTreeView::item:selected,
QListWidget::item:selected, QListView::item:selected {{
    border-left: 2px solid {c['accent_red']};
}}
QTreeWidget::item:hover, QTreeView::item:hover,
QListWidget::item:hover, QListView::item:hover {{
    background-color: {c['surface']};
    color: {c['text_bright']};
}}
QHeaderView::section {{
    min-height: 27px;
    background-color: {c['surface']};
    color: {c['ash']};
    border: 0px;
    border-bottom: 1px solid {c['divider']};
    padding: 3px 7px;
    font-size: 8.5pt;
    font-weight: 700;
}}
QTableCornerButton::section {{
    background-color: {c['surface']};
    border: 0px;
    border-bottom: 1px solid {c['divider']};
}}

QCheckBox, QRadioButton {{
    min-height: 24px;
    spacing: 7px;
    color: {c['parchment']};
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    background-color: {c['ink']};
    border: 1px solid {c['divider']};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {c['accent_red']};
    border: 1px solid {c['accent_red']};
}}

QStatusBar#statusRail {{
    min-height: 26px;
    background-color: {c['ink']};
    color: {c['ash']};
    border: 0px;
    border-top: 1px solid {c['divider']};
    font-size: 8.5pt;
}}
QStatusBar#statusRail::item {{ border: 0px; }}

QFrame#blackstarTimerPanel {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c['obsidian']}, stop:0.58 {c['surface_warm']}, stop:1 {c['obsidian']});
    border: 0px;
    border-bottom: 1px solid {c['divider']};
}}
QFrame#blackstarTimerBody,
QFrame#blackstarTimerMain,
QFrame#blackstarHero {{ background: transparent; border: 0px; }}
QFrame#blackstarHero {{ border-bottom: 1px solid {c['divider']}; }}
QFrame#blackstarHeroArtHalo {{
    background: qradialgradient(cx:0.5, cy:0.42, radius:0.72,
        stop:0 {c['surface']}, stop:0.68 {c['ink']}, stop:1 transparent);
    border: 0px;
    border-bottom: 1px solid {c['accent_red']};
}}
QLabel#blackstarHeroArt {{ background: transparent; border: 0px; }}
QFrame#blackstarMetricRow {{
    background: transparent;
    border: 0px;
    border-top: 1px solid {c['divider']};
}}
QFrame#changeRecord {{
    background: transparent;
    border: 0px;
    border-left: 1px solid {c['divider']};
}}
QFrame#changeRecordRow {{
    background: transparent;
    border: 0px;
    border-bottom: 1px solid {c['divider']};
}}
QFrame#blackstarStatusBlock {{
    background: transparent;
    border: 0px;
    border-left: 2px solid {c['accent_red']};
}}
QLabel#blackstarTimerTitle {{
    color: {c['text_bright']};
    font-family: Georgia;
    font-size: 28pt;
}}
QLabel#blackstarCompatibilityEyebrow {{
    color: {c['ash']};
    font-size: 7pt;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#blackstarCompatibility {{
    color: {c['moss']};
    font-size: 9pt;
    font-weight: 700;
}}
QLabel#blackstarTimerSectionTitle,
QLabel#changeRecordTitle {{
    color: {c['text_bright']};
    font-family: Georgia;
    font-size: 12pt;
}}
QLabel[metricBefore="true"] {{ color: {c['ash']}; font-size: 13pt; }}
QLabel[metricAfter="true"] {{ color: {c['text_bright']}; font-size: 14pt; }}
QLabel[recordValue="true"] {{
    color: {c['text_bright']};
    font-size: 9pt;
    font-weight: 700;
}}

QProgressBar {{
    min-height: 14px;
    background-color: {c['ink']};
    color: {c['parchment']};
    border: 1px solid {c['divider']};
    border-radius: 0px;
    text-align: center;
}}
QProgressBar::chunk {{ background-color: {c['accent_red']}; }}

QSplitter::handle, QMainWindow::separator {{ background-color: {c['divider']}; }}
QSplitter::handle:horizontal {{ width: 2px; }}
QSplitter::handle:vertical {{ height: 2px; }}
QSplitter::handle:hover, QMainWindow::separator:hover {{ background-color: {c['accent_red']}; }}
QDockWidget {{ color: {c['parchment']}; border: 0px; }}
QDockWidget::title {{
    min-height: 26px;
    background-color: {c['surface']};
    color: {c['text_bright']};
    border-bottom: 1px solid {c['divider']};
    padding: 4px 8px;
    font-family: Georgia;
    font-weight: 600;
}}

QScrollBar:vertical {{ width: 10px; margin: 0px; background-color: {c['ink']}; }}
QScrollBar:horizontal {{ height: 10px; margin: 0px; background-color: {c['ink']}; }}
QScrollBar::handle {{
    min-height: 28px;
    min-width: 28px;
    background-color: {c['divider']};
    border-radius: 0px;
}}
QScrollBar::handle:hover {{ background-color: {c['ash']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}

QToolTip {{
    background-color: {c['surface']};
    color: {c['text_bright']};
    border: 1px solid {c['divider']};
    padding: 5px;
}}
"""


def scaled_stylesheet(
    tokens: Mapping[str, str],
    scale: float = 1.0,
    compact: bool = False,
) -> str:
    factor = max(0.5, min(2.0, scale)) * (0.86 if compact else 1.0)
    stylesheet = build_stylesheet(tokens)
    if abs(factor - 1.0) < 0.001:
        return stylesheet

    def scale_pixels(match: re.Match[str]) -> str:
        value = max(1, round(int(match.group(1)) * factor))
        return f"{value}px"

    return re.sub(r"(\d+)px", scale_pixels, stylesheet)


def apply_crimson_theme(
    app,
    mode: str = "dark",
    *,
    scale: float = 1.0,
    compact: bool = False,
) -> str:
    tokens = CRIMSON_LIGHT_TOKENS if mode == "light" else CRIMSON_DARK_TOKENS
    stylesheet = scaled_stylesheet(tokens, scale=scale, compact=compact)
    if app is not None and app.styleSheet() != stylesheet:
        # Both workspaces and the shell each ask for the theme; re-applying an
        # identical application-wide stylesheet costs ~0.4s of restyling.
        from PySide6.QtGui import QFont

        font = QFont()
        font.setFamilies(["Bahnschrift", "Segoe UI"])
        font.setStyleName(BODY_FONT_STYLE)
        font.setPointSize(10)
        app.setFont(font)
        app.setStyleSheet(stylesheet)
    return stylesheet


def install_crimson_shell(tab_widget, *, product: str, action_widget=None):
    """Install the branded corners used by both primary navigation bars."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

    brand = QWidget(tab_widget)
    brand.setObjectName("brandBlock")
    brand_layout = QHBoxLayout(brand)
    brand_layout.setContentsMargins(18, 4, 18, 4)
    brand_layout.setSpacing(0)
    brand_mark = QLabel(
        '<span style="color:#B63A32">CRIMSON</span> DESERT /<br>TOOLS',
        brand,
    )
    brand_mark.setObjectName("brandMark")
    brand_mark.setTextFormat(Qt.RichText)
    brand_mark.setAccessibleName("Crimson Desert Tools")
    brand_layout.addWidget(brand_mark)
    tab_widget.setCornerWidget(brand, Qt.TopLeftCorner)

    identity = QWidget(tab_widget)
    identity.setObjectName("shellIdentity")
    identity_layout = QHBoxLayout(identity)
    identity_layout.setContentsMargins(12, 3, 12, 3)
    identity_layout.setSpacing(10)
    labels = QVBoxLayout()
    labels.setContentsMargins(0, 0, 0, 0)
    labels.setSpacing(0)
    product_label = QLabel(product.upper(), identity)
    product_label.setObjectName("shellProduct")
    product_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    detail_label = QLabel("CRIMSON DESERT TOOLS", identity)
    detail_label.setObjectName("shellDetail")
    detail_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    labels.addWidget(product_label)
    labels.addWidget(detail_label)
    identity_layout.addLayout(labels)
    if action_widget is not None:
        identity_layout.addWidget(action_widget)
    tab_widget.setCornerWidget(identity, Qt.TopRightCorner)
    return brand, identity
