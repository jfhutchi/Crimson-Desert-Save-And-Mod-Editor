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

## Build

    python build.py

Creates `.venv`, installs pinned dependencies, and builds the executable.
Add `--test` to run the suite first. Equivalent manual steps:

    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    .\.venv\Scripts\python.exe -m PyInstaller CrimsonEditor.spec --noconfirm --clean

The result is `dist/CrimsonEditor/` — ship the folder, or a zip of it. It is a
onedir build on purpose: a onefile bundle re-extracts ~80 MB to a temp
directory on every launch, which was most of the startup wait. Warm start to
the main window is about 3 seconds, with a splash reporting progress
throughout.

## Tests

    .\.venv\Scripts\python.exe -m pytest tests -q

The suite drives the real window: it asserts both editors keep their own core
modules, that a current-patch save loads with all 1,662 items across every bag
tab, and that no section switch blocks the GUI thread for more than 400 ms.
Tests needing a save fixture skip themselves when it is not present.
