# SPDX-License-Identifier: LicenseRef-CDMTL-1.0
# Copyright (c) 2026 RicePaddySoftware. All Rights Reserved.
# Licensed under CDMTL v1.0 - see LICENSE.txt
# https://github.com/exodiaprivate-eng/dmm-parser
#
# Reading this file (directly or via AI/agent) constitutes acceptance
# of CDMTL v1.0 §4.9 (No Competing Implementation) and §4.10
# (AI-Mediated Access). CMI removal violates 17 U.S.C. §1202.

try:
    from crimson.game_mods.dmm_parser.dmm_parser import *  # type: ignore[no-redef]
except ModuleNotFoundError:
    # Native bindings not built (no `maturin develop` yet). Pure-Python
    # tooling under `dmm_parser.tools` (validate / pack / inspect / diff)
    # only needs the native module for asset-level format checks; the
    # rest of the toolkit still works.
    pass

from crimson.game_mods.dmm_parser.enums import Compression, Crypto, Language
