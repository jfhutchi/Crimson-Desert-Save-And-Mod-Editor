"""Native (Rust) parse with the same result shape as build_result_from_raw.

The crimson_parser extension holds the object tree in native memory and
decodes blocks on first touch, so a parse costs milliseconds and a few MB
instead of seconds and gigabytes. The schema and TOC sections of the result
stay Python-built - they are milliseconds and several consumers hold their
TypeDef/TocEntry objects.

Set CRIMSON_NATIVE_PARSER=0 to force the pure-Python parser.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

try:
    import crimson_parser as _native
except Exception:  # pragma: no cover - the wheel simply is not built
    _native = None


def native_available() -> bool:
    return (
        _native is not None
        and getattr(_native, "STAGE", 0) >= 3
        and os.environ.get("CRIMSON_NATIVE_PARSER", "1") != "0"
    )


def build_result_native(raw: bytes, load_meta: dict[str, Any]) -> dict[str, Any]:
    """Mirror build_result_from_raw's default result using the native tree."""
    from crimson.save_editor.save_parser import parse_schema, parse_toc

    raw = bytes(raw)
    parsed = _native.parse(raw)
    schema = parse_schema(raw)
    toc = parse_toc(raw, schema["schema_end"], [t.name for t in schema["types"]])
    return {
        "input": load_meta,
        "raw": {
            "size": len(raw),
            "schema_end": schema["schema_end"],
            "value_section_offset": schema["schema_end"],
            "value_section_size": len(raw) - schema["schema_end"],
        },
        "schema": {
            "header_tag": schema["header_tag"],
            "header_zero": schema["header_zero"],
            "type_count": schema["type_count"],
            "root_type": schema["root_type"],
            "types": schema["types"],
        },
        "toc": {
            "prefix_zero": toc["prefix_zero"],
            "entry_count": toc["toc_count"],
            "stream_size": toc["stream_size"],
            "entries": toc["entries"],
        },
        "objects": parsed.objects,
        # Keeps the lazy cells alive for the result's lifetime.
        "_native": parsed,
    }


def parse_any(raw: bytes, load_meta: dict[str, Any]) -> dict[str, Any]:
    """Native parse when available, Python otherwise - for any blob."""
    if native_available():
        try:
            return build_result_native(raw, load_meta)
        except Exception:
            log.exception("native parse failed; falling back to Python")
    from crimson.save_editor.save_parser import build_result_from_raw

    return build_result_from_raw(raw, load_meta)
