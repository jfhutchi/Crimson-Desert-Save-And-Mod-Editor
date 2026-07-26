from __future__ import annotations

from dataclasses import dataclass

from crimson.save_editor.parc_inserter3 import (
    FixupMetrics,
    ParsedInsertContext,
    insert_knowledge_keys_with_context,
)

CALL_DRAGON_KNOWLEDGE_KEY = 1000175
CALL_DRAGON_FIELDS = {
    "_key": 4,
    "_level": 4,
    "_learnedFieldTime": 8,
    "_isNewMark": 1,
}


class CallDragonKnowledgeError(ValueError):
    pass


@dataclass(frozen=True)
class CallDragonKnowledgeState:
    status: str
    count: int
    level: int | None


def _value(raw: bytes, field) -> int:
    return int.from_bytes(raw[field.start_offset : field.end_offset], "little")


def knowledge_parts(context: ParsedInsertContext):
    roots = [
        obj
        for obj in context.result["objects"]
        if obj.class_name == "KnowledgeSaveData"
    ]
    if len(roots) != 1:
        raise CallDragonKnowledgeError("Expected exactly one KnowledgeSaveData")
    entries = next(
        (field for field in roots[0].fields if field.name == "_list"),
        None,
    )
    if not entries or entries.list_elements is None:
        raise CallDragonKnowledgeError("KnowledgeSaveData._list is missing")
    return roots[0], entries


def present_fields(element) -> dict[str, object]:
    return {
        field.name: field
        for field in element.child_fields or []
        if field.present
    }


def inspect_call_dragon(
    context: ParsedInsertContext,
) -> CallDragonKnowledgeState:
    _root, entries = knowledge_parts(context)
    matches = []
    for element in entries.list_elements:
        fields = present_fields(element)
        key = fields.get("_key")
        if key and key.end_offset - key.start_offset == 4:
            if _value(context.raw, key) == CALL_DRAGON_KNOWLEDGE_KEY:
                matches.append(fields)
    if not matches:
        return CallDragonKnowledgeState("missing", 0, None)
    if len(matches) != 1:
        raise CallDragonKnowledgeError(
            f"Duplicate Call Dragon knowledge entries: {len(matches)}"
        )
    level = matches[0].get("_level")
    if not level or level.end_offset - level.start_offset != 4:
        raise CallDragonKnowledgeError("Call Dragon knowledge level is malformed")
    value = _value(context.raw, level)
    if value < 1:
        raise CallDragonKnowledgeError("Call Dragon knowledge level is below 1")
    return CallDragonKnowledgeState("present", 1, value)


def select_call_dragon_template(context: ParsedInsertContext):
    _root, entries = knowledge_parts(context)
    candidates = []
    for element in entries.list_elements:
        fields = present_fields(element)
        widths = {
            name: field.end_offset - field.start_offset
            for name, field in fields.items()
        }
        if widths != CALL_DRAGON_FIELDS:
            continue
        if _value(context.raw, fields["_isNewMark"]) != 1:
            continue
        if element.end_offset - element.start_offset != 43:
            continue
        candidates.append(element)
    if not candidates:
        raise CallDragonKnowledgeError(
            "No compatible target-local Call Dragon template"
        )
    return candidates[-1]


def insert_call_dragon(
    context: ParsedInsertContext,
) -> tuple[bytes, FixupMetrics]:
    if inspect_call_dragon(context).status == "present":
        return context.raw, FixupMetrics()
    template = select_call_dragon_template(context)
    return insert_knowledge_keys_with_context(
        context,
        [CALL_DRAGON_KNOWLEDGE_KEY],
        override_level=1,
        template_element=template,
    )
