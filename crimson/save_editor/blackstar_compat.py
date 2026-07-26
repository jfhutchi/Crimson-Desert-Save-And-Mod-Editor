from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from crimson.save_editor.blackstar_knowledge import (
    CallDragonKnowledgeError,
    inspect_call_dragon,
    select_call_dragon_template,
)
from crimson.save_editor.parc_inserter3 import ParsedInsertContext
from crimson.save_editor.save_compat import SaveSchemaIdentity, _type_signature

BLACKSTAR_FAMILY_ID = "blackstar-owner-114-v1"
BLACKSTAR_TYPE_SIGNATURES = {
    "MercenaryClanSaveData": "dfd071099c52729b1a4a81d8227feae5e1369d7a78382e0b4c493e82b36f9118",
    "MercenarySaveData": "9eb3b14c5c3bcfb28010eb0e6213a253a4813a13645f37ee37e2344c063b2972",
    "ExperienceLevelSaveData": "f582f2a269e3d5fa443b6c11b4648e52052a2495df1461d66cde28b9754c67d5",
    "FriendlyDailyCountSaveData": "e901369cd8b8602e892c19be4a7832e7bbd5ef927690dbfab5af605c7150be50",
    "ItemSaveData": "6d245a805bcc83cc5631563c79e7918f8dfd1ba1ee21bfb8e281ec7dfbf426ff",
    "ItemSocketSaveData": "6198ec9b4b46a21482e1ba713d877388c9c58daac5c82827eae22854e5741979",
    "KnowledgeSaveData": "124fdeffcc6583cb99cc82640364591e9d616d8bcca79ddaf77605606097029e",
}


@dataclass(frozen=True)
class BlackstarCompatibilityGrant:
    family_id: str
    container_version: int
    source_schema_sha256: str
    source_type_count: int
    source_root_count: int
    touched_type_signatures: tuple[tuple[str, str], ...]


class BlackstarCompatibilityError(ValueError):
    pass


@dataclass(frozen=True)
class BlackstarApplyToken:
    family_id: str
    source_path: str
    source_file_sha256: str
    source_blob_sha256: str
    source_schema_sha256: str
    candidate_blob_sha256: str
    document_generation: int


def make_blackstar_apply_token(
    source_path: str | Path,
    source_blob: bytes,
    identity: SaveSchemaIdentity,
    candidate_blob_sha256: str,
    generation: int,
    *,
    source_file_sha256: str,
) -> BlackstarApplyToken:
    path = Path(source_path).resolve()
    return BlackstarApplyToken(
        family_id=BLACKSTAR_FAMILY_ID,
        source_path=str(path),
        source_file_sha256=source_file_sha256,
        source_blob_sha256=hashlib.sha256(source_blob).hexdigest(),
        source_schema_sha256=identity.schema_sha256,
        candidate_blob_sha256=candidate_blob_sha256,
        document_generation=generation,
    )


def require_blackstar_compatibility(
    context: ParsedInsertContext,
    identity: SaveSchemaIdentity | None,
) -> BlackstarCompatibilityGrant:
    if identity is None:
        raise BlackstarCompatibilityError("Save has no schema identity")
    if identity.container_version != 2:
        raise BlackstarCompatibilityError(
            f"Unsupported container version {identity.container_version}"
        )
    if identity.root_entry_count != 15:
        raise BlackstarCompatibilityError(
            f"Unsupported root entry count {identity.root_entry_count}"
        )
    by_name = {item.name: item for item in context.parc.types}
    observed = tuple(
        (name, _type_signature(by_name[name]) if name in by_name else "MISSING")
        for name in BLACKSTAR_TYPE_SIGNATURES
    )
    expected = tuple(BLACKSTAR_TYPE_SIGNATURES.items())
    if observed != expected:
        differences = [
            name for (name, actual), (_, wanted) in zip(observed, expected)
            if actual != wanted
        ]
        raise BlackstarCompatibilityError(
            "Blackstar type signatures differ: " + ", ".join(differences)
        )
    objects = {obj.class_name: obj for obj in context.result["objects"]}
    for required in ("MercenaryClanSaveData", "KnowledgeSaveData", "QuestSaveData"):
        if required not in objects:
            raise BlackstarCompatibilityError(f"Required root object missing: {required}")
    try:
        call_dragon = inspect_call_dragon(context)
        if call_dragon.status == "missing":
            select_call_dragon_template(context)
    except CallDragonKnowledgeError as exc:
        raise BlackstarCompatibilityError(str(exc)) from exc
    mount_field = next(
        (field for field in objects["MercenaryClanSaveData"].fields
         if field.name == "_mercenaryDataList"), None
    )
    if not mount_field or not mount_field.list_elements:
        raise BlackstarCompatibilityError("Mercenary mount list is missing or empty")
    if mount_field.list_prefix_u8 != 1:
        raise BlackstarCompatibilityError("Unsupported mercenary list prefix")
    if any(len(element.child_mask_bytes) != 6 for element in mount_field.list_elements):
        raise BlackstarCompatibilityError("Unsupported mercenary element mask width")
    return BlackstarCompatibilityGrant(
        family_id=BLACKSTAR_FAMILY_ID,
        container_version=identity.container_version,
        source_schema_sha256=identity.schema_sha256,
        source_type_count=identity.type_count,
        source_root_count=identity.root_entry_count,
        touched_type_signatures=observed,
    )
