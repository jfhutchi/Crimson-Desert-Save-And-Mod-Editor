"""The Rust parser must match the Python parser exactly, stage by stage.

Nothing native wires into the app until its output is indistinguishable from
the reference implementation on a real save. Stage 1: schema and TOC.
"""
from __future__ import annotations

import pytest

from conftest import SAVE_FIXTURE, requires_fixture

crimson_parser = pytest.importorskip(
    "crimson_parser", reason="native parser not built (maturin develop)"
)

pytestmark = requires_fixture


@pytest.fixture(scope="module")
def raw() -> bytes:
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for entry in (root, root / "crimson" / "game_mods"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))
    from crimson.save_editor import save_crypto

    return bytes(save_crypto.load_save_file(str(SAVE_FIXTURE)).decompressed_blob)


def test_schema_matches_python_exactly(raw) -> None:
    from crimson.save_editor.save_parser import parse_schema

    ours = crimson_parser.parse_schema(raw)
    reference = parse_schema(raw)

    for key in ("header_tag", "header_zero", "type_count", "root_type", "schema_end"):
        assert ours[key] == reference[key], key

    assert len(ours["types"]) == len(reference["types"])
    for rust_type, py_type in zip(ours["types"], reference["types"]):
        assert rust_type["name"] == py_type.name
        assert rust_type["index"] == py_type.index
        assert rust_type["start_offset"] == py_type.start_offset
        assert rust_type["end_offset"] == py_type.end_offset
        assert len(rust_type["fields"]) == len(py_type.fields)
        for rust_field, py_field in zip(rust_type["fields"], py_type.fields):
            for attr in ("name", "type_name", "meta_kind", "meta_size",
                         "meta_aux", "start_offset", "end_offset"):
                assert rust_field[attr] == getattr(py_field, attr), (
                    f"{py_type.name}.{py_field.name}.{attr}"
                )


def test_toc_matches_python_exactly(raw) -> None:
    from crimson.save_editor.save_parser import parse_schema, parse_toc

    reference_schema = parse_schema(raw)
    reference = parse_toc(
        raw, reference_schema["schema_end"],
        [t.name for t in reference_schema["types"]],
    )
    ours = crimson_parser.parse_toc(raw)

    for key in ("prefix_zero", "toc_count", "stream_size"):
        assert ours[key] == reference[key], key
    assert len(ours["entries"]) == len(reference["entries"])
    for rust_entry, py_entry in zip(ours["entries"], reference["entries"]):
        for attr in ("index", "class_index", "class_name", "sentinel1",
                     "sentinel2", "data_offset", "data_size", "entry_offset"):
            assert rust_entry[attr] == getattr(py_entry, attr), (
                f"entry {py_entry.index} {attr}"
            )
