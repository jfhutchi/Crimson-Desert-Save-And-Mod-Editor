#!/usr/bin/env python3
"""Run the suite as two consecutive processes so memory fully resets.

Loaded-save fixtures peak at a few GB each; one process running every module
compounds whatever teardown misses. Two halves keep the peak at half-a-suite
and the OS reclaims everything in between.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TESTS = sorted(p.name for p in Path("tests").glob("test_*.py"))
half = (len(TESTS) + 1) // 2
GROUPS = [TESTS[:half], TESTS[half:]]

exit_code = 0
for index, group in enumerate(GROUPS, 1):
    print(f"--- half {index}: {len(group)} files ---", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *(f"tests/{name}" for name in group)],
    )
    exit_code = exit_code or result.returncode
sys.exit(exit_code)
