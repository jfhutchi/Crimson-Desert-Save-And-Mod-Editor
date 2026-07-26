"""Full-coverage dump of iteminfo.pabgb to JSONL + index files.

Uses Potter's crimson_rs parser, which is verified byte-perfect roundtrip on the
full 4.8 MB / 6024-item file. Output goes to data/iteminfo_dump/.

Run from CrimsonGameMods/ root:
    python tools/dump_iteminfo.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from crimson.game_mods import crimson_rs as crimson_rs


GAME_DIR_CANDIDATES = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert",
    r"F:\SteamLibrary\steamapps\common\Crimson Desert",
    r"D:\SteamLibrary\steamapps\common\Crimson Desert",
    r"E:\SteamLibrary\steamapps\common\Crimson Desert",
]


def find_game_dir() -> str:
    for c in GAME_DIR_CANDIDATES:
        if os.path.isdir(c):
            return c
    raise SystemExit(
        "Game dir not found. Edit GAME_DIR_CANDIDATES in this script."
    )


def python_type_name(v) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        if not v:
            return "list[]"
        return f"list[{python_type_name(v[0])}]"
    if isinstance(v, dict):
        return "dict"
    return type(v).__name__


def main() -> None:
    here = Path(__file__).resolve().parent.parent
    out_dir = here / "data" / "iteminfo_dump"
    out_dir.mkdir(parents=True, exist_ok=True)

    game_dir = find_game_dir()
    print(f"Game dir:   {game_dir}")
    print(f"Output dir: {out_dir}")

    t0 = time.perf_counter()
    raw = crimson_rs.extract_file(
        game_dir, "0008", "gamedata/binary__/client/bin", "iteminfo.pabgb"
    )
    items = crimson_rs.parse_iteminfo_from_bytes(raw)
    t1 = time.perf_counter()
    print(f"Parsed {len(items):,} items from {len(raw):,} bytes in "
          f"{(t1-t0)*1000:.0f} ms")

    # 1) Full JSONL — one item per line, every field, no truncation.
    jsonl_path = out_dir / "items.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")
    print(f"  items.jsonl              {jsonl_path.stat().st_size:>12,} bytes")

    # 2) Pretty single-item samples for human reading.
    sample_keys = [
        1000080,    # Hwando_TwoHandSword
        1000082,    # Troopers_TwoHandSpear
        1003265,    # GreyWolf_OneHandBow
        391518535,  # Toad_AccessoryRing (Oath of Darkness — godmode template)
        1003289,    # Hernandian_Stirrup
        1000382,    # Plate Boots of the Shadows
    ]
    samples_dir = out_dir / "samples"
    samples_dir.mkdir(exist_ok=True)
    by_key = {it["key"]: it for it in items}
    for k in sample_keys:
        it = by_key.get(k)
        if not it:
            continue
        sample_path = samples_dir / f"{k}_{it.get('string_key','')}.json"
        with sample_path.open("w", encoding="utf-8") as fh:
            json.dump(it, fh, indent=2, ensure_ascii=False)
    print(f"  samples/                 {len(sample_keys)} pretty samples")

    # 3) Field-level summary: type, presence, nullability, distinct count, examples.
    field_stats: dict[str, dict] = {}
    for it in items:
        for k, v in it.items():
            entry = field_stats.setdefault(k, {
                "types": Counter(),
                "present": 0,
                "non_null": 0,
                "non_zero": 0,
                "non_empty": 0,
                "distinct_scalar_values": set(),
                "max_list_len": 0,
                "examples": [],
            })
            entry["present"] += 1
            entry["types"][python_type_name(v)] += 1
            if v is not None:
                entry["non_null"] += 1
            if isinstance(v, (int, float)) and v != 0:
                entry["non_zero"] += 1
            if isinstance(v, str) and v:
                entry["non_empty"] += 1
            if isinstance(v, list):
                if v:
                    entry["non_empty"] += 1
                if len(v) > entry["max_list_len"]:
                    entry["max_list_len"] = len(v)
            if isinstance(v, dict):
                if v:
                    entry["non_empty"] += 1
            if isinstance(v, (int, float, str, bool)) and v is not None:
                if len(entry["distinct_scalar_values"]) < 50:
                    entry["distinct_scalar_values"].add(v)
            if len(entry["examples"]) < 3 and v not in (None, 0, "", [], {}):
                entry["examples"].append(v)

    # Convert to JSON-serialisable form.
    summary_out: dict[str, dict] = {}
    for k, e in field_stats.items():
        summary_out[k] = {
            "types": dict(e["types"]),
            "present": e["present"],
            "non_null": e["non_null"],
            "non_zero": e["non_zero"],
            "non_empty": e["non_empty"],
            "max_list_len": e["max_list_len"],
            "distinct_scalar_values_capped_at_50": len(e["distinct_scalar_values"]),
            "examples": e["examples"],
        }
    summary_path = out_dir / "field_summary.json"
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary_out, fh, indent=2, ensure_ascii=False, default=str)
    print(f"  field_summary.json       {summary_path.stat().st_size:>12,} bytes "
          f"({len(summary_out)} top-level fields)")

    # 4) Lookup indices.
    by_string_key: dict[str, int] = {}
    by_category: dict[int, list[int]] = defaultdict(list)
    by_item_type: dict[int, list[int]] = defaultdict(list)
    by_equip_type: dict[int, list[int]] = defaultdict(list)
    by_inventory: dict[int, list[int]] = defaultdict(list)
    by_tier: dict[int, list[int]] = defaultdict(list)
    has_field: dict[str, list[int]] = {
        "enchant_data_list": [],
        "equip_passive_skill_list": [],
        "docking_child_data": [],
        "gimmick_info_set": [],
        "is_dyeable": [],
        "drop_default_data_socket_list": [],
        "is_shield_item": [],
        "is_destroy_when_broken": [],
    }

    for it in items:
        k = it["key"]
        sk = it.get("string_key") or ""
        if sk:
            by_string_key[sk] = k
        by_category[it.get("category_info", 0)].append(k)
        by_item_type[it.get("item_type", 0)].append(k)
        if it.get("equip_type_info"):
            by_equip_type[it["equip_type_info"]].append(k)
        if it.get("inventory_info"):
            by_inventory[it["inventory_info"]].append(k)
        if it.get("item_tier"):
            by_tier[it["item_tier"]].append(k)
        if it.get("enchant_data_list"):
            has_field["enchant_data_list"].append(k)
        if it.get("equip_passive_skill_list"):
            has_field["equip_passive_skill_list"].append(k)
        if it.get("docking_child_data"):
            has_field["docking_child_data"].append(k)
        if it.get("gimmick_info"):
            has_field["gimmick_info_set"].append(k)
        if it.get("is_dyeable"):
            has_field["is_dyeable"].append(k)
        ddd = it.get("drop_default_data") or {}
        if ddd.get("add_socket_material_item_list"):
            has_field["drop_default_data_socket_list"].append(k)
        if it.get("is_shield_item"):
            has_field["is_shield_item"].append(k)
        if it.get("is_destroy_when_broken"):
            has_field["is_destroy_when_broken"].append(k)

    indices = {
        "by_string_key.json":   by_string_key,
        "by_category.json":     {str(k): v for k, v in sorted(by_category.items())},
        "by_item_type.json":    {str(k): v for k, v in sorted(by_item_type.items())},
        "by_equip_type.json":   {str(k): v for k, v in sorted(by_equip_type.items())},
        "by_inventory.json":    {str(k): v for k, v in sorted(by_inventory.items())},
        "by_tier.json":         {str(k): v for k, v in sorted(by_tier.items())},
        "has_field.json":       has_field,
    }
    for name, data in indices.items():
        p = out_dir / name
        with p.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)
        print(f"  {name:<25} {p.stat().st_size:>12,} bytes")

    # 5) Distinct value catalogs for the small enums.
    catalog_path = out_dir / "enum_catalogs.json"
    catalogs = {
        "item_type":         dict(Counter(it.get("item_type", 0) for it in items).most_common()),
        "category_info":     dict(Counter(it.get("category_info", 0) for it in items).most_common()),
        "item_tier":         dict(Counter(it.get("item_tier", 0) for it in items).most_common()),
        "knowledge_obtain_type": dict(Counter(it.get("knowledge_obtain_type", 0) for it in items).most_common()),
        "item_charge_type":  dict(Counter(it.get("item_charge_type", 0) for it in items).most_common()),
        "filter_type":       dict(Counter(it.get("filter_type", "") for it in items).most_common()),
        "money_type_define": dict(Counter(repr(it.get("money_type_define")) for it in items).most_common(20)),
    }
    catalogs = {k: {str(kk): vv for kk, vv in v.items()} for k, v in catalogs.items()}
    with catalog_path.open("w", encoding="utf-8") as fh:
        json.dump(catalogs, fh, indent=2, ensure_ascii=False)
    print(f"  enum_catalogs.json       {catalog_path.stat().st_size:>12,} bytes")

    # 6) Markdown README.
    readme_path = out_dir / "README.md"
    sizes_summary = []
    for p in sorted(out_dir.glob("*.json*")):
        sizes_summary.append(f"| `{p.name}` | {p.stat().st_size:,} |")

    readme_path.write_text(f"""# iteminfo.pabgb — Complete Index Dump

**Source:** vanilla `iteminfo.pabgb` from `0008/0.paz`
({len(raw):,} bytes / {len(items):,} items / {len(summary_out)} top-level fields per item)

**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}

**Generator:** `tools/dump_iteminfo.py` — uses Potter's `crimson_rs.parse_iteminfo_from_bytes`
(byte-perfect lossless roundtrip verified).

## Files

| File | Bytes |
|---|---:|
{chr(10).join(sizes_summary)}

## How to query

```python
import json

# Full dump — stream line-by-line (one item per line)
with open("items.jsonl", encoding="utf-8") as fh:
    for line in fh:
        item = json.loads(line)
        if "Bow" in item["string_key"]:
            print(item["key"], item["string_key"])

# Lookup by string_key → integer key
sk_index = json.load(open("by_string_key.json", encoding="utf-8"))
hwando_key = sk_index["Hwando_TwoHandSword"]

# All items in a category
cat = json.load(open("by_category.json", encoding="utf-8"))
all_swords = cat["202"]   # category_info=202 = TwoHandSword

# Which items have a field set
hf = json.load(open("has_field.json", encoding="utf-8"))
items_with_passives = hf["equip_passive_skill_list"]
items_with_dye = hf["is_dyeable"]
```

## Field reference

For per-field types (u8/u32/i64/etc) and the hash table each key references,
read Potter's type stub:

```
C:\\Users\\Coding\\AppData\\Roaming\\Python\\Python314\\site-packages\\crimson_rs\\__init__.pyi
```

39 TypedDicts cover every nested struct with field-level docstrings.

## Pretty samples

`samples/` has six items dumped in indented JSON for human reading:
- `1000080_Hwando_TwoHandSword.json`
- `1000082_Troopers_TwoHandSpear.json`
- `1003265_GreyWolf_OneHandBow.json`
- `391518535_Toad_AccessoryRing.json` (Oath of Darkness — godmode template)
- `1003289_Hernandian_Stirrup.json`
- `1000382_Plate Boots of the Shadows`

## Regenerating

When the game updates iteminfo.pabgb, re-run:

```bash
cd CrimsonGameMods
python tools/dump_iteminfo.py
```

Output overwrites in place.
""", encoding="utf-8")
    print(f"  README.md                {readme_path.stat().st_size:>12,} bytes")

    print()
    print(f"Done in {(time.perf_counter()-t0):.1f}s. Output: {out_dir}")


if __name__ == "__main__":
    main()
