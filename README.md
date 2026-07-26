# Crimson Desert — Save & Mod Editor

One application for both halves of the toolset: editing a character save and
patching installed game data. Previously these were two separate executables
with two windows, two icon caches, and two copies of the save parser.

    python main.py

## Layout

    crimson/
      app.py          unified window: hosts every page from both editors
      save_editor/    save file editing (items, quests, knowledge, mounts)
      game_mods/      game data patching (PAZ overlays, item stats, drops)
      common/         shared shell, theme, icon cache, progress helpers
    tools/
      migrate_from_python_repo.py   regenerates the packages from the source repo

Both editors ship modules with the same names (`models`, `save_crypto`,
`item_scanner`, ...) whose contents differ, so each lives in its own
subpackage and imports are package-relative. That is what allows a single
process — and therefore a single window — to host both.

## Safety

Save writes go through the transactional path: a verified backup is taken, the
new file is written to a temporary path, decrypted and validated, and only then
swapped in. Unknown save schemas load read-only rather than risking a bad
write.

## Credits

Built on the Crimson Desert Save Editor and Game Mods tools by NattKh and
contributors. See `crimson/game_mods/CREDITS.md`.
