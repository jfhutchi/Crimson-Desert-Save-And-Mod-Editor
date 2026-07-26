from __future__ import annotations

from crimson.game_mods.gui.crimson_theme import (
    CRIMSON_DARK_TOKENS,
    CRIMSON_LIGHT_TOKENS,
    apply_crimson_theme,
    build_stylesheet,
    install_crimson_shell,
    legacy_colors,
)


DARK_COLORS = legacy_colors(CRIMSON_DARK_TOKENS)
LIGHT_COLORS = legacy_colors(CRIMSON_LIGHT_TOKENS)
COLORS = dict(DARK_COLORS)

CATEGORY_COLORS = {
    "Equipment": "#C6A15F",
    "Material": "#A89B62",
    "Quest": "#C28A3C",
    "Currency": "#C6A15F",
    "Consumable": "#758B5C",
    "Ammo": "#A94A3E",
    "Misc": "#A89B87",
}

_TAB_SELECTED_BG = CRIMSON_DARK_TOKENS["selection"]
_TAB_SELECTED_COLOR = CRIMSON_DARK_TOKENS["parchment"]
_TAB_SELECTED_BORDER = CRIMSON_DARK_TOKENS["bronze_bright"]
_TAB_SELECTED_BG_LIGHT = CRIMSON_LIGHT_TOKENS["selection"]
_TAB_SELECTED_COLOR_LIGHT = CRIMSON_LIGHT_TOKENS["parchment"]
_TAB_SELECTED_BORDER_LIGHT = CRIMSON_LIGHT_TOKENS["bronze_bright"]

DARK_STYLESHEET = build_stylesheet(CRIMSON_DARK_TOKENS)
LIGHT_STYLESHEET = build_stylesheet(CRIMSON_LIGHT_TOKENS)


def apply_theme(
    app,
    mode: str,
    *,
    scale: float = 1.0,
    compact: bool = False,
) -> str:
    colors = LIGHT_COLORS if mode == "light" else DARK_COLORS
    COLORS.clear()
    COLORS.update(colors)
    return apply_crimson_theme(app, mode, scale=scale, compact=compact)
