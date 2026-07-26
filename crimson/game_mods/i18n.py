
import json
import logging
import os
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

_LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")
_current_locale: str = "en"
_strings: Dict[str, str] = {}
_fallback: Dict[str, str] = {}


def _load_locale_file(code: str) -> Dict[str, str]:
    path = os.path.join(_LOCALE_DIR, f"{code}.json")
    if not os.path.isfile(path):
        log.warning("Locale file not found: %s", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception as e:
        log.error("Failed to load locale %s: %s", code, e)
        return {}


def set_locale(code: str) -> None:
    global _current_locale, _strings, _fallback
    _current_locale = code

    if not _fallback:
        _fallback = _load_locale_file("en")

    if code == "en":
        _strings = _fallback
    else:
        _strings = _load_locale_file(code)
        if not _strings:
            log.warning("Locale '%s' not found, using English", code)
            _strings = _fallback

    try:
        from crimson.game_mods import gui_i18n as gui_i18n
        cur = gui_i18n.current_language() if hasattr(gui_i18n, "current_language") else "en"
        if code != cur and not (code == "en" and cur not in (None, "", "en")):
            gui_i18n.set_language(code)
    except Exception:
        pass


def set_language(code: str) -> None:
    set_locale(code)


def tr(key: str, **kwargs) -> str:
    if not _strings and not _fallback:
        set_locale("en")

    text = _strings.get(key) or _fallback.get(key)
    if text is None:
        try:
            from crimson.game_mods import gui_i18n as gui_i18n
            text = gui_i18n.tr(key, **kwargs) if kwargs else gui_i18n.tr(key)
            return text
        except Exception:
            text = key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text


def available_locales() -> List[dict]:
    locales = []
    if not os.path.isdir(_LOCALE_DIR):
        return locales

    for fname in sorted(os.listdir(_LOCALE_DIR)):
        if not fname.endswith(".json"):
            continue
        code = fname[:-5]
        path = os.path.join(_LOCALE_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            meta = data.get("_meta", {})
            locales.append({
                "code": code,
                "language": meta.get("language", code),
                "author": meta.get("author", ""),
            })
        except Exception:
            locales.append({"code": code, "language": code, "author": ""})

    return locales


def get_locale() -> str:
    return _current_locale


def locale_dir() -> str:
    return _LOCALE_DIR
