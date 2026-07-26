#!/usr/bin/env python3
"""Build the editor from source.

    python build.py            # create .venv, install deps, build dist/CrimsonEditor/
    python build.py --test     # run the test suite first (needs a save fixture)

Requires Python 3.12+ on Windows. The result is a self-contained folder;
users of the built app do not need Python installed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts" if sys.platform == "win32" else "bin") / "python.exe"
if sys.platform != "win32":
    PYTHON = PYTHON.with_suffix("")


def run(*cmd: str) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="run the test suite before building")
    args = parser.parse_args()

    if sys.version_info < (3, 12):
        sys.exit(f"Python 3.12+ required, this is {sys.version.split()[0]}")

    if not PYTHON.is_file():
        print(f"creating virtual environment at {VENV}")
        venv.create(VENV, with_pip=True)
    run(PYTHON, "-m", "pip", "install", "-q", "-r", "requirements-dev.txt")

    if args.test:
        run(PYTHON, "-m", "pytest", "tests", "-q")

    run(PYTHON, "-m", "PyInstaller", "CrimsonEditor.spec", "--noconfirm", "--clean")

    exe = ROOT / "dist" / "CrimsonEditor" / "CrimsonEditor.exe"
    if not exe.is_file():
        sys.exit("build finished but the executable is missing")
    print(f"\nbuilt: {exe}\nship the dist/CrimsonEditor folder (or a zip of it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
