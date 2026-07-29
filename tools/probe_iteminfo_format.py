"""Probe the iteminfo record format against a real install.

See docs/ITEMINFO_PARSER_HANDOFF.md. Set CRIMSON_GAME_PATH to override the
game directory.
"""
"""Find SubItem type 17's payload size using the reference parser verbatim.

_test_parse.parse_full_item is reused as-is; only the SubItem tag branch is
made configurable, by patching the module's ValueError path at runtime.
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "crimson", "game_mods")]

from crimson.game_mods import crimson_rs

GAME = os.environ.get("CRIMSON_GAME_PATH",
                     r"D:\SteamLibrary\steamapps\common\Crimson Desert")
d = bytes(crimson_rs.extract_file(
    GAME, '0008', 'gamedata/binary__/client/bin', 'iteminfo.pabgb'))
print(f"iteminfo body: {len(d):,} bytes")

src = open(os.path.join(ROOT, "crimson", "game_mods", "_test_parse.py"),
           encoding="utf-8").read()
# Keep only the helpers and the record parser; drop its file-loading preamble.
body = src[src.index("def r8("):src.index("print(")]

results = {}
for extra in (0, 2, 4, 6, 8, 12, 16):
    patched = body.replace(
        "elif ti != 14: raise ValueError(f\"bad SubItem type {ti}\")",
        f"elif ti == 17: off += {extra}\n"
        f"    elif ti != 14: raise ValueError(f'bad SubItem type {{ti}}')",
    )
    ns = {"struct": struct}
    exec(patched, ns)
    parse = ns["parse_full_item"]

    for has_new in (True, False):
        off, count = 0, 0
        err = None
        try:
            while off < len(d):
                off, key, skey, sz = parse(d, off, has_new_field=has_new)
                count += 1
        except Exception as exc:
            err = str(exc)[:44]
        pct = 100 * off / len(d)
        results[(extra, has_new)] = (count, pct, err)
        if err is None or pct > 95:
            print(f"  t17={extra:>2}B new_field={has_new}: {count:>6,} items, "
                  f"{pct:5.1f}%  {'COMPLETE' if err is None else err}")

best = max(results.items(), key=lambda kv: kv[1][1])
(extra, has_new), (count, pct, err) = best
print(f"\nbest: type17 payload {extra}B, has_new_field={has_new} -> "
      f"{count:,} items, {pct:.1f}% of file"
      + ("" if err is None else f", stopped: {err}"))
