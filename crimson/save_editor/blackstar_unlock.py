from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import struct
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from crimson.save_editor.blackstar_compat import require_blackstar_compatibility
from crimson.save_editor.blackstar_template import BlackstarDynamicValues, materialize_blackstar_template
from crimson.save_editor.app_logging import phase
from crimson.save_editor.parc_inserter3 import (
    FixupMetrics, ParsedInsertContext, _fixup_external, _fixup_trailing_sizes,
    build_insert_context,
)
from crimson.save_editor.save_compat import SaveSchemaIdentity

BLACKSTAR_CHARACTER_KEY = 1000799
BLACKSTAR_ITEM_KEY = 1002269
_TARGET_PATTERN = re.compile(r"\s*target=0x[0-9A-Fa-f]+")
log = logging.getLogger(__name__)


class BlackstarValidationError(ValueError):
    pass


class BlackstarCancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class BlackstarProgress:
    phase: str
    completed: int
    total: int
    message: str


@dataclass(frozen=True)
class BlackstarChangeReport:
    classification_before: str
    action: str
    mount_before: int
    mount_after: int
    mercenary_no: int | None
    item_no: int | None
    reference_character_key: int | None
    knowledge_changes: int
    quest_changes: int
    byte_growth: int
    fixups: FixupMetrics
    timings_ms: dict[str, float]


@dataclass(frozen=True)
class BlackstarResult:
    input_sha256: str
    candidate_sha256: str
    output_blob: bytes | None
    profile_id: str
    report: BlackstarChangeReport


def _field_value(raw: bytes, field) -> int | None:
    if not field or not field.present:
        return None
    size = field.end_offset - field.start_offset
    if size not in (1, 2, 4, 8):
        return None
    return int.from_bytes(raw[field.start_offset:field.end_offset], "little")


def _objects(context: ParsedInsertContext, class_name: str):
    return [obj for obj in context.result["objects"] if obj.class_name == class_name]


def _clan_parts(context: ParsedInsertContext):
    objects = _objects(context, "MercenaryClanSaveData")
    if len(objects) != 1:
        raise BlackstarValidationError("Expected exactly one MercenaryClanSaveData")
    clan = objects[0]
    mounts = next((f for f in clan.fields if f.name == "_mercenaryDataList"), None)
    if not mounts or not mounts.list_elements:
        raise BlackstarValidationError("Mercenary mount list is missing or empty")
    return clan, mounts


def _walk(node, path: str):
    for field in getattr(node, "fields", None) or []:
        yield from _walk(field, f"{path}.{field.name}")
    for field in getattr(node, "child_fields", None) or []:
        yield from _walk(field, f"{path}.{field.name}")
    for index, element in enumerate(getattr(node, "list_elements", None) or []):
        yield from _walk(element, f"{path}[{index}]")
    if getattr(node, "present", False):
        yield path, node


def canonical_root_snapshot(context: ParsedInsertContext, class_name: str) -> tuple:
    snapshots = []
    for object_index, obj in enumerate(_objects(context, class_name)):
        for path, field in _walk(obj, f"{class_name}[{object_index}]"):
            children = getattr(field, "child_fields", None) or []
            elements = getattr(field, "list_elements", None)
            if children or elements is not None:
                snapshots.append((path, field.type_name, field.child_mask_bytes.hex(),
                                  field.list_prefix_u8,
                                  len(elements) if elements is not None else None))
            elif field.present:
                semantic = _TARGET_PATTERN.sub("", str(field.value_repr))
                snapshots.append((path, field.type_name, semantic))
    if not snapshots:
        raise BlackstarValidationError(f"{class_name} is missing")
    return tuple(snapshots)


def canonical_quest_snapshot(context: ParsedInsertContext) -> tuple:
    return canonical_root_snapshot(context, "QuestSaveData")


def _mount_rows(context: ParsedInsertContext):
    _clan, mounts = _clan_parts(context)
    rows = []
    for index, element in enumerate(mounts.list_elements):
        fields = {field.name: field for field in element.child_fields or []}
        rows.append((index, element, fields, _field_value(context.raw, fields.get("_characterKey"))))
    return rows


def _classify(context: ParsedInsertContext) -> tuple[str, list]:
    found = [row for row in _mount_rows(context) if row[3] == BLACKSTAR_CHARACTER_KEY]
    if not found:
        return "absent", found
    if len(found) > 1:
        raise BlackstarValidationError(f"Duplicate Blackstar records: {len(found)}")
    _index, element, fields, _key = found[0]
    equipment = fields.get("_equipItemList")
    item_key = None
    if equipment and equipment.present and len(equipment.list_elements or []) == 1:
        item_fields = {f.name: f for f in equipment.list_elements[0].child_fields or []}
        item_key = _field_value(context.raw, item_fields.get("_itemKey"))
    legitimate = (
        item_key == BLACKSTAR_ITEM_KEY
        and _field_value(context.raw, fields.get("_ownedCharacterKey")) is not None
        and _field_value(context.raw, fields.get("_isMainMercenary")) == 1
        and _field_value(context.raw, fields.get("_isInitialize")) == 1
        and _field_value(context.raw, fields.get("_occupationState")) == 1
    )
    if legitimate:
        active = _field_value(context.raw, fields.get("_lastSummoned")) == 1
        return "legitimate_active" if active else "legitimate_idle", found
    if element.end_offset - element.start_offset == 206:
        return "legacy", found
    raise BlackstarValidationError("Unrecognized existing Blackstar record; refusing mutation")


def _allocate_ids(context: ParsedInsertContext) -> tuple[int, int]:
    values = set()
    for obj in context.result["objects"]:
        for _path, field in _walk(obj, obj.class_name):
            if field.type_name in ("MercenaryNo", "ItemNo") and field.end_offset - field.start_offset == 8:
                value = _field_value(context.raw, field)
                if value not in (None, 0, 0xFFFFFFFFFFFFFFFF):
                    values.add(value)
    maximum = max(values, default=0)
    if maximum > 0xFFFFFFFFFFFFFFFD:
        raise BlackstarValidationError("Instance-number space is exhausted")
    return maximum + 1, maximum + 2


def _reference_values(context: ParsedInsertContext):
    candidates = []
    maximum_time = 0
    for row in _mount_rows(context):
        index, _element, fields, key = row
        for name in ("_lastPaidTime", "_lastBreedingTime"):
            maximum_time = max(maximum_time, _field_value(context.raw, fields.get(name)) or 0)
        required = ("_ownedCharacterKey", "_spawnPosition", "_spawnYaw", "_spawnFieldInfoKey",
                    "_isMainMercenary", "_isInitialize")
        if all(fields.get(name) and fields[name].present for name in required):
            if _field_value(context.raw, fields["_isMainMercenary"]) == 1 and _field_value(context.raw, fields["_isInitialize"]) == 1:
                candidates.append(row)
    horse = [row for row in candidates if row[3] == 1003120]
    active = [row for row in candidates if _field_value(context.raw, row[2].get("_lastSummoned")) == 1]
    selected = horse if len(horse) == 1 else active if len(active) == 1 else candidates
    if len(selected) != 1:
        raise BlackstarValidationError("Primary mount reference is missing or ambiguous")
    _index, _element, fields, key = selected[0]
    return key, maximum_time, BlackstarDynamicValues(
        mercenary_no=0, item_no=0,
        owner_key=_field_value(context.raw, fields["_ownedCharacterKey"]),
        last_paid_time=maximum_time, last_breeding_time=maximum_time,
        spawn_position=context.raw[fields["_spawnPosition"].start_offset:fields["_spawnPosition"].end_offset],
        spawn_yaw=context.raw[fields["_spawnYaw"].start_offset:fields["_spawnYaw"].end_offset],
        spawn_field_key=_field_value(context.raw, fields["_spawnFieldInfoKey"]),
    )


def _set_mount_count(blob: bytearray, list_start: int, count: int) -> None:
    blob[list_start + 1] = (count >> 8) & 0xFF
    blob[list_start + 2] = count & 0xFF


def _insert_record(context: ParsedInsertContext, record: bytes) -> tuple[bytes, FixupMetrics]:
    clan, mounts = _clan_parts(context)
    position = mounts.list_elements[-1].end_offset
    toc_index = next(entry.index for entry in context.parc.toc_entries
                     if context.parc.type_by_index[entry.class_index].name == "MercenaryClanSaveData")
    block_end = clan.data_offset + clan.data_size
    blob = bytearray(context.raw)
    blob[position:position] = record
    _set_mount_count(blob, mounts.start_offset, len(mounts.list_elements) + 1)
    trailing = _fixup_trailing_sizes(blob, context.raw, position, len(record),
                                     "MercenaryClanSaveData", result=context.result)
    pointers = _fixup_external(blob, context.raw, context.parc, toc_index, position, len(record))
    toc = sum(1 for entry in context.parc.toc_entries if entry.data_offset >= block_end)
    return bytes(blob), FixupMetrics(pointers, trailing, toc, 1, 1)


def _replace_record(context: ParsedInsertContext, element, record: bytes) -> tuple[bytes, FixupMetrics]:
    clan, _mounts = _clan_parts(context)
    start, end = element.start_offset, element.end_offset
    delta = len(record) - (end - start)
    toc_index = next(entry.index for entry in context.parc.toc_entries
                     if context.parc.type_by_index[entry.class_index].name == "MercenaryClanSaveData")
    block_end = clan.data_offset + clan.data_size
    blob = bytearray(context.raw)
    blob[start:end] = record
    trailing = _fixup_trailing_sizes(blob, context.raw, start, delta,
                                     "MercenaryClanSaveData", result=context.result)
    pointers = _fixup_external(blob, context.raw, context.parc, toc_index, end, delta)
    toc = sum(1 for entry in context.parc.toc_entries if entry.data_offset >= block_end)
    return bytes(blob), FixupMetrics(pointers, trailing, toc, 1, 1)


def _emit(callback, phase, completed, message):
    if callback:
        callback(BlackstarProgress(phase, completed, 6, message))


def unlock_blackstar(
    blob: bytes | bytearray,
    identity: SaveSchemaIdentity,
    dry_run: bool,
    operation_id: str,
    progress: Callable[[BlackstarProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> BlackstarResult:
    original = bytes(blob)
    started = time.perf_counter()
    context = build_insert_context(original)
    grant = require_blackstar_compatibility(context, identity)
    _emit(progress, "parse", 1, "Validated Blackstar compatibility")
    classification, found = _classify(context)
    _emit(progress, "detection", 2, f"Blackstar state: {classification}")
    if cancelled and cancelled():
        raise BlackstarCancelledError("Blackstar operation cancelled")
    if classification.startswith("legitimate"):
        report = BlackstarChangeReport(classification, "none", 1, 1, None, None,
                                       None, 0, 0, 0, FixupMetrics(),
                                       {"total": (time.perf_counter() - started) * 1000})
        for value, phase_name in ((3, "allocation"), (4, "candidate"), (5, "validation"), (6, "complete")):
            _emit(progress, phase_name, value, "No changes required")
        digest = hashlib.sha256(original).hexdigest()
        return BlackstarResult(digest, digest, None if dry_run else original,
                               grant.family_id, report)
    quest_before = canonical_root_snapshot(context, "QuestSaveData")
    knowledge_before = canonical_root_snapshot(context, "KnowledgeSaveData")
    mercenary_no, item_no = _allocate_ids(context)
    reference_key, _time, values = _reference_values(context)
    values = BlackstarDynamicValues(
        mercenary_no=mercenary_no, item_no=item_no, owner_key=values.owner_key,
        last_paid_time=values.last_paid_time, last_breeding_time=values.last_breeding_time,
        spawn_position=values.spawn_position, spawn_yaw=values.spawn_yaw,
        spawn_field_key=values.spawn_field_key,
    )
    _emit(progress, "allocation", 3, "Allocated collision-free instance numbers")
    _clan, mounts = _clan_parts(context)
    position = found[0][1].start_offset if classification == "legacy" else mounts.list_elements[-1].end_offset
    record = materialize_blackstar_template(context, position, values)
    with phase(
        log,
        operation_id,
        "blackstar_mount_insertion",
        classification=classification,
        record_bytes=len(record),
    ):
        if classification == "legacy":
            candidate, fixups = _replace_record(context, found[0][1], record)
            action = "replace"
        else:
            candidate, fixups = _insert_record(context, record)
            action = "insert"
    _emit(progress, "candidate", 4, f"Built Blackstar {action} candidate")
    candidate_context = build_insert_context(candidate)
    after_classification, after_found = _classify(candidate_context)
    if after_classification != "legitimate_idle" or len(after_found) != 1:
        raise BlackstarValidationError("Candidate Blackstar record did not validate")
    if canonical_root_snapshot(candidate_context, "QuestSaveData") != quest_before:
        raise BlackstarValidationError("Quest semantics changed")
    with phase(
        log,
        operation_id,
        "blackstar_knowledge_insertion",
        requested_changes=0,
    ):
        if canonical_root_snapshot(candidate_context, "KnowledgeSaveData") != knowledge_before:
            raise BlackstarValidationError("Knowledge semantics changed")
    _emit(progress, "validation", 5, "Validated unchanged quests and knowledge")
    input_hash = hashlib.sha256(original).hexdigest()
    candidate_hash = hashlib.sha256(candidate).hexdigest()
    report = BlackstarChangeReport(classification, action, 1 if classification == "legacy" else 0, 1, mercenary_no,
                                   item_no, reference_key, 0, 0,
                                   len(candidate) - len(original), fixups,
                                   {"total": (time.perf_counter() - started) * 1000})
    _emit(progress, "complete", 6, "Blackstar ownership candidate is valid")
    return BlackstarResult(input_hash, candidate_hash, None if dry_run else candidate,
                           grant.family_id, report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview legitimate Blackstar ownership")
    parser.add_argument("--save", required=True)
    parser.add_argument("--dry-run", action="store_true", required=True)
    args = parser.parse_args()
    from crimson.save_editor.save_crypto import load_save_file
    path = Path(args.save).resolve()
    fixture_root = (Path(__file__).resolve().parents[1] / "tests" / "fixtures").resolve()
    if fixture_root not in path.parents and Path(tempfile.gettempdir()).resolve() not in path.parents:
        parser.error("Dry-run input must be a copied save under tests/fixtures or temp")
    save = load_save_file(str(path))
    result = unlock_blackstar(save.decompressed_blob, save.schema_identity, True, "cli")
    print(json.dumps(asdict(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
