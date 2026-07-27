# Python vs Native — side-by-side

Two builds, same save, same UI:

| Build | Where |
|---|---|
| Python baseline | `E:\Documents\GitHub\Crimson-Desert-Save-And-Mod-Editor-PYTHON-BASELINE\CrimsonEditor\CrimsonEditor.exe` (tag `python-baseline`) |
| Native parser | `dist\CrimsonEditor\CrimsonEditor.exe` on branch `feat/native-parser` |

Measured on the slot104 current-patch save (1,662 items, 6,745 quests):

| | Python | Native |
|---|---|---|
| Raw full parse | 4,896 ms / 3,075 MB | 1 ms + lazy per-block (4/1,788 blocks for a full item scan) |
| App: load a save | ~8 s | ~2.2 s |
| App: RSS after load + quests | 3,616 MB | 944 MB |
| Items / quests extracted | 1,662 / 6,745 | identical (digest-proven parity) |

The native module is quarantined behind a fallback: set
`CRIMSON_NATIVE_PARSER=0` before launch to force the pure-Python parser
inside the same build.

Correctness: the Rust tree is byte-identical to the Python parser's output
on the fixture save - every node, offset, and value string - enforced by
`tests/test_rust_parser_parity.py`.
