"""
Core functions for CLI — no GUI imports, no PySide6.
Extracted from gui/tabs/stacker.py for standalone use.
"""
import struct
import re


def apply_one_legacy_patch(patched, vanilla, change):
    """Apply a single legacy JSON v2 patch to a bytearray. Returns 1 if applied."""
    ptype = change.get('type', 'replace')
    if ptype == 'replace':
        entry = change.get('entry')
        if entry and 'rel_offset' in change:
            name_bytes = entry.encode('ascii')
            search = struct.pack('<I', len(name_bytes)) + name_bytes + b'\x00'
            pos = vanilla.find(search)
            if pos < 0:
                return 0
            entry_start = pos - 4
            abs_off = entry_start + change['rel_offset']
        elif 'offset' in change:
            off_val = change['offset']
            abs_off = int(off_val, 16) if isinstance(off_val, str) else off_val
        else:
            return 0
        patch_bytes = bytes.fromhex(change.get('patched', ''))
        orig_bytes = bytes.fromhex(change.get('original', ''))
        if not patch_bytes:
            return 0
        end = abs_off + max(len(orig_bytes), len(patch_bytes))
        if end > len(patched):
            return 0
        patched[abs_off:abs_off + len(orig_bytes)] = patch_bytes
        return 1
    elif ptype == 'insert':
        entry = change.get('entry')
        if entry and 'rel_offset' in change:
            name_bytes = entry.encode('ascii')
            search = struct.pack('<I', len(name_bytes)) + name_bytes + b'\x00'
            pos = vanilla.find(search)
            if pos < 0:
                return 0
            abs_off = (pos - 4) + change['rel_offset']
        elif 'offset' in change:
            off_val = change['offset']
            abs_off = int(off_val, 16) if isinstance(off_val, str) else off_val
        else:
            return 0
        insert_bytes = bytes.fromhex(change.get('bytes', ''))
        if not insert_bytes:
            return 0
        patched[abs_off:abs_off] = insert_bytes
        return 1
    return 0


def deep_diff_to_intents(entry, key, a, b, prefix=''):
    """Recursively diff two parsed item dicts and emit Format 3 intents."""
    intents = []
    all_keys = set(list(a.keys()) + list(b.keys()))
    for k in sorted(all_keys):
        if k in ('key', 'string_key'):
            continue
        path = f'{prefix}.{k}' if prefix else k
        va, vb = a.get(k), b.get(k)
        if va == vb:
            continue
        if isinstance(va, dict) and isinstance(vb, dict):
            intents.extend(deep_diff_to_intents(entry, key, va, vb, path))
        elif isinstance(va, list) and isinstance(vb, list):
            if va != vb:
                intents.append({
                    'entry': entry, 'key': key,
                    'field': path, 'op': 'set', 'new': vb,
                })
        else:
            intents.append({
                'entry': entry, 'key': key,
                'field': path, 'op': 'set', 'new': vb,
            })
    return intents


def load_legacy_parser():
    """Load the legacy crimson_rs parser from _legacy/ dir. Returns module or None."""
    import importlib.util
    import os
    import sys

    legacy_pyd = None
    for base in [os.path.dirname(os.path.abspath(__file__)),
                 getattr(sys, '_MEIPASS', ''), os.getcwd()]:
        for rel in [
            os.path.join(base, 'crimson_rs', '_legacy', 'crimson_rs.pyd'),
        ]:
            p = os.path.normpath(rel)
            if os.path.isfile(p):
                legacy_pyd = p
                break
        if legacy_pyd:
            break
    if not legacy_pyd:
        return None
    saved_modules = {}
    for k in list(sys.modules):
        if k == 'crimson_rs' or k.startswith('crimson_rs.'):
            saved_modules[k] = sys.modules.pop(k)
    try:
        spec = importlib.util.spec_from_file_location("crimson_rs", legacy_pyd)
        legacy_rs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy_rs)
        return legacy_rs
    finally:
        for k in list(sys.modules):
            if k == 'crimson_rs' or k.startswith('crimson_rs.'):
                sys.modules.pop(k, None)
        sys.modules.update(saved_modules)
