from __future__ import annotations

import argparse
import hashlib
import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from crimson.save_editor import parc_serializer as parc_serializer
from crimson.save_editor import save_parser as save_parser

REQUIRED_TYPES = (
    "MercenaryClanSaveData",
    "MercenarySaveData",
    "ExperienceLevelSaveData",
    "FriendlyDailyCountSaveData",
    "KnowledgeSaveData",
)


@dataclass(frozen=True)
class SaveSchemaIdentity:
    container_version: int
    schema_sha256: str
    root_entry_count: int
    type_count: int
    required_type_signatures: dict[str, str]
    observed_encodings: dict[str, str]


@dataclass(frozen=True)
class CompatibilityProfile:
    profile_id: str
    identity: SaveSchemaIdentity
    mount_list_prefix: int
    mount_element_mask_hex: str
    knowledge_list_prefix: int
    knowledge_element_mask_hex: str


class UnknownSaveSchemaError(ValueError):
    pass


def _type_signature(
    type_def: parc_serializer.TypeDef | save_parser.TypeDef,
) -> str:
    payload = [
        [field.name, field.type_name, field.meta_kind, field.meta_size, field.meta_aux]
        for field in type_def.fields
    ]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def extract_required_list_encodings(result: dict) -> dict[str, str]:
    targets = {
        "MercenaryClanSaveData": "_mercenaryDataList",
        "KnowledgeSaveData": "_list",
    }
    encodings: dict[str, str] = {}
    objects = {obj.class_name: obj for obj in result["objects"]}
    for class_name, field_name in targets.items():
        prefix_key = f"{class_name}.{field_name}.list_prefix"
        mask_key = f"{class_name}.{field_name}.element_mask"
        obj = objects.get(class_name)
        field = (
            next((item for item in obj.fields if item.name == field_name), None)
            if obj
            else None
        )
        elements = field.list_elements if field and field.list_elements else []
        encodings[prefix_key] = str(field.list_prefix_u8) if field else "MISSING"
        masks = sorted({
            element.child_mask_bytes.hex()
            for element in elements
            if element.child_mask_bytes
        })
        if masks:
            encodings[mask_key] = ",".join(masks)
        else:
            encodings[mask_key] = "MISSING"
    return encodings


def compute_schema_identity(blob: bytes, raw_header: bytes) -> SaveSchemaIdentity:
    schema = save_parser.parse_schema(blob)
    type_names = [type_def.name for type_def in schema["types"]]
    toc = save_parser.parse_toc(blob, schema["schema_end"], type_names)
    result = save_parser.build_result_from_layout(
        blob,
        {"input_kind": "raw_blob"},
        schema,
        toc,
        object_class_names={"MercenaryClanSaveData", "KnowledgeSaveData"},
    )
    by_name = {type_def.name: type_def for type_def in schema["types"]}
    signatures = {
        name: _type_signature(by_name[name]) if name in by_name else "MISSING"
        for name in REQUIRED_TYPES
    }
    version = struct.unpack_from("<H", raw_header, 4)[0] if len(raw_header) >= 6 else 0
    schema_bytes = blob[0x0E:schema["schema_end"]]
    return SaveSchemaIdentity(
        container_version=version,
        schema_sha256=hashlib.sha256(schema_bytes).hexdigest(),
        root_entry_count=struct.unpack_from("<I", schema_bytes)[0],
        type_count=len(schema["types"]),
        required_type_signatures=signatures,
        observed_encodings=extract_required_list_encodings(result),
    )


def load_profiles(path: Path | None = None) -> tuple[CompatibilityProfile, ...]:
    source = path or Path(__file__).with_name("save_schema_profiles.json")
    if not source.is_file():
        return ()
    data = json.loads(source.read_text(encoding="utf-8"))
    return tuple(
        CompatibilityProfile(
            profile_id=item["profile_id"],
            identity=SaveSchemaIdentity(**item["identity"]),
            mount_list_prefix=item["mount_list_prefix"],
            mount_element_mask_hex=item["mount_element_mask_hex"],
            knowledge_list_prefix=item["knowledge_list_prefix"],
            knowledge_element_mask_hex=item["knowledge_element_mask_hex"],
        )
        for item in data["profiles"]
    )


def match_profile(
    identity: SaveSchemaIdentity,
    profiles: tuple[CompatibilityProfile, ...],
) -> CompatibilityProfile | None:
    for profile in profiles:
        if not schema_structure_matches(identity, profile.identity):
            continue
        mount_prefix = identity.observed_encodings.get(
            "MercenaryClanSaveData._mercenaryDataList.list_prefix"
        )
        knowledge_prefix = identity.observed_encodings.get(
            "KnowledgeSaveData._list.list_prefix"
        )
        mount_masks = set(
            identity.observed_encodings.get(
                "MercenaryClanSaveData._mercenaryDataList.element_mask", ""
            ).split(",")
        )
        knowledge_masks = set(
            identity.observed_encodings.get(
                "KnowledgeSaveData._list.element_mask", ""
            ).split(",")
        )
        if (
            mount_prefix == str(profile.mount_list_prefix)
            and knowledge_prefix == str(profile.knowledge_list_prefix)
            and profile.mount_element_mask_hex in mount_masks
            and profile.knowledge_element_mask_hex in knowledge_masks
        ):
            return profile
    return None


def schema_structure_matches(
    left: SaveSchemaIdentity,
    right: SaveSchemaIdentity,
) -> bool:
    return (
        left.container_version == right.container_version
        and left.schema_sha256 == right.schema_sha256
        and left.root_entry_count == right.root_entry_count
        and left.type_count == right.type_count
        and left.required_type_signatures == right.required_type_signatures
        and left.observed_encodings.get(
            "MercenaryClanSaveData._mercenaryDataList.list_prefix"
        )
        == right.observed_encodings.get(
            "MercenaryClanSaveData._mercenaryDataList.list_prefix"
        )
        and left.observed_encodings.get("KnowledgeSaveData._list.list_prefix")
        == right.observed_encodings.get("KnowledgeSaveData._list.list_prefix")
    )


def require_supported_identity(
    identity: SaveSchemaIdentity,
    profiles: tuple[CompatibilityProfile, ...],
) -> CompatibilityProfile:
    profile = match_profile(identity, profiles)
    if profile is None:
        raise UnknownSaveSchemaError(
            f"Unknown save schema: schema_sha256={identity.schema_sha256} "
            f"container_version={identity.container_version}"
        )
    return profile


def _profile_from_fixture(
    fixture: Path,
    profile_id: str,
) -> CompatibilityProfile:
    from crimson.save_editor.save_crypto import load_save_file

    save = load_save_file(str(fixture))
    identity = save.schema_identity or compute_schema_identity(
        bytes(save.decompressed_blob), save.raw_header
    )
    result = save_parser.build_result_from_raw(
        bytes(save.decompressed_blob), {"input_kind": "profile_fixture"}
    )
    encodings = extract_required_list_encodings(result)
    mount_prefix = encodings[
        "MercenaryClanSaveData._mercenaryDataList.list_prefix"
    ]
    knowledge_prefix = encodings["KnowledgeSaveData._list.list_prefix"]
    mount_obj = next(
        obj for obj in result["objects"] if obj.class_name == "MercenaryClanSaveData"
    )
    knowledge_obj = next(
        obj for obj in result["objects"] if obj.class_name == "KnowledgeSaveData"
    )
    mount_field = next(
        item for item in mount_obj.fields if item.name == "_mercenaryDataList"
    )
    knowledge_field = next(item for item in knowledge_obj.fields if item.name == "_list")
    mount_mask = mount_field.list_elements[-1].child_mask_bytes.hex()
    knowledge_mask = knowledge_field.list_elements[-1].child_mask_bytes.hex()
    if "MISSING" in (mount_prefix, mount_mask, knowledge_prefix, knowledge_mask):
        raise UnknownSaveSchemaError("Fixture is missing a required Blackstar encoding")
    return CompatibilityProfile(
        profile_id=profile_id,
        identity=identity,
        mount_list_prefix=int(mount_prefix),
        mount_element_mask_hex=mount_mask,
        knowledge_list_prefix=int(knowledge_prefix),
        knowledge_element_mask_hex=knowledge_mask,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Enroll a copied save schema profile")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = _profile_from_fixture(args.fixture, args.profile_id)
    document = {"profiles": [asdict(profile)]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote compatibility profile {profile.profile_id} to {args.output}")


if __name__ == "__main__":
    main()
