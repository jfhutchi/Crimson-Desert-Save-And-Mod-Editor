"""Legacy JSON byte-patch inspector.

Crimson Desert's `iteminfo.pabgb` mods commonly ship as format:2 JSON with
byte-patch directives:

    { "type": "replace", "entry": "Item_X",
      "rel_offset": 266, "original": "00", "patched": "01" }

These work by writing the `patched` bytes at `entry_start + rel_offset`.
They break when:
 - the game updates and the field offset shifts → patch lands on the wrong field
 - another mod expands the same entry → every following rel_offset is off
 - the `original` bytes stop matching → patch is silently skipped

This module converts byte patches into semantic (field-level) patches by
resolving each `(entry, rel_offset)` to a field path via the tracked
reader in crimson_rs. The result:

 - **Inspector readout**: "patch #7 sets `cooltime` on `Item_Apple_Pie` to 0"
   — visible in the Stacker Details pane regardless of whether the patch
   applied cleanly or was skipped.
 - **Semantic-merge mode**: the patched bytes are interpreted as that
   field's type (u8/u32/i64/etc.), dropped onto the dict directly, and
   merged with other mods at the field level. Survives offset drift.

The tracking primitive is `crimson_rs.inspect_legacy_patches(bytes,
patches)` — it does the parse + binary-search in Rust, so the whole pass
over a ~100-patch mod costs ~0.7s (one vanilla parse).
"""
from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass, field
from typing import Optional

from crimson.game_mods import crimson_rs as crimson_rs

log = logging.getLogger(__name__)


# ── inspect_legacy_patches implementation ─────────────────────────
# This function was planned for crimson_rs but never added to the Rust
# PyO3 bindings. We implement it in Python using parse_iteminfo_tracked
# which DOES exist and provides the byte-range spans we need.
#
# Monkey-patched onto crimson_rs so all call sites
# (`crimson_rs.inspect_legacy_patches(...)`) work without changes.

_tracked_cache: dict[int, tuple[dict, dict]] = {}


def _get_tracked(vanilla_bytes: bytes):
    """Cache the tracked parse result keyed by id(bytes) to avoid
    re-parsing the same vanilla on every call."""
    key = id(vanilla_bytes)
    if key in _tracked_cache:
        return _tracked_cache[key]

    result = crimson_rs.parse_iteminfo_tracked(vanilla_bytes)
    items = result["items"]
    spans = result["spans"]

    # Build lookup: string_key → (entry_start_abs, ranges_list)
    # Note: span["start"] and ranges[i]["start"] may use different bases
    # due to a doubling in the tracked reader. Use ranges[0]["start"] as
    # the true absolute start since it's consistent with all range offsets.
    entry_index = {}
    for item, span in zip(items, spans):
        sk = item.get("string_key", "")
        if sk and span["ranges"]:
            true_start = span["ranges"][0]["start"]
            entry_index[sk] = (true_start, span["ranges"])

    _tracked_cache[key] = (entry_index, result)
    if len(_tracked_cache) > 3:
        oldest = next(iter(_tracked_cache))
        del _tracked_cache[oldest]

    return entry_index, result


def _inspect_legacy_patches(vanilla_bytes: bytes, queries: list[dict]
                            ) -> list[dict | None]:
    """Resolve (entry, rel_offset) queries to field paths using the
    tracked reader's byte-range spans.

    Each query is {"entry": str, "rel_offset": int, "length": int}.
    Returns parallel list: dict with field info or None if not found.
    """
    entry_index, _ = _get_tracked(vanilla_bytes)

    results = []
    for q in queries:
        entry_name = q.get("entry", "")
        rel_offset = q.get("rel_offset", -1)
        length = q.get("length", 1)

        lookup = entry_index.get(entry_name)
        if lookup is None:
            results.append(None)
            continue

        entry_start, ranges = lookup

        # Ranges use absolute offsets into the file.
        # rel_offset is relative to entry_start.
        abs_offset = entry_start + rel_offset

        # Find which range contains this absolute offset
        hit = None
        for r in ranges:
            if r["start"] <= abs_offset < r["end"]:
                hit = r
                break

        if hit is None:
            results.append(None)
            continue

        abs_end = abs_offset + length
        # Convert back to entry-relative for the caller
        results.append({
            "path": hit["path"],
            "ty": hit["ty"],
            "field_start_rel": hit["start"] - entry_start,
            "field_end_rel": hit["end"] - entry_start,
            "byte_offset_in_field": abs_offset - hit["start"],
            "spans_field_end": abs_end > hit["end"],
        })

    return results


if not hasattr(crimson_rs, "inspect_legacy_patches"):
    crimson_rs.inspect_legacy_patches = _inspect_legacy_patches


# Status values for PatchInspection. "applied" = bytes matched vanilla and
# we overwrote. "stale" = original mismatch (different game version).
# "no_entry" = entry name not in vanilla (renamed upstream). "no_field" =
# rel_offset fell outside any recorded field range (shouldn't happen with
# a well-formed mod). "unsupported" = patch type we can't handle yet
# (insert / delete).
PATCH_APPLIED = "applied"
PATCH_STALE = "stale"
PATCH_NO_ENTRY = "no_entry"
PATCH_NO_FIELD = "no_field"
PATCH_UNSUPPORTED = "unsupported"


@dataclass
class PatchInspection:
    """Per-patch field attribution and outcome.

    `status` distinguishes "we wrote the bytes" from "we know what field
    was intended but couldn't write it" — the Details pane surfaces both
    so the user understands what they lost.
    """
    index: int                        # 0-based position within the JSON's patches array
    entry: str                        # entry name from the JSON
    rel_offset: int                   # as authored in the JSON
    orig_hex: str                     # from JSON (may be empty)
    new_hex: str                      # from JSON
    # Field resolution (None when entry missing or offset out of range)
    field_path: Optional[str] = None
    field_ty: Optional[str] = None
    field_start_rel: Optional[int] = None
    field_end_rel: Optional[int] = None
    byte_offset_in_field: Optional[int] = None
    spans_field_end: bool = False     # True if patch extends past the resolved field
    # Decoded values (best-effort; None when we can't decode for this ty)
    old_value: object = None
    new_value: object = None
    status: str = PATCH_APPLIED       # see constants above
    status_note: str = ""             # free-form extra detail


# -----------------------------------------------------------------------------
#   Patch gathering from legacy JSON doc
# -----------------------------------------------------------------------------

def collect_iteminfo_patches(doc: dict) -> list[dict]:
    """Pull every iteminfo-targeted patch out of a legacy JSON doc.

    Returns a flat list preserving the patch's source index so the
    Inspector report can cite it ("patch #7 in this JSON touches …").
    """
    out = []
    idx = 0
    for patch in doc.get("patches", []):
        gf = (patch.get("game_file") or "").lower()
        if "iteminfo.pabgb" not in gf:
            continue
        for change in patch.get("changes", []):
            change["_source_index"] = idx
            idx += 1
            out.append(change)
    return out


# -----------------------------------------------------------------------------
#   Inspector pass
# -----------------------------------------------------------------------------

def inspect_patches(vanilla_bytes: bytes, changes: list[dict]
                    ) -> list[PatchInspection]:
    """Resolve each change's (entry, rel_offset) to a field path, decode
    old/new values, and classify the outcome.

    Runs `crimson_rs.inspect_legacy_patches` once over the batch — O(N
    parse) vs per-patch parse. Returns parallel list; one PatchInspection
    per input change, in input order.
    """
    # Build the Rust-side query list for changes with resolvable
    # (entry, rel_offset). Non-replace or offset-only changes get a
    # placeholder result.
    rust_queries: list[dict] = []
    rust_map: list[int] = []  # rust_queries[i] corresponds to changes[rust_map[i]]

    results: list[PatchInspection] = []
    for i, change in enumerate(changes):
        ctype = change.get("type", "replace")
        entry = change.get("entry")
        rel = change.get("rel_offset")
        orig_hex = change.get("original", "") or ""
        new_hex = change.get("patched", "") or ""
        insp = PatchInspection(
            index=change.get("_source_index", i),
            entry=entry or "",
            rel_offset=int(rel) if isinstance(rel, (int, str)) and str(rel).lstrip("-").isdigit() else -1,
            orig_hex=orig_hex,
            new_hex=new_hex,
        )
        if ctype != "replace":
            insp.status = PATCH_UNSUPPORTED
            insp.status_note = f"type={ctype!r} (only 'replace' is understood)"
            results.append(insp)
            continue
        if entry is None or rel is None:
            # Raw absolute-offset patches — we could still resolve them by
            # scanning ranges, but nobody emits these today. Mark as
            # unsupported so the user sees they landed outside the
            # inspector.
            insp.status = PATCH_UNSUPPORTED
            insp.status_note = "absolute offset (no entry+rel_offset)"
            results.append(insp)
            continue
        try:
            length = len(bytes.fromhex(new_hex)) if new_hex else 0
        except ValueError:
            insp.status = PATCH_UNSUPPORTED
            insp.status_note = "malformed 'patched' hex"
            results.append(insp)
            continue
        rust_queries.append({
            "entry": entry,
            "rel_offset": int(rel),
            "length": length,
        })
        rust_map.append(i)
        results.append(insp)

    if not rust_queries:
        return results

    hits = crimson_rs.inspect_legacy_patches(vanilla_bytes, rust_queries)

    # Splice hits back into the results and decode values.
    for q_idx, hit in enumerate(hits):
        r_idx = rust_map[q_idx]
        insp = results[r_idx]
        if hit is None:
            # Either the entry isn't in vanilla or rel_offset is OOB for
            # that entry. We can't distinguish cheaply without another
            # Rust call, but the Rust inspector returns None only for
            # "no such entry" or "offset outside ranges"; try to split.
            insp.status = PATCH_NO_ENTRY  # default assumption
            continue
        insp.field_path = hit["path"]
        insp.field_ty = hit["ty"]
        insp.field_start_rel = hit["field_start_rel"]
        insp.field_end_rel = hit["field_end_rel"]
        insp.byte_offset_in_field = hit["byte_offset_in_field"]
        insp.spans_field_end = bool(hit.get("spans_field_end") or False)

    # Separate pass: for each insp without a hit, distinguish no_entry vs
    # no_field by probing the entry alone.
    to_probe = [i for i in rust_map if results[i].field_path is None
                and results[i].status != PATCH_UNSUPPORTED]
    if to_probe:
        entries_to_probe = list({results[i].entry for i in to_probe})
        # One probe per unique entry: a rel_offset=0 lookup — if it comes
        # back None, the entry is missing; if it comes back hit, the
        # entry exists but our rel_offset was OOB (= no_field).
        probe_queries = [{"entry": e, "rel_offset": 0, "length": 1}
                         for e in entries_to_probe]
        probe_hits = crimson_rs.inspect_legacy_patches(vanilla_bytes, probe_queries)
        entry_exists = {e: (h is not None) for e, h in zip(entries_to_probe, probe_hits)}
        for i in to_probe:
            insp = results[i]
            if entry_exists.get(insp.entry):
                insp.status = PATCH_NO_FIELD
                insp.status_note = (
                    f"rel_offset {insp.rel_offset} falls outside "
                    f"any recorded field range for this entry"
                )
            else:
                insp.status = PATCH_NO_ENTRY
                insp.status_note = f"entry {insp.entry!r} not found in vanilla"

    # Decode old/new values for primitive field types, and detect stale
    # patches (original hex doesn't match vanilla bytes).
    for insp in results:
        if insp.field_path is None:
            continue
        insp.old_value = _decode_field_value(
            vanilla_bytes, insp.field_ty,
            # We have field_start_rel (from entry_start) but not
            # entry_start. Re-derive via the field start/end from the
            # Rust hit if we kept it — but we didn't. Instead, decode
            # from the patch's new_hex for the new_value (we know where
            # in the field to splice), and skip old_value if we can't.
            None, None,
        )
        try:
            new_bytes = bytes.fromhex(insp.new_hex)
            # Best-effort decode of the new value assuming the patch
            # covers the whole field (the common case: rel_offset==
            # field_start, len == field_end-field_start). When the patch
            # is a sub-range we just report the patched bytes.
            if (insp.byte_offset_in_field == 0
                and len(new_bytes) == (insp.field_end_rel - insp.field_start_rel)):
                insp.new_value = _decode_primitive(insp.field_ty, new_bytes)
            else:
                insp.new_value = None  # sub-field patch — defer rendering
        except ValueError:
            insp.status = PATCH_UNSUPPORTED
            insp.status_note = "malformed 'patched' hex"
            continue

        # Stale detection — does the patch's 'original' still match
        # vanilla at the absolute offset?
        if insp.orig_hex:
            try:
                orig = bytes.fromhex(insp.orig_hex)
            except ValueError:
                insp.status = PATCH_UNSUPPORTED
                insp.status_note = "malformed 'original' hex"
                continue
            # Absolute vanilla offset = field_start + byte_offset_in_field
            # But field_start is absolute; we stored field_start_rel (rel
            # to entry_start). Re-query via rust hit dict? We didn't keep
            # the absolute. Simpler: the patch as authored targeted rel_
            # offset relative to entry_start; entry_start lookup via the
            # patcher is done elsewhere. Here we can't recheck without
            # the entry_start map. Fortunately _apply_legacy_json still
            # runs in parallel and flips status → PATCH_STALE when the
            # byte splice fails. We'll accept that authority.
            pass

    return results


def _decode_primitive(ty: Optional[str], buf: bytes) -> object:
    """Decode a primitive field's bytes into a Python value for display
    and semantic-merge. Returns None if type isn't decodable or length
    doesn't match (patches that cover sub-ranges of composite fields)."""
    if not ty or not buf:
        return None
    try:
        if ty == "u8" and len(buf) == 1:
            return buf[0]
        if ty == "u16" and len(buf) == 2:
            return struct.unpack("<H", buf)[0]
        if ty == "u32" and len(buf) == 4:
            return struct.unpack("<I", buf)[0]
        if ty == "u64" and len(buf) == 8:
            return struct.unpack("<Q", buf)[0]
        if ty == "i8" and len(buf) == 1:
            return struct.unpack("<b", buf)[0]
        if ty == "i32" and len(buf) == 4:
            return struct.unpack("<i", buf)[0]
        if ty == "i64" and len(buf) == 8:
            return struct.unpack("<q", buf)[0]
        if ty == "f32" and len(buf) == 4:
            return struct.unpack("<f", buf)[0]
    except struct.error:
        return None
    return None


def _decode_field_value(data, ty, start, end):
    """Placeholder — unused. We intentionally skip old_value decoding
    from the whole-file vanilla here because we don't have the absolute
    field offset stored on the PatchInspection. The Details pane only
    shows new_value, which is enough for the user to verify intent."""
    return None


# -----------------------------------------------------------------------------
#   Semantic apply — convert PatchInspections to dict-level edits
# -----------------------------------------------------------------------------

def apply_semantic(items: list[dict], inspections: list[PatchInspection]
                   ) -> tuple[int, int, list[str]]:
    """Apply field-level edits directly to the parsed dict list.

    Used when the user enables Semantic mode on a legacy JSON source —
    bypasses byte-patching entirely, so offset drift from other mods
    (e.g. Super MOD adding entries, another mod expanding a CArray) no
    longer breaks these patches.

    Only applies patches where:
     - field resolved (not `no_entry` / `no_field` / `unsupported`)
     - patch covers the whole field (byte_offset_in_field==0 AND
       length matches field size) — partial-field patches are skipped
       because we can't split a primitive into sub-bytes on the dict
       side without reconstructing surrounding struct bytes too.
     - field type is a known primitive (u8/u16/u32/u64/i8/i32/i64/f32)
       — composite patches (CString payload rewrites, CArray size
       changes) need explicit handling per-type, deferred until the
       first real-world case shows up.

    Mutates `items` in place. Returns (applied_count, skipped_count,
    skipped_reasons).
    """
    # Index items by string_key for O(1) lookup. `items` is the merged
    # dict list or vanilla dict list; either way each entry has a
    # string_key matching what the JSON's `entry` field refers to.
    idx = {}
    for it in items:
        k = it.get("string_key")
        if k:
            idx[k] = it

    applied = 0
    skipped_reasons: list[str] = []
    for insp in inspections:
        if insp.status != PATCH_APPLIED:
            # byte-apply already failed or patch unsupported
            # — semantic apply isn't going to rescue it
            skipped_reasons.append(
                f"patch #{insp.index} ({insp.field_path or '?'}): "
                f"{insp.status} — {insp.status_note}"
            )
            continue
        if insp.field_path is None or insp.new_value is None:
            skipped_reasons.append(
                f"patch #{insp.index}: no field resolution or "
                f"partial-field patch (not yet supported in semantic mode)"
            )
            continue
        item = idx.get(insp.entry)
        if item is None:
            skipped_reasons.append(
                f"patch #{insp.index}: entry {insp.entry!r} missing "
                "in merge result (removed by upstream mod?)"
            )
            continue
        if not _set_dict_path(item, insp.field_path, insp.new_value):
            skipped_reasons.append(
                f"patch #{insp.index}: dict path {insp.field_path!r} "
                "not writable in merged entry"
            )
            continue
        applied += 1
    return applied, len(skipped_reasons), skipped_reasons


def _set_dict_path(obj: dict, path: str, value: object) -> bool:
    """Set a dotted path (`a.b[0].c`) on a nested dict/list tree.

    Paths generated by crimson-rs use `[i]` for array indices and `.`
    for nested struct fields. `__count__` and `__len__` segments are
    bookkeeping fields, not writable on the dict side — if one shows up
    here, the patch was editing a count/length prefix directly, which
    the dict model doesn't let us express (the serializer derives them
    from the list length). Skip those patches.
    """
    parts = _split_path(path)
    if any(p in ("__count__", "__len__", "__tag__") for p, _ in parts):
        return False
    cur = obj
    for key, idx in parts[:-1]:
        if idx is None:
            nxt = cur.get(key) if isinstance(cur, dict) else None
        else:
            container = cur.get(key) if isinstance(cur, dict) else None
            if not isinstance(container, list) or idx >= len(container):
                return False
            nxt = container[idx]
        if nxt is None:
            return False
        cur = nxt
    last_key, last_idx = parts[-1]
    if last_idx is None:
        if not isinstance(cur, dict):
            return False
        cur[last_key] = value
    else:
        container = cur.get(last_key) if isinstance(cur, dict) else None
        if not isinstance(container, list) or last_idx >= len(container):
            return False
        container[last_idx] = value
    return True


def _split_path(path: str) -> list[tuple[str, Optional[int]]]:
    """Split `a.b[2].c[0]` into [("a",None),("b",2),("c",0)]."""
    out = []
    for seg in path.split("."):
        # Each segment is either `name` or `name[i]` or `name[i][j]` (rare).
        name = seg
        while True:
            lb = name.find("[")
            if lb < 0:
                out.append((name, None))
                break
            rb = name.find("]", lb)
            if rb < 0:
                out.append((name, None))
                break
            base = name[:lb]
            try:
                idx = int(name[lb + 1:rb])
            except ValueError:
                out.append((name, None))
                break
            out.append((base, idx))
            name = name[rb + 1:]
            if not name:
                break
            # Remaining part is another [i] — parse as nested array index
            # with empty base name. We record it as ("", i); _set_dict_path
            # will see an empty key and fail gracefully — path formats
            # produced by the Rust reader don't chain array subscripts on
            # the same field today, so this branch is defensive.
    return out


# -----------------------------------------------------------------------------
#   Human-readable summary for the Details pane
# -----------------------------------------------------------------------------

# Danger signals — field-path suffixes that indicate byte-patching
# structurally unsafe positions. Writing these requires coordinating
# multiple patches atomically, which JMM's byte-apply can't guarantee
# across game versions or when other mods expand entries. See
# _analyze_accessory_mod.py for the forensic walkthrough.
#
# `.__count__`  = CArray element count prefix. If the count is bumped
#                 without also inserting matching element data (and
#                 vice-versa), the stream desynchronizes and the entire
#                 file becomes unparseable.
# `.__len__`    = CString length prefix. Same problem — length without
#                 matching payload bytes truncates or over-reads.
# `.__tag__`    = COptional presence tag. Flipping tag from 0 to 1
#                 without supplying the inner struct's bytes leaves
#                 the reader consuming arbitrary subsequent fields.
_DANGEROUS_FIELD_SUFFIXES = (".__count__", ".__len__", ".__tag__")


def count_dangerous_patches(inspections: list[PatchInspection]) -> dict:
    """Return {'count_prefix': N, 'len_prefix': N, 'tag_prefix': N,
    'split_primitive': N}.

    split_primitive = patches whose byte offset lands INSIDE a primitive
    field (byte_offset_in_field > 0 or length extends past field_end).
    That's the other major corruption mode — a 4-byte insert 1 byte into
    a u32 splits the primitive and shifts every subsequent read.
    """
    out = {"count_prefix": 0, "len_prefix": 0, "tag_prefix": 0,
           "split_primitive": 0}
    for i in inspections:
        fp = i.field_path or ""
        if fp.endswith(".__count__"):
            out["count_prefix"] += 1
        elif fp.endswith(".__len__"):
            out["len_prefix"] += 1
        elif fp.endswith(".__tag__"):
            out["tag_prefix"] += 1
        # split-primitive check: the patch's byte_offset_in_field is
        # non-zero OR the patch extends past field_end. Both mean the
        # patch's bytes don't align with a single primitive.
        if (i.byte_offset_in_field and i.byte_offset_in_field > 0) or \
           i.spans_field_end:
            out["split_primitive"] += 1
    return out


def format_danger_warnings(inspections: list[PatchInspection],
                            has_inserts: bool = False) -> list[str]:
    """Return UI lines warning about structurally unsafe patches.
    Empty list if the mod is clean. Flagged sources should be offered
    a prompt to switch to Reparse-Diff mode."""
    danger = count_dangerous_patches(inspections)
    lines: list[str] = []
    total_prefix = danger["count_prefix"] + danger["len_prefix"] + danger["tag_prefix"]
    if total_prefix or danger["split_primitive"] or has_inserts:
        lines.append("  CORRUPTION RISK:")
        if danger["count_prefix"]:
            lines.append(
                f"    ! {danger['count_prefix']} patch(es) write to CArray "
                ".__count__ prefixes. If the count and its paired element "
                "data don't land together, the whole file desyncs.")
        if danger["len_prefix"]:
            lines.append(
                f"    ! {danger['len_prefix']} patch(es) write to CString "
                ".__len__ prefixes. Same risk — length without matching "
                "payload truncates or over-reads.")
        if danger["tag_prefix"]:
            lines.append(
                f"    ! {danger['tag_prefix']} patch(es) write to COptional "
                ".__tag__ bytes. Flipping tag without inner data shifts "
                "every later read.")
        if danger["split_primitive"]:
            lines.append(
                f"    ! {danger['split_primitive']} patch(es) split "
                "primitive fields (offset lands inside a field or extends "
                "past it). Both halves corrupt.")
        if has_inserts:
            lines.append(
                "    ! This mod uses `insert` patches. Paired byte "
                "inserts are extremely fragile across game versions and "
                "when stacked with other mods.")
        lines.append("    -> Switch this source to Reparse-Diff mode (RPD)")
        lines.append("       — it translates the whole patch set into")
        lines.append("       semantic FieldEdits that flow through the")
        lines.append("       safe serialize pipeline, avoiding all of the")
        lines.append("       above corruption vectors.")
        lines.append("")
    return lines


def format_inspection_summary(inspections: list[PatchInspection],
                               max_rows: int = 40) -> list[str]:
    """Return a list of text lines for the Details pane."""
    if not inspections:
        return ["  (no patches found in JSON)"]
    counts = {PATCH_APPLIED: 0, PATCH_STALE: 0, PATCH_NO_ENTRY: 0,
              PATCH_NO_FIELD: 0, PATCH_UNSUPPORTED: 0}
    for i in inspections:
        counts[i.status] = counts.get(i.status, 0) + 1
    lines = [
        f"  {counts[PATCH_APPLIED]} applied  "
        f"· {counts[PATCH_STALE]} stale (original-mismatch)  "
        f"· {counts[PATCH_NO_ENTRY]} missing-entry  "
        f"· {counts[PATCH_NO_FIELD]} offset-out-of-range  "
        f"· {counts[PATCH_UNSUPPORTED]} unsupported",
    ]
    lines.append("")
    lines.append("  Per-patch field attribution:")
    for i, insp in enumerate(inspections[:max_rows]):
        marker = {
            PATCH_APPLIED: "✔",
            PATCH_STALE: "⚠",
            PATCH_NO_ENTRY: "⨯",
            PATCH_NO_FIELD: "?",
            PATCH_UNSUPPORTED: "·",
        }.get(insp.status, "?")
        if insp.field_path:
            val = ("" if insp.new_value is None
                   else f"  →  {insp.new_value!r}")
            lines.append(
                f"    {marker} #{insp.index:<3} {insp.entry}."
                f"{insp.field_path} [{insp.field_ty}]{val}"
            )
        else:
            lines.append(
                f"    {marker} #{insp.index:<3} {insp.entry} @ rel_offset="
                f"{insp.rel_offset}  ({insp.status}: {insp.status_note})"
            )
    if len(inspections) > max_rows:
        lines.append(f"    … {len(inspections) - max_rows} more "
                     "(truncated for UI)")
    return lines


# -----------------------------------------------------------------------------
#   Mark stale status post-byte-apply
# -----------------------------------------------------------------------------

# =============================================================================
#   Reparse-diff mode
# =============================================================================
#
# The Inspector above resolves individual `{entry, rel_offset, patched}`
# directives to field paths. That works for pure primitive `replace`
# patches but gives up on:
#   - stale patches (`original` mismatch) — even when the field resolved,
#     `apply_semantic` refuses to write
#   - `insert` patches that expand a CArray or add a whole new entry
#   - `replace` patches using `offset` (absolute) instead of `entry+rel_offset`
#
# Reparse-diff is a simpler, more forgiving technique that handles every
# case uniformly:
#
#   1. Apply EVERY patch to a copy of vanilla bytes.
#   2. Try to parse the result with `crimson_rs.parse_iteminfo_from_bytes`.
#   3. If parse succeeds, diff the modded dict list against vanilla's.
#      Every changed leaf, every CArray length delta, every new or
#      missing entry becomes a `FieldEdit`.
#   4. If parse fails, fall back to single-patch isolation: apply each
#      patch alone, try to reparse, diff. Patches that break parsing in
#      isolation are reported as `parse_break` and carry an error note.
#
# The output `FieldEdit` list is composable with everything else Stacker
# does: it flows into `apply_field_edits(items, edits)` which mutates
# the dict tree. From there the normal serialize pipeline produces the
# merged iteminfo.pabgb, no byte offsets involved.


SET = "set"                  # primitive field value change
APPEND = "append"            # CArray gained element(s) at the tail
REMOVE_IDX = "remove_idx"    # CArray lost element(s) at specified indices
ADD_ENTRY = "add_entry"      # a new top-level item appeared in modded
REMOVE_ENTRY = "remove_entry"   # a vanilla item disappeared in modded


@dataclass
class FieldEdit:
    """A single semantic change produced by `reparse_diff_patches`.

    `op` is one of the module-level constants above. `entry` is the
    item's string_key (for ADD_ENTRY it's the new item's key). `path`
    is the dotted field path (empty for entry-level ops). `value` is
    the intended new value — shape depends on op:
      SET         → the new primitive/dict/list value for `path`
      APPEND      → list of new elements to append to the CArray at `path`
      REMOVE_IDX  → list of int indices to drop from the CArray at `path`
      ADD_ENTRY   → the full item dict to insert
      REMOVE_ENTRY→ None
    """
    op: str
    entry: str
    path: str
    value: object = None
    # For traceability — which patch indices in the source JSON produced
    # this edit (usually one; absolute-offset patches landing inside an
    # entry can produce several together).
    source_patches: list = field(default_factory=list)
    # Optional human note (e.g. "CArray length went 3 → 5 in modded").
    note: str = ""


@dataclass
class ReparseDiffReport:
    """Result of `reparse_diff_patches`. Carries the field edits plus
    per-patch outcome so the UI can report what each patch contributed
    to (or which patch broke parsing)."""
    edits: list[FieldEdit] = field(default_factory=list)
    # `mode` tells the user what path succeeded — "full_reparse" for the
    # happy path; "per_patch_isolation" when we had to fall back because
    # the full-file reparse broke.
    mode: str = "full_reparse"
    # Patches that landed but whose deltas we couldn't capture (parsing
    # failed on them in isolation). Parallel list of indices into the
    # flattened changes array.
    parse_break_patches: list[int] = field(default_factory=list)
    # Patches that contributed zero observable delta even though they
    # byte-applied. Usually means the patch wrote the same value vanilla
    # already had (idempotent no-op). Not an error; users can ignore.
    no_op_patches: list[int] = field(default_factory=list)
    # Patches that couldn't be applied at all (hex parse, out-of-range,
    # entry missing). Parallel to the inspector's own skip categories.
    unapplied_patches: list[tuple[int, str]] = field(default_factory=list)


def reparse_diff_patches(vanilla_bytes: bytes, doc: dict,
                         entry_blob_start: dict) -> ReparseDiffReport:
    """Translate a legacy JSON's intent into a list of FieldEdits by
    applying the patches to vanilla bytes, reparsing, and diffing.

    This handles stale/insert/absolute-offset patches uniformly. Users
    get every capturable edit even when individual patches can't be
    byte-applied cleanly.
    """
    # Flatten all changes into (index, change) pairs preserving input order.
    all_changes: list[dict] = []
    for patch in doc.get("patches", []):
        gf = (patch.get("game_file") or "").lower()
        if "iteminfo.pabgb" not in gf:
            continue
        for change in patch.get("changes", []):
            all_changes.append(change)

    report = ReparseDiffReport()

    # --- Path 1: splice everything, reparse full file ---
    modded_bytes, unapplied = _splice_all(vanilla_bytes, all_changes,
                                          entry_blob_start)
    report.unapplied_patches = unapplied

    try:
        vanilla_items = crimson_rs.parse_iteminfo_from_bytes(vanilla_bytes)
        modded_items = crimson_rs.parse_iteminfo_from_bytes(modded_bytes)
        report.edits = _diff_item_lists(vanilla_items, modded_items,
                                        source_patches=list(range(len(all_changes))))
        report.mode = "full_reparse"
        return report
    except Exception as e:
        log.info("reparse-diff: full reparse failed (%s); "
                 "falling back to per-patch isolation", e)

    # --- Path 2: per-patch isolation ---
    report.mode = "per_patch_isolation"
    try:
        vanilla_items = crimson_rs.parse_iteminfo_from_bytes(vanilla_bytes)
    except Exception as e:
        log.error("reparse-diff: cannot parse vanilla (%s)", e)
        return report

    for i, change in enumerate(all_changes):
        # Skip patches that already failed at the splice step.
        if any(idx == i for idx, _ in unapplied):
            continue
        single_bytes, single_unapplied = _splice_all(
            vanilla_bytes, [change], entry_blob_start, base_index=i)
        if single_unapplied:
            # Already recorded in the outer unapplied list — no action.
            continue
        try:
            single_items = crimson_rs.parse_iteminfo_from_bytes(single_bytes)
        except Exception:
            report.parse_break_patches.append(i)
            continue

        # Sanity guards against corrupted-but-parseable output.
        #
        # When a patch corrupts a CString length or CArray count in a
        # way that still parses (count reads as a valid u32 but happens
        # to be, say, 65536 or a handful of junk bytes), per-patch
        # isolation will produce a diff with giant APPEND or missing
        # entries. We can't distinguish "this patch deleted 335 items"
        # from "this patch broke my stream and the reader gave up
        # early". Veto the diff when it looks pathological: either the
        # parsed entry count dropped by more than a small tolerance, or
        # any single edit claims a huge array growth.
        if len(single_items) < len(vanilla_items) - 2:
            report.parse_break_patches.append(i)
            continue
        per_patch_edits = _diff_item_lists(vanilla_items, single_items,
                                           source_patches=[i])
        # Filter out edits that look like count-prefix corruption
        # (APPENDs or REMOVE_IDXs with absurd size). 256 is a generous
        # cap — the biggest legitimate CArray in vanilla iteminfo
        # (enchant_data_list etc.) stays well under 50.
        huge = any(
            (e.op == APPEND and isinstance(e.value, list) and len(e.value) > 256)
            or (e.op == REMOVE_IDX and isinstance(e.value, list) and len(e.value) > 256)
            for e in per_patch_edits
        )
        if huge:
            report.parse_break_patches.append(i)
            continue
        if not per_patch_edits:
            report.no_op_patches.append(i)
            continue
        report.edits.extend(per_patch_edits)

    # De-duplicate: when many patches each set the same field to the same
    # value, we'll emit one FieldEdit per patch. Coalesce so each unique
    # (entry, path, op, value) appears once with the union of source
    # patches.
    report.edits = _coalesce_edits(report.edits)
    return report


def _splice_all(vanilla_bytes: bytes, changes: list[dict],
                entry_blob_start: dict, base_index: int = 0
                ) -> tuple[bytes, list[tuple[int, str]]]:
    """Apply `changes` to a copy of `vanilla_bytes`, handling replace/
    insert/delete + absolute/relative offsets uniformly.

    Returns (modded_bytes, unapplied_list). `unapplied_list[i]` = (index,
    reason) for patches we had to skip. Index is into the flattened
    changes list of the CALLING context (supply base_index if slicing).

    Why apply in input order: each subsequent patch's offset is
    interpreted against the vanilla coordinate system, but when an
    earlier patch has shifted bytes (insert/delete or length-changing
    replace), the later patch's anchor has to shift with it. We track
    cumulative byte deltas through a simple `shift` counter maintained
    against sorted offsets.

    Practical note: JMM mods typically author patches in ascending
    offset order so this holds. Mods that interleave or target
    non-monotonic offsets get best-effort treatment — any patch whose
    resolved absolute offset lands outside the current buffer is
    reported as unapplied with reason "offset_oob_after_shift".
    """
    buf = bytearray(vanilla_bytes)
    unapplied: list[tuple[int, str]] = []
    # Track cumulative shift keyed by the ORIGINAL vanilla absolute
    # offset the patch was authored against. For a patch at vanilla
    # offset V, current buf offset = V + sum(deltas for prior patches
    # whose vanilla offset <= V).
    shifts: list[tuple[int, int]] = []  # (vanilla_offset, delta)

    def current_shift(vanilla_off: int) -> int:
        return sum(d for vo, d in shifts if vo <= vanilla_off)

    for i, change in enumerate(changes):
        ctype = change.get("type", "replace")
        # Resolve vanilla absolute offset.
        if "offset" in change:
            try:
                v_off = int(change["offset"], 16) if isinstance(
                    change["offset"], str) else int(change["offset"])
            except (TypeError, ValueError):
                unapplied.append((base_index + i, "malformed_offset"))
                continue
        elif "entry" in change and "rel_offset" in change:
            base = entry_blob_start.get(change["entry"])
            if base is None:
                unapplied.append((base_index + i, "entry_not_in_vanilla"))
                continue
            try:
                v_off = base + int(change["rel_offset"])
            except (TypeError, ValueError):
                unapplied.append((base_index + i, "malformed_rel_offset"))
                continue
        else:
            unapplied.append((base_index + i, "no_offset_spec"))
            continue

        # Map to current buf offset by adding accumulated shifts.
        cur_off = v_off + current_shift(v_off)

        if ctype == "replace":
            new_hex = change.get("patched") or ""
            old_hex = change.get("original") or ""
            try:
                new_bytes = bytes.fromhex(new_hex)
            except ValueError:
                unapplied.append((base_index + i, "malformed_patched_hex"))
                continue
            try:
                old_bytes = bytes.fromhex(old_hex) if old_hex else b""
            except ValueError:
                unapplied.append((base_index + i, "malformed_original_hex"))
                continue
            old_len = len(old_bytes) if old_bytes else len(new_bytes)
            if cur_off + old_len > len(buf):
                unapplied.append((base_index + i, "offset_oob_after_shift"))
                continue
            # We DO NOT validate `original` here. In reparse-diff mode we
            # deliberately trust the author's `patched` bytes even when
            # `original` doesn't match — that's the whole point (stale
            # patches still carry usable intent). The strict mode in
            # `_apply_legacy_json` preserves the old behaviour.
            buf[cur_off:cur_off + old_len] = new_bytes
            delta = len(new_bytes) - old_len
            if delta != 0:
                shifts.append((v_off, delta))
        elif ctype == "insert":
            ins_hex = change.get("bytes") or change.get("patched") or ""
            try:
                ins_bytes = bytes.fromhex(ins_hex)
            except ValueError:
                unapplied.append((base_index + i, "malformed_insert_hex"))
                continue
            if cur_off > len(buf):
                unapplied.append((base_index + i, "offset_oob_after_shift"))
                continue
            buf[cur_off:cur_off] = ins_bytes
            shifts.append((v_off, len(ins_bytes)))
        elif ctype == "delete":
            del_len = change.get("length")
            if not isinstance(del_len, int) or del_len < 0:
                unapplied.append((base_index + i, "delete_without_length"))
                continue
            if cur_off + del_len > len(buf):
                unapplied.append((base_index + i, "offset_oob_after_shift"))
                continue
            del buf[cur_off:cur_off + del_len]
            shifts.append((v_off, -del_len))
        else:
            unapplied.append((base_index + i, f"unsupported_type_{ctype}"))
            continue

    return bytes(buf), unapplied


def _diff_item_lists(vanilla_items: list[dict], modded_items: list[dict],
                     source_patches: list[int]) -> list[FieldEdit]:
    """Diff two parsed iteminfo lists. Returns ordered FieldEdits for
    every entry that changed, every entry added, every entry removed.

    `source_patches` is attached to each produced edit for provenance —
    in per-patch isolation we pass a single-element list; in full-file
    mode we pass the full patch-index list (we can't cheaply determine
    which specific patches contributed to which field diff in that
    path; coalesce_edits picks that up later).
    """
    vi = {it.get("string_key"): it for it in vanilla_items if it.get("string_key")}
    mi = {it.get("string_key"): it for it in modded_items if it.get("string_key")}
    out: list[FieldEdit] = []

    # Entries added in modded
    for key in mi:
        if key in vi:
            continue
        out.append(FieldEdit(op=ADD_ENTRY, entry=key, path="",
                             value=mi[key],
                             source_patches=list(source_patches)))

    # Entries removed in modded
    for key in vi:
        if key in mi:
            continue
        out.append(FieldEdit(op=REMOVE_ENTRY, entry=key, path="",
                             source_patches=list(source_patches)))

    # Entries present in both: recursive field-diff
    for key in vi:
        if key not in mi:
            continue
        for edit in _diff_dicts(vi[key], mi[key], "", key, source_patches):
            out.append(edit)

    return out


def _diff_dicts(a: dict, b: dict, path_prefix: str, entry_key: str,
                source_patches: list[int]):
    """Recursively yield FieldEdits for every leaf difference between
    dicts `a` (vanilla) and `b` (modded). Handles nested dicts + lists.
    CArray length changes become APPEND / REMOVE_IDX edits with the
    tail or head index range; per-element value changes become SET
    edits targeting the indexed path.
    """
    # Keys in b but not a, or with different values
    for k in b:
        sub_path = f"{path_prefix}.{k}" if path_prefix else k
        vb = b[k]
        va = a.get(k) if isinstance(a, dict) else None
        if isinstance(vb, dict) and isinstance(va, dict):
            yield from _diff_dicts(va, vb, sub_path, entry_key, source_patches)
        elif isinstance(vb, list) and isinstance(va, list):
            yield from _diff_lists(va, vb, sub_path, entry_key, source_patches)
        else:
            if va != vb:
                yield FieldEdit(op=SET, entry=entry_key, path=sub_path,
                                value=vb,
                                source_patches=list(source_patches))


def _diff_lists(a: list, b: list, path_prefix: str, entry_key: str,
                source_patches: list[int]):
    """Diff two parallel lists at `path_prefix`. Cheap heuristic: if
    length differs, the common prefix is compared per-index; the tail
    of b is emitted as APPEND; the tail of a as REMOVE_IDX. This is
    correct when mods only APPEND to arrays (the common case for
    CArray extensions like socket lists or enchant lists) and when
    existing elements stay in order. When mods splice into the middle
    we'll emit SET edits for the overlap + tail-append for the excess,
    which is semantically equivalent for idempotent element types.
    """
    common = min(len(a), len(b))
    for i in range(common):
        ai, bi = a[i], b[i]
        sub_path = f"{path_prefix}[{i}]"
        if isinstance(ai, dict) and isinstance(bi, dict):
            yield from _diff_dicts(ai, bi, sub_path, entry_key, source_patches)
        elif isinstance(ai, list) and isinstance(bi, list):
            yield from _diff_lists(ai, bi, sub_path, entry_key, source_patches)
        elif ai != bi:
            yield FieldEdit(op=SET, entry=entry_key, path=sub_path,
                            value=bi,
                            source_patches=list(source_patches))
    if len(b) > len(a):
        yield FieldEdit(op=APPEND, entry=entry_key, path=path_prefix,
                        value=list(b[len(a):]),
                        source_patches=list(source_patches),
                        note=f"array grew {len(a)} -> {len(b)}")
    elif len(a) > len(b):
        yield FieldEdit(op=REMOVE_IDX, entry=entry_key, path=path_prefix,
                        value=list(range(len(b), len(a))),
                        source_patches=list(source_patches),
                        note=f"array shrank {len(a)} -> {len(b)}")


def _coalesce_edits(edits: list[FieldEdit]) -> list[FieldEdit]:
    """Merge duplicate (entry, path, op, value) edits produced by the
    per-patch pass into one, unioning their source_patches. Keeps
    ordering by first-appearance for deterministic output."""
    import hashlib, json as _json
    seen: dict[str, FieldEdit] = {}
    order: list[str] = []
    for e in edits:
        try:
            val_hash = hashlib.sha1(
                _json.dumps(e.value, sort_keys=True, default=str).encode()
            ).hexdigest()
        except TypeError:
            val_hash = str(id(e.value))
        key = f"{e.entry}|{e.path}|{e.op}|{val_hash}"
        if key in seen:
            for sp in e.source_patches:
                if sp not in seen[key].source_patches:
                    seen[key].source_patches.append(sp)
        else:
            seen[key] = FieldEdit(op=e.op, entry=e.entry, path=e.path,
                                  value=e.value,
                                  source_patches=list(e.source_patches),
                                  note=e.note)
            order.append(key)
    return [seen[k] for k in order]


def apply_field_edits(items: list[dict], edits: list[FieldEdit]
                      ) -> tuple[int, int, list[str]]:
    """Apply a list of FieldEdits to the parsed dict list.

    Mutates `items` in place. Returns (applied, skipped, skip_reasons).
    Supports all four ops produced by `reparse_diff_patches`:
      SET          → write primitive (or nested) value at path
      APPEND       → extend CArray at path by new elements
      REMOVE_IDX   → drop specified indices from CArray at path
      ADD_ENTRY    → append new entry dict to items
      REMOVE_ENTRY → drop entry by string_key

    Order matters: ADD_ENTRY / REMOVE_ENTRY are applied first so path-
    targeting edits see the right universe of entries.
    """
    idx = {it.get("string_key"): it for it in items
           if it.get("string_key")}

    # Entry-level ops first
    for e in edits:
        if e.op == REMOVE_ENTRY:
            tgt = idx.pop(e.entry, None)
            if tgt is None:
                continue
            try:
                items.remove(tgt)
            except ValueError:
                pass
        elif e.op == ADD_ENTRY and isinstance(e.value, dict):
            if e.entry in idx:
                continue  # already present — treat as idempotent
            items.append(e.value)
            idx[e.entry] = e.value

    applied = 0
    skipped_reasons: list[str] = []

    for e in edits:
        if e.op in (ADD_ENTRY, REMOVE_ENTRY):
            applied += 1
            continue

        item = idx.get(e.entry)
        if item is None:
            skipped_reasons.append(
                f"{e.op} on {e.entry}.{e.path}: entry missing in merged dict")
            continue

        if e.op == SET:
            if _set_dict_path(item, e.path, e.value):
                applied += 1
            else:
                skipped_reasons.append(
                    f"SET on {e.entry}.{e.path}: path not writable "
                    "(count/length prefix or broken nesting)")
        elif e.op == APPEND:
            target = _resolve_path_container(item, e.path)
            if not isinstance(target, list):
                skipped_reasons.append(
                    f"APPEND on {e.entry}.{e.path}: target not a list")
                continue
            if isinstance(e.value, list):
                target.extend(e.value)
                applied += 1
            else:
                skipped_reasons.append(
                    f"APPEND on {e.entry}.{e.path}: value not a list")
        elif e.op == REMOVE_IDX:
            target = _resolve_path_container(item, e.path)
            if not isinstance(target, list):
                skipped_reasons.append(
                    f"REMOVE_IDX on {e.entry}.{e.path}: target not a list")
                continue
            if not isinstance(e.value, list):
                skipped_reasons.append(
                    f"REMOVE_IDX on {e.entry}.{e.path}: indices not a list")
                continue
            # Remove from highest index first so lower indices remain valid.
            for drop_idx in sorted(e.value, reverse=True):
                if 0 <= drop_idx < len(target):
                    del target[drop_idx]
            applied += 1
        else:
            skipped_reasons.append(f"unknown op {e.op}")

    return applied, len(skipped_reasons), skipped_reasons


def _resolve_path_container(obj: dict, path: str):
    """Walk `path` and return the list/dict at that location (for
    APPEND/REMOVE_IDX). Returns None if the path doesn't exist."""
    if not path:
        return obj
    parts = _split_path(path)
    # The `path` here is the array's own path; the final segment can be
    # either (name, None) meaning dict[name] or (name, i) meaning
    # dict[name][i]. For APPEND/REMOVE on the array as a whole, last
    # segment is (name, None) pointing to the list itself.
    cur = obj
    for key, ai in parts:
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
        if ai is not None:
            if not isinstance(cur, list) or ai >= len(cur):
                return None
            cur = cur[ai]
    return cur


def format_field_edits_summary(report: "ReparseDiffReport",
                                max_rows: int = 40) -> list[str]:
    """Render a ReparseDiffReport for the Details pane."""
    lines = []
    by_op: dict[str, int] = {}
    for e in report.edits:
        by_op[e.op] = by_op.get(e.op, 0) + 1
    header_parts = [f"{n} {op}" for op, n in sorted(by_op.items())]
    lines.append(f"  mode={report.mode}  "
                 f"·  {len(report.edits)} edit(s): {', '.join(header_parts) or '(none)'}")
    if report.parse_break_patches:
        lines.append(f"  {len(report.parse_break_patches)} patch(es) broke parsing "
                     f"in isolation — see indices: "
                     f"{report.parse_break_patches[:10]}"
                     + (" …" if len(report.parse_break_patches) > 10 else ""))
    if report.unapplied_patches:
        lines.append(f"  {len(report.unapplied_patches)} patch(es) could not be "
                     "applied at all (hex parse, OOB offset, missing entry):")
        for idx, reason in report.unapplied_patches[:6]:
            lines.append(f"    #{idx}: {reason}")
        if len(report.unapplied_patches) > 6:
            lines.append(f"    … {len(report.unapplied_patches) - 6} more")
    if report.no_op_patches:
        lines.append(f"  {len(report.no_op_patches)} patch(es) were idempotent "
                     "(wrote value identical to vanilla) — safe to ignore")
    lines.append("")
    lines.append("  Per-edit list (from diff):")
    for e in report.edits[:max_rows]:
        src = (f"[patches #{e.source_patches[0]}"
               f"+{len(e.source_patches)-1}]"
               if len(e.source_patches) > 1
               else (f"[patch #{e.source_patches[0]}]"
                     if e.source_patches else ""))
        if e.op == SET:
            preview = repr(e.value)
            if len(preview) > 60:
                preview = preview[:57] + "..."
            lines.append(f"    * SET    {e.entry}.{e.path} = {preview} {src}")
        elif e.op == APPEND:
            n = len(e.value) if isinstance(e.value, list) else "?"
            lines.append(f"    + APPEND {e.entry}.{e.path} (+{n} elem) {src}")
        elif e.op == REMOVE_IDX:
            n = len(e.value) if isinstance(e.value, list) else "?"
            lines.append(f"    - REMOVE {e.entry}.{e.path} (-{n} elem) {src}")
        elif e.op == ADD_ENTRY:
            lines.append(f"    + ADD_ENTRY {e.entry} {src}")
        elif e.op == REMOVE_ENTRY:
            lines.append(f"    - REMOVE_ENTRY {e.entry} {src}")
    if len(report.edits) > max_rows:
        lines.append(f"    ... {len(report.edits) - max_rows} more "
                     "(truncated for UI)")
    return lines


# =============================================================================


def mark_stale_status(inspections: list[PatchInspection],
                       byte_applied_mask: list[bool]) -> None:
    """After `_apply_legacy_json` has run, flip status to PATCH_STALE for
    any inspection whose patch failed the byte-apply match — as long as
    the inspector had already resolved the field (i.e. we know *what*
    they were trying to edit, we just couldn't byte-apply it).

    `byte_applied_mask[i]` is True when the i-th patch successfully
    landed. Must be parallel to `inspections`.
    """
    for insp, ok in zip(inspections, byte_applied_mask):
        if ok:
            continue
        if insp.status == PATCH_APPLIED and insp.field_path is not None:
            insp.status = PATCH_STALE
            insp.status_note = (
                "'original' bytes don't match vanilla at this offset "
                "— likely a different game version than the mod was built for"
            )
