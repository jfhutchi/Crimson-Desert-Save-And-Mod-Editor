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


# ---------------------------------------------------------------------------
# Stage 2: the full object tree, compared via a canonical FNV-1a digest that
# both sides compute over the identical field walk.

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x00000100000001B3
_MASK = (1 << 64) - 1


class _Digest:
    def __init__(self) -> None:
        self.state = _FNV_OFFSET
        self.nodes = 0

    def _push(self, data: bytes) -> None:
        state = self.state
        for byte in data:
            state = ((state ^ byte) * _FNV_PRIME) & _MASK
        state = ((state ^ 0x1F) * _FNV_PRIME) & _MASK
        self.state = state

    def text(self, s: str) -> None:
        self._push(s.encode("utf-8"))

    def num(self, v) -> None:
        self._push(str(int(v)).encode())

    def node(self, n) -> None:
        self.nodes += 1
        self.num(n.field_index)
        self.text(n.name)
        self.text(n.type_name)
        self.num(n.meta_kind)
        self.num(n.meta_size)
        self.num(n.meta_aux)
        self.num(n.present)
        self.text(n.decode_kind)
        self.num(n.start_offset)
        self.num(n.end_offset)
        self.text(n.value_repr)
        self.text(n.edit_format)
        self.num(n.editable)
        self.text(n.note)
        self.num(n.child_prefix_u16)
        self.num(n.child_prefix_u8)
        self.num(n.child_mask_byte_count)
        self.text(n.child_mask_bytes.hex())
        self.num(n.child_type_index)
        self.text(n.child_type_name)
        self.num(n.child_reserved_u8)
        self.num(n.child_sentinel1_u32)
        self.num(n.child_sentinel2_u32)
        self.num(n.child_payload_offset)
        self.num(n.child_reserved_u32)
        self.num(n.child_size_u32)
        self.num(n.list_prefix_u8)
        self.num(n.list_count)
        self.num(n.list_reserved1_u32)
        self.num(n.list_reserved2_u32)
        self.num(n.list_reserved3_u32)
        self.num(n.list_reserved4_u16)
        self.num(n.list_reserved4_u32)
        self.num(n.list_header_size)
        if n.child_fields is None:
            self.text("|nochildren")
        else:
            self.num(len(n.child_fields))
            for child in n.child_fields:
                self.node(child)
        if n.child_undecoded_ranges is None:
            self.text("|nounranges")
        else:
            for a, b in n.child_undecoded_ranges:
                self.num(a)
                self.num(b)
        if n.list_elements is None:
            self.text("|noelements")
        else:
            self.num(len(n.list_elements))
            for element in n.list_elements:
                self.node(element)

    def block(self, b) -> None:
        self.num(b.entry_index)
        self.num(b.class_index)
        self.text(b.class_name)
        self.num(b.data_offset)
        self.num(b.data_size)
        self.num(b.mask_byte_count)
        self.text(b.header_mask_bytes.hex())
        self.num(b.reserved_u32)
        for f in b.fields:
            self.node(f)
        for a, z in b.undecoded_ranges:
            self.num(a)
            self.num(z)

    def hex(self) -> str:
        return f"{self.state:016x}"


def test_object_tree_matches_python_exactly(raw) -> None:
    if getattr(crimson_parser, "STAGE", 0) < 2:
        pytest.skip("native module predates stage 2; rebuild with maturin")
    from crimson.save_editor.save_parser import build_result_from_raw

    reference = build_result_from_raw(raw, {"input_kind": "raw_blob"})
    expected = _Digest()
    for block in reference["objects"]:
        expected.block(block)

    got_hex, got_nodes, got_blocks = crimson_parser.decode_digest(raw)

    assert got_blocks == len(reference["objects"]), (
        f"block count differs: rust {got_blocks} vs python {len(reference['objects'])}"
    )
    assert got_nodes == expected.nodes, (
        f"node count differs: rust {got_nodes} vs python {expected.nodes}"
    )
    assert got_hex == expected.hex(), (
        "canonical digests differ - some field decodes are not identical"
    )
