# iteminfo parser vs game 1.15 — handoff

**Goal**: parse `iteminfo` from a current-patch install so the build planner can
show item stats (phase 2), and so ItemBuffs editing works on 1.15.

**Status**: blocked. The bundled parser cannot read a single current-patch
record. This is a bigger, more valuable target than the stat view itself —
see "Why this matters" below.

## What is known

Extraction works. From a real install:

```python
from crimson.game_mods import crimson_rs
GAME = r"D:\SteamLibrary\steamapps\common\Crimson Desert"
body   = bytes(crimson_rs.extract_file(GAME, '0008', 'gamedata/binary__/client/bin', 'iteminfo.pabgb'))
header = bytes(crimson_rs.extract_file(GAME, '0008', 'gamedata/binary__/client/bin', 'iteminfo.pabgh'))
# body 5,938,891 bytes | header 52,066 bytes  (2026-07-27, game 1.15)
```

The repo's captured copy in `vanilla_tables/iteminfo.pabgb` is **5,486,564**
bytes — an older format. Diffing old against new is the main lead.

**Body layout** (confirmed by hexdump at offset 0):

```
00: 98 08 00 00      u32  key           = 2200
04: 0f 00 00 00      u32  string length = 15
08: "Pyeonjeon_Arrow"     skey
...
```

So records start at offset 0 and begin `u32 key` + length-prefixed string,
exactly as `parse_full_item` expects. The mismatch is later in the record.

**Header layout**: `u16` count at 0 = 6508, and `(len-2)/6508 == 8.00`, so it
is 6508 fixed 8-byte entries. With `rs=8`, the u32 at `2 + i*8 + 4` yields
`[0, 654, 1302, ...]` — ascending and starting at 0, so these look like record
offsets into the body. That path still produced zero parsed items, so either
the field position is wrong or the record parse fails for the same reason as
the sequential walk.

## The failure

`_iteminfo_parse` (both `dmm_parser` and `crimson_rs`) reports:

```
parse error at offset 0x0000014F: unknown SubItem type: 17
```

`crimson/game_mods/_test_parse.py` is a readable pure-Python reference
implementation of the same record. Its SubItem handling is:

```python
ti, off = r8(d, off)
if ti in (0, 3, 9): _, off = r32(d, off)   # tag + u32 payload
elif ti != 14: raise ValueError(...)       # 14 = tag only, no payload
```

So tag 17 is new and its payload size is unknown.

**But 17 is not the whole story.** Reusing `parse_full_item` verbatim and
trying tag-17 payloads of 0/2/4/6/8/12/16 bytes, with `has_new_field` both
True and False, every combination fails **inside the first record** with a
wildly out-of-range length — i.e. misalignment happens *before* the SubItem
tag. At least one other field was added or resized in 1.15.

Note `parse_full_item` already carries `has_new_field` (`off += 6  # NEW: u32
+ u16`) from a previous version bump, so format drift here is routine.

## Reproduce

`scratchpad/probe17b.py` from the session did this; the essentials:

1. Extract body+header as above.
2. `exec` the helper functions and `parse_full_item` out of `_test_parse.py`
   (skip its file-loading preamble, which points at the author's paths).
3. Patch the SubItem branch to accept 17 with a parameterised payload size.
4. Walk records from offset 0, counting successes and reporting where it stops.

## Suggested approach

Byte-diff old versus new for the **same item key**, rather than guessing sizes:

1. Parse `vanilla_tables/iteminfo.pabgb` with `has_new_field=False` — it should
   still work, since it predates the change. Record each item's key, start
   offset and size.
2. Take an item whose old record parses, find the same key in the new file
   (search for `u32 key` followed by its string length + name).
3. Compare the two byte ranges field by field against `parse_full_item`'s
   sequence until they diverge. The divergence point names the new field.
4. Repeat for an item that carries SubItem tag 17 to pin that payload.

## Why this matters

Beyond the build planner: open upstream issues **#89** ("CGM v2.0.6 — ItemBuff
Problem") and **#91** ("ItemBuff & BagSpace Problem") are consistent with the
item-info parser failing on current-patch data. Unproven, but if the parser is
repaired those may resolve, which would make this a high-value contribution
rather than a feature enabler.

## Related state

- Build planner phase 1 (ownership column in the item database) is **done and
  shipped** — `_owned_counts()` in `crimson/save_editor/gui.py`.
- Phase 3 (absolute stat totals) additionally needs the game's aggregation
  formula, which is not in the save; only `_level` and `_currentHp` are stored.
