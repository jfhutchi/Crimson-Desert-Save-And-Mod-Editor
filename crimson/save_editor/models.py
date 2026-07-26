from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from crimson.save_editor.save_compat import SaveSchemaIdentity


class QuestState(IntEnum):
    LOCKED           = 0x0D01
    AVAILABLE        = 0x0902
    AVAILABLE_PLUS   = 0x0903
    IN_PROGRESS      = 0x0905
    IN_PROGRESS_PLUS = 0x1102
    COMPLETED        = 0x1105
    SIDE_CONTENT     = 0x1502
    FULLY_COMPLETED  = 0x1905


@dataclass
class SaveItem:
    offset: int = 0
    item_no: int = 0
    item_key: int = 0
    slot_no: int = 0
    stack_count: int = 0
    enchant_level: int = 0
    endurance: int = 0
    sharpness: int = 0

    @property
    def actual_endurance(self) -> int:
        return self.endurance & 0xFF

    @property
    def socket_count_from_endurance(self) -> int:
        return (self.endurance >> 8) & 0xFF
    has_enchant: bool = False
    is_equipment: bool = False
    source: str = "Inventory"
    bag: str = ""
    section: int = 0
    name: str = ""
    category: str = "Misc"
    block_size: int = 0
    field_offsets: dict = field(default_factory=dict)
    parc_parsed: bool = False


@dataclass
class SaveData:
    source_file_sha256: str
    raw_header: bytes = b""
    decompressed_blob: bytearray = field(default_factory=bytearray)
    original_compressed_size: int = 0
    original_decompressed_size: int = 0
    file_path: str = ""
    is_raw_stream: bool = False
    schema_identity: SaveSchemaIdentity | None = None
    compatibility_profile_id: str | None = None
    is_schema_supported: bool = False
    document_generation: int = 0
    parse_epoch: int = 0

    def __setattr__(self, name, value):
        # Structural edits replace decompressed_blob by assignment, which
        # invalidates every previously parsed offset. In-place byte edits keep
        # offsets valid and do not pass through here.
        if name == "decompressed_blob":
            object.__setattr__(
                self, "parse_epoch", getattr(self, "parse_epoch", 0) + 1
            )
        object.__setattr__(self, name, value)


@dataclass
class ItemInfo:
    item_key: int = 0
    name: str = ""
    internal_name: str = ""
    category: str = "Misc"
    max_stack: int = 0


@dataclass
class UndoEntry:
    description: str = ""
    offset: int = 0
    old_bytes: bytes = b""
    new_bytes: bytes = b""
    patches: list = field(default_factory=list)
    previous_blob: bytes | None = None
