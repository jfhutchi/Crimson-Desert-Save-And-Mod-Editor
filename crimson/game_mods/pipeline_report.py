from __future__ import annotations

import datetime
import os
import struct
import sys
import traceback
from typing import Any


class PipelineReport:
    def __init__(self, log_name: str = "logs.txt") -> None:
        self._lines: list[str] = []
        self._expected: dict[str, Any] = {}
        self._log_path = self._resolve_log_path(log_name)
        self.add(f"\n{'='*78}\n=== Pipeline report {datetime.datetime.now().isoformat()} ===\n{'='*78}")

    @staticmethod
    def _resolve_log_path(name: str) -> str:
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, name)

    def add(self, msg: str) -> None:
        self._lines.append(msg)

    def stage(self, name: str, detail: str = "") -> None:
        tag = f"[STAGE] {name}"
        self._lines.append(f"{tag}: {detail}" if detail else tag)

    def expect(self, kind: str, data: Any) -> None:
        self._expected[kind] = data
        self._lines.append(f"[EXPECT] {kind}: {self._summarize(data)}")

    @staticmethod
    def _summarize(data: Any) -> str:
        if isinstance(data, dict):
            return f"{len(data)} entries"
        if isinstance(data, (list, tuple)):
            return f"{len(data)} entries"
        return repr(data)

    def verify(self, final_data: bytes, rust_items_final: list | None = None) -> None:
        from crimson.game_mods import crimson_rs as crimson_rs

        final_bytes = bytes(final_data)
        items = rust_items_final
        if items is None and ('max_stacks' in self._expected
                              or 'inf_dura' in self._expected
                              or 'max_charges' in self._expected):
            try:
                items = crimson_rs.parse_iteminfo_from_bytes(final_bytes)
            except Exception as e:
                self.add(f"[VERIFY] rust re-parse FAILED: {e} — skipping rust-dict checks")
                items = None

        by_key: dict[int, dict] = {}
        if items is not None:
            by_key = {it.get('key'): it for it in items}

        self._verify_rust_dict('max_stacks', 'max_stack_count', by_key)
        self._verify_rust_dict('inf_dura', 'max_endurance', by_key, expected_override=65535)
        self._verify_rust_dict('max_charges', 'max_charged_useable_count', by_key)
        self._verify_buffs(by_key)
        self._verify_cooldowns(final_bytes)
        self._verify_transmog(final_bytes)

    def _verify_rust_dict(self, kind: str, field: str, by_key: dict,
                           expected_override: int | None = None) -> None:
        spec = self._expected.get(kind)
        if spec is None:
            return
        if not by_key:
            self.add(f"[VERIFY] {kind}: SKIPPED (no rust items)")
            return
        if isinstance(spec, dict):
            target = spec.get('target')
            keys = spec.get('items', list(by_key.keys()))
        else:
            target = expected_override if expected_override is not None else spec
            keys = list(by_key.keys())
        if expected_override is not None:
            target = expected_override
        ok = bad = missing = 0
        bad_samples: list[tuple[int, Any]] = []
        for k in keys:
            it = by_key.get(k)
            if not it:
                missing += 1
                continue
            v = it.get(field)
            if v == target:
                ok += 1
            else:
                bad += 1
                if len(bad_samples) < 5:
                    bad_samples.append((k, v))
        tag = "OK" if bad == 0 and ok > 0 else ("FAIL" if bad else "EMPTY")
        self.add(f"[VERIFY] {kind} ({field}={target}): {tag} ok={ok} bad={bad} missing={missing}"
                 + (f" samples={bad_samples}" if bad_samples else ""))

    def _verify_buffs(self, by_key: dict) -> None:
        spec = self._expected.get('buffs')
        if not spec:
            return
        ok = bad = 0
        bad_samples: list[tuple[int, Any]] = []
        for item_key, expected_buff_ids in spec.items():
            it = by_key.get(item_key)
            if not it:
                bad += 1
                if len(bad_samples) < 5:
                    bad_samples.append((item_key, 'MISSING_ITEM'))
                continue
            found_ids: set[int] = set()
            for ed in (it.get('enchant_data_list') or []):
                for eb in (ed.get('equip_buffs') or []):
                    bid = eb.get('buff_id') if isinstance(eb, dict) else None
                    if bid:
                        found_ids.add(int(bid))
            missing = set(expected_buff_ids) - found_ids
            if missing:
                bad += 1
                if len(bad_samples) < 5:
                    bad_samples.append((item_key, f"missing={sorted(missing)}"))
            else:
                ok += 1
        tag = "OK" if bad == 0 and ok > 0 else ("FAIL" if bad else "EMPTY")
        self.add(f"[VERIFY] buffs: {tag} ok={ok} bad={bad}"
                 + (f" samples={bad_samples}" if bad_samples else ""))

    def _verify_cooldowns(self, blob: bytes) -> None:
        spec = self._expected.get('cooldowns')
        if not spec:
            return
        ok = bad = 0
        bad_samples: list[tuple[int, int, int]] = []
        for item_key, (off, new_val) in spec.items():
            if off is None or off + 4 > len(blob):
                bad += 1
                continue
            actual = struct.unpack_from('<I', blob, off)[0]
            if actual == new_val:
                ok += 1
            else:
                bad += 1
                if len(bad_samples) < 5:
                    bad_samples.append((item_key, new_val, actual))
        tag = "OK" if bad == 0 and ok > 0 else ("FAIL" if bad else "EMPTY")
        self.add(f"[VERIFY] cooldowns: {tag} ok={ok} bad={bad}"
                 + (f" samples(key,expected,actual)={bad_samples}" if bad_samples else ""))

    def _verify_transmog(self, blob: bytes) -> None:
        spec = self._expected.get('transmog')
        if not spec:
            return
        ok = bad = 0
        bad_samples: list[tuple[int, int, int]] = []
        for sw in spec:
            src_hash = sw.get('src_hash')
            for off in sw.get('offsets', []):
                if off + 4 > len(blob):
                    bad += 1
                    continue
                actual = struct.unpack_from('<I', blob, off)[0]
                if actual == src_hash:
                    ok += 1
                else:
                    bad += 1
                    if len(bad_samples) < 5:
                        bad_samples.append((sw.get('tgt_key'), src_hash, actual))
        tag = "OK" if bad == 0 and ok > 0 else ("FAIL" if bad else "EMPTY")
        self.add(f"[VERIFY] transmog: {tag} ok={ok} bad={bad}"
                 + (f" samples(tgt_key,expected,actual)={bad_samples}" if bad_samples else ""))

    def write(self) -> str:
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write("\n".join(self._lines) + "\n")
        except Exception:
            sys.stdout.write("\n".join(self._lines) + "\n")
            sys.stdout.flush()
            traceback.print_exc()
        return self._log_path

    def render(self) -> str:
        return "\n".join(self._lines)
