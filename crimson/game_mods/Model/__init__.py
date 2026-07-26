"""Vendored PAC/PAM/PAB parsers + exporters for Crimson Desert meshes.

Provenance:
  - pac_parser.py, pac_decode.py, pam_parser.py, parser_factory.py,
    model_types.py, exporters/* :: from github.com/Altair200333/crimson-desert-model-browser (MIT)
  - pab_skeleton_parser.py :: from github.com/hzeemr/crimsonforge (MIT, renamed from skeleton_parser.py)

The files are copied verbatim except pab_skeleton_parser.py which was patched
to drop its project-specific logger dependency. See LICENSES.md / LICENSE_crimsonforge
for full license text.

To import, put the Model/ directory on sys.path first:

    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Model'))
    from pac_parser import PacParser
    from pab_skeleton_parser import parse_pab
    from exporters.fbx_exporter import FbxExporter

All parsers use flat imports (e.g. `from model_types import ...`) so Model/
itself must be on sys.path — don't import like `from Model.pac_parser import ...`.
"""
import os as _os
import sys as _sys

_MODEL_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _MODEL_DIR not in _sys.path:
    _sys.path.insert(0, _MODEL_DIR)
