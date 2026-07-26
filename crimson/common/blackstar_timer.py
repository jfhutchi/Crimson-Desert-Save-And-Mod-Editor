from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import shutil
import stat
import struct
import subprocess
import tempfile
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Mapping

import crimson_rs

log = logging.getLogger(__name__)


class TimerStatus(str, Enum):
    VANILLA = "vanilla"
    APPLIED = "applied"
    UNKNOWN = "unknown"
    PARTIAL = "partial"
    BACKUP_CONFLICT = "backup_conflict"
    GAME_RUNNING = "game_running"


class StalePreviewError(RuntimeError):
    """Raised when an archive changed after a successful preview."""


class TimerTransactionError(RuntimeError):
    """Raised after an Apply failure has been rolled back."""


class BackupConflictError(RuntimeError):
    """Raised when Restore cannot prove ownership of the current files."""


class GameRunningError(RuntimeError):
    """Raised when an archive write is requested while the game is open."""


class _MetadataRecoveryRequired(RuntimeError):
    """Defers restoration until the held metadata identity can be released."""

    def __init__(
        self,
        path: Path,
        displaced_path: Path,
        original: Exception,
        *,
        concurrent_bytes: bytes | None = None,
    ) -> None:
        super().__init__(str(original))
        self.path = path
        self.displaced_path = displaced_path
        self.original = original
        self.concurrent_bytes = concurrent_bytes


class _MetadataExchangeConflict(StalePreviewError):
    """Carries the concurrent preimage preservation state into rollback."""

    def __init__(
        self,
        path: Path,
        displaced_path: Path | None,
        *,
        restored: bool,
        detail: str,
    ) -> None:
        super().__init__(f"concurrent metadata replacement detected: {detail}")
        self.path = path
        self.displaced_path = displaced_path
        self.restored = restored


class _ArchiveSourceError(RuntimeError):
    """Wraps only source-inspection errors translated by legacy detection."""


def is_crimson_desert_running() -> bool:
    if os.name != "nt":
        return False
    completed = subprocess.run(
        [
            "tasklist",
            "/FI",
            "IMAGENAME eq CrimsonDesert.exe",
            "/FO",
            "CSV",
            "/NH",
        ],
        capture_output=True,
        text=True,
        check=True,
        creationflags=0x08000000,
    )
    return '"crimsondesert.exe"' in completed.stdout.lower()


@dataclass(frozen=True)
class TimerProfile:
    profile_id: str
    group_name: str
    directory: str
    file_name: str
    entry_offset: int
    vanilla_compressed_size: int
    uncompressed_size: int
    vanilla_body_sha256: str
    applied_body_sha256: str
    cooldown_offset: int
    duration_offset: int
    vanilla_cooldown_seconds: int
    vanilla_duration_seconds: int
    preset_cooldown_seconds: int
    preset_duration_seconds: int
    compression: int = 2
    crypto: int = 0

    @property
    def archive_path(self) -> str:
        directory = self.directory.strip("/")
        return f"{directory}/{self.file_name}" if directory else self.file_name


@dataclass(frozen=True)
class DetectionReport:
    status: TimerStatus
    reason: str
    profile_id: str
    game_dir: Path
    body_sha256: str | None = None
    cooldown_seconds: int | None = None
    duration_seconds: int | None = None
    entry_offset: int | None = None
    compressed_size: int | None = None
    uncompressed_size: int | None = None


@dataclass(frozen=True)
class ArchiveFileHash:
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class PreviewToken:
    profile_id: str
    game_dir: Path
    archive_hashes: tuple[ArchiveFileHash, ...]
    source_body_sha256: str
    candidate_body_sha256: str
    entry_offset: int
    source_compressed_size: int
    candidate_compressed_size: int


@dataclass(frozen=True)
class _ArchiveEntry:
    chunk_offset: int
    compressed_size: int
    uncompressed_size: int
    chunk_id: int
    compression: int
    crypto: int

    @classmethod
    def from_mapping(cls, entry: Mapping[str, object]) -> _ArchiveEntry:
        return cls(
            chunk_offset=int(entry["chunk_offset"]),
            compressed_size=int(entry["compressed_size"]),
            uncompressed_size=int(entry["uncompressed_size"]),
            chunk_id=int(entry["chunk_id"]),
            compression=int(entry["compression"]),
            crypto=int(entry["crypto"]),
        )


@dataclass(frozen=True)
class _PazIdentity:
    sha256: str
    source_sha256: str
    checksum: int
    size: int
    entry_bytes: bytes | None
    source_entry_sha256: str


@dataclass(frozen=True)
class _BackupFileIdentity:
    relative_path: str
    sha256: str
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _HeldBackupSet:
    path: Path
    root_fd: int | None
    directory_identity: tuple[int, int]
    files: tuple[_BackupFileIdentity, ...]


class _JenkinsChecksum:
    _mask = 0xFFFFFFFF
    _triple = struct.Struct("<III")

    def __init__(self, length: int) -> None:
        self._length = length
        self._seen = 0
        self._processed = 0
        self._tail = bytearray()
        seed = (length + 0xDEBA1DCD) & self._mask
        self._a = self._b = self._c = seed

    @classmethod
    def _rotate(cls, value: int, count: int) -> int:
        return (
            (value << count) | (value >> (32 - count))
        ) & cls._mask

    def _mix_block(self, block: bytes | memoryview) -> None:
        word_a, word_b, word_c = self._triple.unpack(block)
        a = (self._a + word_a) & self._mask
        b = (self._b + word_b) & self._mask
        c = (self._c + word_c) & self._mask
        a = ((a - c) ^ self._rotate(c, 4)) & self._mask
        c = (c + b) & self._mask
        b = ((b - a) ^ self._rotate(a, 6)) & self._mask
        a = (a + c) & self._mask
        c = ((c - b) ^ self._rotate(b, 8)) & self._mask
        b = (b + a) & self._mask
        a = ((a - c) ^ self._rotate(c, 16)) & self._mask
        c = (c + b) & self._mask
        b = ((b - a) ^ self._rotate(a, 19)) & self._mask
        a = (a + c) & self._mask
        c = ((c - b) ^ self._rotate(b, 4)) & self._mask
        b = (b + a) & self._mask
        self._a, self._b, self._c = a, b, c

    def update(self, data: bytes) -> None:
        if self._seen + len(data) > self._length:
            raise ValueError("Checksum stream exceeded its declared length")
        self._seen += len(data)
        view = memoryview(data)
        offset = 0

        if self._tail and self._processed + 12 < self._length:
            needed = 12 - len(self._tail)
            take = min(needed, len(view))
            self._tail.extend(view[:take])
            offset += take
            if len(self._tail) == 12:
                self._mix_block(self._tail)
                self._processed += 12
                self._tail.clear()

        while (
            offset + 12 <= len(view)
            and self._processed + 12 < self._length
        ):
            self._mix_block(view[offset:offset + 12])
            self._processed += 12
            offset += 12

        if offset < len(view):
            self._tail.extend(view[offset:])

    def digest(self) -> int:
        if self._seen != self._length:
            raise ValueError("Checksum stream ended before its declared length")
        if not self._tail:
            return self._c
        if len(self._tail) > 12:
            raise ValueError("Checksum tail exceeds one block")

        padded = bytes(self._tail) + b"\x00" * (12 - len(self._tail))
        word_a, word_b, word_c = self._triple.unpack(padded)
        a = (self._a + word_a) & self._mask
        b = (self._b + word_b) & self._mask
        c = (self._c + word_c) & self._mask
        c = ((c ^ b) - self._rotate(b, 14)) & self._mask
        a = ((a ^ c) - self._rotate(c, 11)) & self._mask
        b = ((b ^ a) - self._rotate(a, 25)) & self._mask
        c = ((c ^ b) - self._rotate(b, 16)) & self._mask
        a = ((a ^ c) - self._rotate(c, 4)) & self._mask
        b = ((b ^ a) - self._rotate(a, 14)) & self._mask
        c = ((c ^ b) - self._rotate(b, 24)) & self._mask
        return c


@dataclass(frozen=True)
class _ArchiveInspection:
    report: DetectionReport
    entry: _ArchiveEntry
    body: bytes
    paths: tuple[Path, Path, Path]


@dataclass(frozen=True)
class PreviewReport:
    status: TimerStatus
    reason: str
    profile_id: str
    game_dir: Path
    token: PreviewToken | None
    cooldown_before: int | None
    cooldown_after: int | None
    duration_before: int | None
    duration_after: int | None
    source_body_sha256: str | None
    candidate_body_sha256: str | None
    candidate_compressed_size: int | None
    slot_capacity: int | None


@dataclass(frozen=True)
class TransactionReport:
    status: TimerStatus
    action: str
    changed: bool
    reason: str
    profile_id: str
    game_dir: Path
    backup_dir: Path | None
    source_body_sha256: str | None
    candidate_body_sha256: str | None
    source_compressed_size: int | None
    candidate_compressed_size: int | None
    cooldown_seconds: int | None
    duration_seconds: int | None


BLACKSTAR_114_PROFILE = TimerProfile(
    profile_id="blackstar-timer-114-v1",
    group_name="0008",
    directory="gamedata",
    file_name="characterinfo.pabgb",
    entry_offset=2_381_376,
    vanilla_compressed_size=1_194_691,
    uncompressed_size=26_431_464,
    vanilla_body_sha256=(
        "e234565b744fb1bb304547b5883cf9249c6cfff54c034c2611a87da26d8324d2"
    ),
    applied_body_sha256=(
        "c90f6689c0aa757efa702e51669be8ff9ff0ab58a4393bc220ee390903fa1402"
    ),
    cooldown_offset=25_579_991,
    duration_offset=25_579_999,
    vanilla_cooldown_seconds=3600,
    vanilla_duration_seconds=600,
    preset_cooldown_seconds=1,
    preset_duration_seconds=1800,
)


class BlackstarTimerService:
    def __init__(
        self,
        profile: TimerProfile = BLACKSTAR_114_PROFILE,
        fault_injector: Callable[[str], None] | None = None,
        process_checker: Callable[[], bool] | None = None,
    ) -> None:
        self.profile = profile
        self._fault_injector = fault_injector
        self._process_checker = process_checker or is_crimson_desert_running

    def detect(self, game_dir: str | Path) -> DetectionReport:
        game = Path(game_dir).expanduser().resolve()
        process_report = self._process_report(game)
        if process_report is not None:
            return process_report
        try:
            return self._inspect(game).report
        except _ArchiveSourceError as exc:
            return self._report(
                TimerStatus.UNKNOWN,
                game,
                f"Archive compatibility check failed: {exc}",
            )

    def preview(
        self,
        game_dir: str | Path,
        progress: Callable[[str, int], None] | None = None,
    ) -> PreviewReport:
        self._emit_progress(progress, "process_check", 5)
        game = Path(game_dir).expanduser().resolve()
        process_report = self._process_report(game)
        inspection = None
        if process_report is not None:
            detection = process_report
        else:
            try:
                inspection = self._inspect(game)
                detection = inspection.report
            except _ArchiveSourceError as exc:
                detection = self._report(
                    TimerStatus.UNKNOWN,
                    game,
                    f"Archive compatibility check failed: {exc}",
                )
        if detection.status is not TimerStatus.VANILLA:
            return PreviewReport(
                status=detection.status,
                reason=detection.reason,
                profile_id=self.profile.profile_id,
                game_dir=detection.game_dir,
                token=None,
                cooldown_before=detection.cooldown_seconds,
                cooldown_after=None,
                duration_before=detection.duration_seconds,
                duration_after=None,
                source_body_sha256=detection.body_sha256,
                candidate_body_sha256=None,
                candidate_compressed_size=None,
                slot_capacity=detection.compressed_size,
            )
        if inspection is None or detection.body_sha256 is None:
            raise ValueError("Vanilla detection is missing its archive inspection")

        self._emit_progress(progress, "pamt_lookup", 20)
        entry = inspection.entry
        source = inspection.body
        self._emit_progress(progress, "decompression", 40)
        candidate = self._build_candidate(source)
        candidate_hash = hashlib.sha256(candidate).hexdigest()
        if candidate_hash != self.profile.applied_body_sha256:
            raise ValueError(
                "Candidate body hash does not match the enrolled applied schema"
            )
        candidate_compressed = bytes(
            crimson_rs.compress_data(candidate, entry.compression)
        )
        self._emit_progress(progress, "candidate_compression", 70)
        slot_capacity = entry.compressed_size
        if len(candidate_compressed) > slot_capacity:
            raise ValueError(
                "Candidate compressed stream does not fit the enrolled PAZ slot"
            )
        verified = self._decompress_stream(
            candidate_compressed,
            entry.compression,
            entry.uncompressed_size,
        )
        if verified != candidate:
            raise ValueError("Candidate failed independent compression verification")
        self._emit_progress(progress, "candidate_verification", 90)
        token = PreviewToken(
            profile_id=self.profile.profile_id,
            game_dir=detection.game_dir,
            archive_hashes=tuple(
                ArchiveFileHash(
                    relative_path=path.relative_to(detection.game_dir).as_posix(),
                    sha256=self._hash_file(
                        path,
                        "preview:" + path.relative_to(detection.game_dir).as_posix(),
                    ),
                )
                for path in inspection.paths
            ),
            source_body_sha256=detection.body_sha256,
            candidate_body_sha256=candidate_hash,
            entry_offset=entry.chunk_offset,
            source_compressed_size=entry.compressed_size,
            candidate_compressed_size=len(candidate_compressed),
        )
        return PreviewReport(
            status=TimerStatus.VANILLA,
            reason="Verified preview; Apply is enabled for this exact source",
            profile_id=self.profile.profile_id,
            game_dir=detection.game_dir,
            token=token,
            cooldown_before=detection.cooldown_seconds,
            cooldown_after=self.profile.preset_cooldown_seconds,
            duration_before=detection.duration_seconds,
            duration_after=self.profile.preset_duration_seconds,
            source_body_sha256=detection.body_sha256,
            candidate_body_sha256=candidate_hash,
            candidate_compressed_size=len(candidate_compressed),
            slot_capacity=slot_capacity,
        )

    def _normalize_token_archive_hashes(
        self,
        archive_hashes: tuple[ArchiveFileHash, ...],
    ) -> dict[str, str]:
        expected_hashes: dict[str, str] = {}
        for expected in archive_hashes:
            try:
                relative = self._archive_relative_path(
                    expected.relative_path
                ).as_posix()
            except BackupConflictError as exc:
                raise StalePreviewError(str(exc)) from exc
            if relative in expected_hashes:
                raise StalePreviewError("Source archive changed after preview")
            expected_hashes[relative] = expected.sha256
        return expected_hashes

    @staticmethod
    def _resolve_preview_game_root(game_dir: Path) -> Path:
        expanded = game_dir.expanduser()
        try:
            return expanded.resolve(strict=True)
        except OSError as exc:
            raise StalePreviewError(
                "Source archive changed after preview"
            ) from exc
        except RuntimeError as exc:
            if not str(exc).startswith("Symlink loop from "):
                raise
            raise StalePreviewError(
                "Source archive changed after preview"
            ) from exc

    def validate_preview_token(self, token: PreviewToken) -> None:
        if token.profile_id != self.profile.profile_id:
            raise StalePreviewError("Preview profile does not match this service")
        expected_hashes = self._normalize_token_archive_hashes(
            token.archive_hashes
        )
        game = self._resolve_preview_game_root(token.game_dir)
        if game != token.game_dir:
            raise StalePreviewError("Preview game path is no longer normalized")
        process_report = self._process_report(game)
        if process_report is not None:
            raise StalePreviewError("Source archive changed after preview")
        try:
            inspection = self._inspect(game)
        except _ArchiveSourceError as exc:
            raise StalePreviewError("Source archive changed after preview") from exc
        self._validate_token_against_inspection(
            token, inspection, expected_hashes
        )

    def _validate_token_against_inspection(
        self,
        token: PreviewToken,
        inspection: _ArchiveInspection,
        expected_hashes: dict[str, str],
    ) -> dict[str, str]:
        detection = inspection.report
        if (
            detection.status is not TimerStatus.VANILLA
            or detection.body_sha256 != token.source_body_sha256
            or detection.entry_offset != token.entry_offset
            or detection.compressed_size != token.source_compressed_size
        ):
            raise StalePreviewError("Source archive changed after preview")

        game = detection.game_dir

        current_hashes: dict[str, str] = {}
        for source_path in inspection.paths:
            try:
                relative = source_path.resolve().relative_to(game).as_posix()
                path = self._contained_path(game, relative)
            except (BackupConflictError, ValueError) as exc:
                raise StalePreviewError(str(exc)) from exc
            if not path.is_file():
                raise StalePreviewError(
                    f"Source archive changed after preview: {relative}"
                )
            current_hashes[relative] = self._hash_file(
                path,
                f"source-revalidation:{relative}",
            )

        if current_hashes != expected_hashes:
            changed = sorted(
                key
                for key in current_hashes.keys() | expected_hashes.keys()
                if current_hashes.get(key) != expected_hashes.get(key)
            )
            detail = f": {changed[0]}" if changed else ""
            raise StalePreviewError(
                f"Source archive changed after preview{detail}"
            )
        return current_hashes

    def apply(
        self,
        token: PreviewToken,
        progress: Callable[[str, int], None] | None = None,
    ) -> TransactionReport:
        if token.profile_id != self.profile.profile_id:
            raise StalePreviewError("Preview profile does not match this service")
        expected_hashes = self._normalize_token_archive_hashes(
            token.archive_hashes
        )
        game = self._resolve_preview_game_root(token.game_dir)
        if game != token.game_dir:
            raise StalePreviewError("Preview game path is no longer normalized")

        self._emit_progress(progress, "process_check", 5)
        self._ensure_game_closed()
        process_report = self._process_report(game)
        inspection = None
        if process_report is not None:
            current = process_report
        else:
            try:
                inspection = self._inspect(game)
                current = inspection.report
            except _ArchiveSourceError as exc:
                current = self._report(
                    TimerStatus.UNKNOWN,
                    game,
                    f"Archive compatibility check failed: {exc}",
                )
        if (
            current.status is TimerStatus.APPLIED
            and current.body_sha256 == token.candidate_body_sha256
        ):
            return TransactionReport(
                status=TimerStatus.APPLIED,
                action="none",
                changed=False,
                reason="Verified preset is already applied; no files were written",
                profile_id=self.profile.profile_id,
                game_dir=game,
                backup_dir=self._latest_finalized_backup(game),
                source_body_sha256=token.source_body_sha256,
                candidate_body_sha256=token.candidate_body_sha256,
                source_compressed_size=token.source_compressed_size,
                candidate_compressed_size=current.compressed_size,
                cooldown_seconds=current.cooldown_seconds,
                duration_seconds=current.duration_seconds,
            )

        process_report = self._process_report(game)
        if process_report is not None:
            raise StalePreviewError("Source archive changed after preview")
        if inspection is None:
            try:
                inspection = self._inspect(game)
            except _ArchiveSourceError as exc:
                raise StalePreviewError("Source archive changed after preview") from exc
        source_hashes = self._validate_token_against_inspection(
            token, inspection, expected_hashes
        )
        self._emit_progress(progress, "source_revalidation", 15)

        entry = inspection.entry
        candidate = self._build_candidate(inspection.body)
        candidate_compressed = bytes(
            crimson_rs.compress_data(candidate, entry.compression)
        )
        if (
            hashlib.sha256(candidate).hexdigest() != token.candidate_body_sha256
            or len(candidate_compressed) != token.candidate_compressed_size
        ):
            raise StalePreviewError("Candidate no longer matches the verified preview")

        paths = inspection.paths
        padding = token.source_compressed_size - len(candidate_compressed)
        if padding < 0:
            raise ValueError("Candidate no longer fits the enrolled PAZ slot")
        slot_payload = candidate_compressed + bytes(padding)

        with ExitStack() as backup_guard:
            backup, expected_paz = self._create_backup(
                game,
                paths,
                source_hashes,
                token,
                slot_payload,
                backup_guard,
            )
            return self._apply_with_backup_guard(
                game,
                token,
                progress,
                source_hashes,
                paths,
                entry,
                len(candidate_compressed),
                slot_payload,
                backup,
                expected_paz,
            )

    def _apply_with_backup_guard(
        self,
        game: Path,
        token: PreviewToken,
        progress: Callable[[str, int], None] | None,
        source_hashes: dict[str, str],
        paths: tuple[Path, Path, Path],
        entry: _ArchiveEntry,
        candidate_compressed_size: int,
        slot_payload: bytes,
        backup: _HeldBackupSet,
        expected_paz: _PazIdentity,
    ) -> TransactionReport:
        backup_dir = backup.path
        self._before_backup_apply_step("after-create", backup_dir)
        self._assert_backup_set_complete(
            game,
            backup,
            source_hashes,
            "backup-post-create",
        )
        self._emit_progress(progress, "backup_verification", 30)
        manifest_path = backup_dir / "manifest.json"
        paz_path, pamt_path, papgt_path = paths

        mutation_attempted = False
        try:
            mutation_attempted = True
            self._patch_paz_slot(
                paz_path,
                token.entry_offset,
                slot_payload,
                expected_paz.source_entry_sha256,
                trusted_root=game,
            )
            self._emit_progress(progress, "paz_write", 50)
            self._inject_fault("after_paz_write")

            pamt_relative = pamt_path.relative_to(game).as_posix()
            papgt_relative = papgt_path.relative_to(game).as_posix()
            source_pamt = self._read_metadata_bytes(
                backup_dir / pamt_relative,
                f"metadata-build:{pamt_relative}",
            )
            source_papgt = self._read_metadata_bytes(
                backup_dir / papgt_relative,
                f"metadata-build:{papgt_relative}",
            )
            new_pamt, new_papgt = self._build_metadata_bytes(
                backup_dir,
                entry,
                candidate_compressed_size,
                expected_paz.checksum,
                expected_paz.size,
                source_metadata=(source_pamt, source_papgt),
            )
            self._assert_metadata_unchanged(
                pamt_path,
                source_pamt,
                f"metadata-guard-after-paz:{pamt_relative}",
            )
            self._assert_metadata_unchanged(
                papgt_path,
                source_papgt,
                f"metadata-guard-after-paz:{papgt_relative}",
            )
            expected_post_hashes = {
                paz_path.relative_to(game).as_posix(): expected_paz.sha256,
                pamt_relative: self._sha256_bytes(new_pamt),
                papgt_relative: self._sha256_bytes(new_papgt),
            }
            self._atomic_write(
                pamt_path,
                new_pamt,
                "pamt",
                trusted_root=game,
                expected_current=source_pamt,
                metadata_guard_operation=(
                    f"metadata-guard-before-replace:{pamt_relative}"
                ),
            )
            self._emit_progress(progress, "pamt_update", 65)
            self._inject_fault("after_pamt_write")
            self._atomic_write(
                papgt_path,
                new_papgt,
                "papgt",
                trusted_root=game,
                expected_current=source_papgt,
                metadata_guard_operation=(
                    f"metadata-guard-before-replace:{papgt_relative}"
                ),
            )
            self._emit_progress(progress, "papgt_update", 80)
            self._inject_fault("after_papgt_write")
            verified, post_hashes = self._verify_archive_set(
                game,
                paths,
                expected_post_hashes,
                TimerStatus.APPLIED,
                token.candidate_body_sha256,
            )
            self._emit_progress(progress, "post_write_verification", 95)
            manifest = self._read_manifest(
                manifest_path,
                "manifest-finalization",
            )
            self._before_backup_apply_step(
                "before-finalization",
                backup_dir,
            )
            self._assert_backup_set_complete(
                game,
                backup,
                source_hashes,
                "backup-finalization",
            )
            manifest["post_apply_hashes"] = post_hashes
            manifest["finalized"] = True
            manifest["finalized_at"] = datetime.now(timezone.utc).isoformat()
            self._write_manifest(
                manifest_path,
                manifest,
                trusted_root=game,
                held_directory_root=backup_dir,
                held_directory_root_fd=backup.root_fd,
            )
        except Exception as exc:
            if not mutation_attempted:
                raise
            if isinstance(exc, _MetadataExchangeConflict):
                preserved_paths: set[str] = set()
                recovery_path = exc.displaced_path
                if exc.restored:
                    preserved_paths.add(
                        exc.path.relative_to(game).as_posix()
                    )
                    recovery_path = exc.path
                try:
                    if recovery_path is None or not recovery_path.is_file():
                        raise OSError(
                            "Concurrent metadata recovery asset is missing"
                        )
                    recovery_bytes = self._read_metadata_bytes(
                        recovery_path,
                        "metadata-conflict-recovery-baseline",
                    )
                    self._before_backup_apply_step(
                        "during-rollback",
                        backup_dir,
                    )
                    self._assert_backup_set_complete(
                        game,
                        backup,
                        source_hashes,
                        "backup-conflict-rollback",
                    )
                    self._restore_backup_files(
                        game,
                        backup_dir,
                        source_hashes,
                        preserve_paths=preserved_paths,
                    )
                    if self._read_metadata_bytes(
                        recovery_path,
                        "metadata-conflict-recovery-verification",
                    ) != recovery_bytes:
                        raise OSError(
                            "Concurrent metadata changed during transaction rollback"
                        )
                except Exception as rollback_exc:
                    raise TimerTransactionError(
                        f"Apply failed: {exc}; rollback also failed: "
                        f"{rollback_exc}"
                    ) from exc
                if exc.restored:
                    raise TimerTransactionError(
                        "Apply aborted after a concurrent metadata replacement; "
                        "transaction changes were rolled back without overwriting "
                        f"the concurrent file: {exc.path}"
                    ) from exc
                raise TimerTransactionError(
                    "Apply aborted after a concurrent metadata replacement; "
                    "transaction changes were rolled back and the concurrent file "
                    f"was retained for recovery at: {exc.displaced_path}"
                ) from exc
            try:
                self._before_backup_apply_step(
                    "during-rollback",
                    backup_dir,
                )
                self._assert_backup_set_complete(
                    game,
                    backup,
                    source_hashes,
                    "backup-rollback",
                )
                self._restore_backup_files(game, backup_dir, source_hashes)
                self._verify_archive_set(
                    game,
                    paths,
                    source_hashes,
                    TimerStatus.VANILLA,
                    token.source_body_sha256,
                )
                manifest = self._read_manifest(
                    manifest_path,
                    "manifest-rollback",
                )
                manifest["post_apply_hashes"] = {}
                manifest["finalized"] = False
                manifest.pop("finalized_at", None)
                manifest["rolled_back"] = True
                manifest["rollback_reason"] = str(exc)
                self._write_manifest(
                    manifest_path,
                    manifest,
                    trusted_root=game,
                    held_directory_root=backup_dir,
                    held_directory_root_fd=backup.root_fd,
                )
            except Exception as rollback_exc:
                raise TimerTransactionError(
                    f"Apply failed: {exc}; rollback also failed: {rollback_exc}"
                ) from exc
            raise TimerTransactionError(
                f"Apply failed and all three game archives were rolled back: {exc}"
            ) from exc

        return TransactionReport(
            status=TimerStatus.APPLIED,
            action="apply",
            changed=True,
            reason="Blackstar 30 minute / 1 second preset applied and verified",
            profile_id=self.profile.profile_id,
            game_dir=game,
            backup_dir=backup_dir,
            source_body_sha256=token.source_body_sha256,
            candidate_body_sha256=token.candidate_body_sha256,
            source_compressed_size=token.source_compressed_size,
            candidate_compressed_size=token.candidate_compressed_size,
            cooldown_seconds=verified.cooldown_seconds,
            duration_seconds=verified.duration_seconds,
        )

    def restore(
        self,
        game_dir: str | Path,
        progress: Callable[[str, int], None] | None = None,
    ) -> TransactionReport:
        self._emit_progress(progress, "process_check", 5)
        self._ensure_game_closed()
        game = Path(game_dir).expanduser().resolve()
        backup_dir = self._latest_finalized_backup(game)
        if backup_dir is None:
            raise BackupConflictError("No finalized Blackstar Timer backup was found")
        manifest_path = backup_dir / "manifest.json"
        manifest = self._read_manifest(manifest_path)
        if manifest.get("profile_id") != self.profile.profile_id:
            raise BackupConflictError("Backup profile does not match this service")
        if Path(str(manifest.get("game_dir", ""))).resolve() != game:
            raise BackupConflictError("Backup belongs to a different game directory")
        source_hashes = self._manifest_hashes(manifest, "source_hashes")
        post_hashes = self._manifest_hashes(manifest, "post_apply_hashes")
        expected_paths = self._expected_archive_paths(game)
        if set(source_hashes) != expected_paths or set(post_hashes) != expected_paths:
            raise BackupConflictError(
                "Backup manifest archive paths do not match the enrolled timer files"
            )
        for relative, expected in source_hashes.items():
            backup_file = self._contained_path(backup_dir, relative)
            if not backup_file.is_file() or self._hash_file(backup_file) != expected:
                raise BackupConflictError(f"Backup file is missing or altered: {relative}")
        self._emit_progress(progress, "backup_verification", 30)
        for relative, expected in post_hashes.items():
            current_file = self._contained_path(game, relative)
            if not current_file.is_file() or self._hash_file(current_file) != expected:
                raise BackupConflictError(
                    f"Game archive changed after this preset was applied: {relative}"
                )

        rollback_dir = Path(
            tempfile.mkdtemp(prefix=".restore-rollback-", dir=backup_dir.parent)
        )
        try:
            self._restore_backup_files(rollback_dir, game, post_hashes)
            try:
                self._restore_backup_files(
                    game,
                    backup_dir,
                    source_hashes,
                    after_replace=lambda index, _relative: self._inject_fault(
                        f"after_restore_file_{index}"
                    ),
                )
                self._emit_progress(progress, "archive_restore", 70)
                detection = self.detect(game)
                if detection.status is not TimerStatus.VANILLA:
                    raise TimerTransactionError(
                        "Vanilla verification failed: " + detection.reason
                    )
                self._emit_progress(progress, "restore_verification", 95)
                manifest["restored"] = True
                manifest["restored_at"] = datetime.now(timezone.utc).isoformat()
                self._write_manifest(manifest_path, manifest)
            except Exception as exc:
                try:
                    self._restore_backup_files(game, rollback_dir, post_hashes)
                    rollback_detection = self.detect(game)
                    if rollback_detection.status is not TimerStatus.APPLIED:
                        raise TimerTransactionError(
                            "Applied-state verification failed: "
                            + rollback_detection.reason
                        )
                except Exception as rollback_exc:
                    raise TimerTransactionError(
                        f"Restore failed: {exc}; rollback also failed: {rollback_exc}"
                    ) from exc
                raise TimerTransactionError(
                    "Restore failed and all three archives were rolled back to the "
                    f"applied state: {exc}"
                ) from exc
        finally:
            try:
                shutil.rmtree(rollback_dir)
            except OSError as exc:
                log.warning(
                    "Could not remove temporary restore rollback directory %s: %s",
                    rollback_dir,
                    exc,
                )
        return TransactionReport(
            status=TimerStatus.VANILLA,
            action="restore",
            changed=True,
            reason="Original Blackstar timer archives restored and verified",
            profile_id=self.profile.profile_id,
            game_dir=game,
            backup_dir=backup_dir,
            source_body_sha256=detection.body_sha256,
            candidate_body_sha256=None,
            source_compressed_size=detection.compressed_size,
            candidate_compressed_size=None,
            cooldown_seconds=detection.cooldown_seconds,
            duration_seconds=detection.duration_seconds,
        )

    def _stream_paz_identity(
        self,
        path: Path,
        entry_offset: int,
        entry_size: int,
        replacement: bytes | None = None,
        operation: str | None = None,
    ) -> _PazIdentity:
        del operation
        size = path.stat().st_size
        entry_end = entry_offset + entry_size
        if entry_offset < 0 or entry_size < 0 or entry_end > size:
            raise ValueError("PAZ entry range is outside the archive")
        if replacement is not None and len(replacement) != entry_size:
            raise ValueError("PAZ replacement must exactly fill the enrolled slot")

        sha256 = hashlib.sha256()
        source_sha256 = hashlib.sha256()
        checksum = _JenkinsChecksum(size)
        source_entry_sha256 = hashlib.sha256()

        def update_identity(data: bytes) -> None:
            sha256.update(data)
            checksum.update(data)

        with path.open("rb") as handle:
            if replacement is not None:
                remaining = entry_offset
                while remaining:
                    block = handle.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("PAZ ended before the enrolled entry")
                    update_identity(block)
                    source_sha256.update(block)
                    remaining -= len(block)

                remaining = entry_size
                while remaining:
                    block = handle.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("PAZ entry is truncated")
                    source_entry_sha256.update(block)
                    source_sha256.update(block)
                    remaining -= len(block)
                update_identity(replacement)

                while True:
                    block = handle.read(1024 * 1024)
                    if not block:
                        break
                    update_identity(block)
                    source_sha256.update(block)
                entry_bytes = None
            else:
                position = 0
                captured = bytearray()
                while True:
                    block = handle.read(1024 * 1024)
                    if not block:
                        break
                    update_identity(block)
                    source_sha256.update(block)
                    block_end = position + len(block)
                    overlap_start = max(position, entry_offset)
                    overlap_end = min(block_end, entry_end)
                    if overlap_start < overlap_end:
                        relative_start = overlap_start - position
                        relative_end = overlap_end - position
                        entry_block = block[relative_start:relative_end]
                        captured.extend(entry_block)
                        source_entry_sha256.update(entry_block)
                    position = block_end
                entry_bytes = bytes(captured)

        return _PazIdentity(
            sha256=sha256.hexdigest(),
            source_sha256=source_sha256.hexdigest(),
            checksum=checksum.digest(),
            size=size,
            entry_bytes=entry_bytes,
            source_entry_sha256=source_entry_sha256.hexdigest(),
        )

    def _patch_paz_slot(
        self,
        paz_path: Path,
        entry_offset: int,
        slot_payload: bytes,
        source_entry_sha256: str,
        trusted_root: Path | None = None,
    ) -> None:
        if trusted_root is not None:
            self._assert_safe_archive_path(
                trusted_root,
                paz_path,
                "paz-open",
                require_exists=True,
            )
        with paz_path.open("r+b") as handle:
            handle.seek(entry_offset)
            source_entry = self._read_paz_slot(
                handle,
                len(slot_payload),
                "paz-patch:"
                + (
                    paz_path.relative_to(trusted_root).as_posix()
                    if trusted_root is not None
                    else paz_path.name
                )
                + "-slot",
            )
            if (
                len(source_entry) != len(slot_payload)
                or self._sha256_bytes(source_entry) != source_entry_sha256
            ):
                raise StalePreviewError("Source archive changed after preview")
            if trusted_root is not None:
                self._assert_safe_archive_path(
                    trusted_root,
                    paz_path,
                    "paz-write",
                    require_exists=True,
                )
            handle.seek(entry_offset)
            self._write_all(handle, slot_payload, "paz-slot")
            self._flush_file(handle, "paz-slot")
            self._fsync_file(handle, "paz-slot")

    @staticmethod
    def _read_metadata_bytes(
        source: Path | BinaryIO,
        operation: str,
    ) -> bytes:
        del operation
        if isinstance(source, Path):
            return source.read_bytes()
        source.seek(0)
        data = source.read()
        source.seek(0)
        return data

    @staticmethod
    def _parse_pamt_bytes(data: bytes, operation: str) -> dict:
        del operation
        return crimson_rs.parse_pamt_bytes(data)

    @staticmethod
    def _parse_papgt_bytes(data: bytes, operation: str) -> dict:
        del operation
        return crimson_rs.parse_papgt_bytes(data)

    @staticmethod
    def _read_paz_slot(
        handle: BinaryIO,
        size: int,
        operation: str,
    ) -> bytes:
        del operation
        return handle.read(size)


    @staticmethod
    def _before_metadata_replace(path: Path, operation: str) -> None:
        del path, operation

    @staticmethod
    def _before_metadata_exchange(path: Path, operation: str) -> None:
        del path, operation

    @staticmethod
    def _metadata_displaced_path(path: Path, operation: str) -> Path:
        del operation
        return path.with_name(
            f".{path.name}.displaced-{next(tempfile._get_candidate_names())}"
        )

    @staticmethod
    def _remove_displaced_metadata(displaced: Path, operation: str) -> None:
        del operation
        displaced.unlink()


    @contextmanager
    def _hold_metadata_replace_guard(
        self,
        path: Path,
        expected: bytes,
        operation: str,
    ) -> Iterator[tuple[int, int]]:
        descriptor: int | None = None
        handle: BinaryIO | None = None
        raw_windows_handle: object | None = None
        try:
            try:
                if os.name == "nt":
                    from ctypes import wintypes
                    import msvcrt

                    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                    create_file = kernel32.CreateFileW
                    create_file.argtypes = [
                        wintypes.LPCWSTR,
                        wintypes.DWORD,
                        wintypes.DWORD,
                        wintypes.LPVOID,
                        wintypes.DWORD,
                        wintypes.DWORD,
                        wintypes.HANDLE,
                    ]
                    create_file.restype = wintypes.HANDLE
                    raw_windows_handle = create_file(
                        str(path),
                        0x80000000,  # GENERIC_READ
                        0x00000001 | 0x00000004,
                        None,
                        3,  # OPEN_EXISTING
                        0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
                        None,
                    )
                    handle_value = (
                        raw_windows_handle
                        if isinstance(raw_windows_handle, int)
                        else ctypes.cast(
                            raw_windows_handle,
                            ctypes.c_void_p,
                        ).value
                    )
                    if handle_value in (None, ctypes.c_void_p(-1).value):
                        error = ctypes.get_last_error()
                        raise ctypes.WinError(
                            error,
                            f"Metadata guard open failed ({operation}): {path}",
                        )
                    attributes = self._windows_path_attributes_no_follow(
                        path,
                        operation,
                    )
                    if attributes & 0x00000400:
                        raise OSError(
                            f"Metadata guard rejected a reparse point: {path}"
                        )
                    descriptor = msvcrt.open_osfhandle(
                        int(handle_value),
                        os.O_RDONLY | getattr(os, "O_BINARY", 0),
                    )
                    raw_windows_handle = None
                else:
                    descriptor = os.open(
                        path,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    )
                    try:
                        import fcntl

                        # flock is advisory. The identity/timestamp recheck catches
                        # ordinary noncooperative writes, but a writer deliberately
                        # preserving all stat fields remains a POSIX limitation.
                        fcntl.flock(
                            descriptor,
                            fcntl.LOCK_SH | fcntl.LOCK_NB,
                        )
                    except ImportError:
                        pass

                handle = os.fdopen(descriptor, "rb")
                descriptor = None
                held_before = os.fstat(handle.fileno())
                path_before = os.stat(path, follow_symlinks=False)
                if (
                    (held_before.st_dev, held_before.st_ino)
                    != (path_before.st_dev, path_before.st_ino)
                    or self._read_metadata_bytes(handle, operation) != expected
                ):
                    raise StalePreviewError(
                        f"Live metadata changed during apply: {path.name}"
                    )

                self._before_metadata_replace(path, operation)

                held_after = os.fstat(handle.fileno())
                path_after = os.stat(path, follow_symlinks=False)
                held_fingerprint = (
                    held_after.st_dev,
                    held_after.st_ino,
                    held_after.st_size,
                    held_after.st_mtime_ns,
                    held_after.st_ctime_ns,
                )
                expected_fingerprint = (
                    held_before.st_dev,
                    held_before.st_ino,
                    held_before.st_size,
                    held_before.st_mtime_ns,
                    held_before.st_ctime_ns,
                )
                if (
                    held_fingerprint != expected_fingerprint
                    or (held_after.st_dev, held_after.st_ino)
                    != (path_after.st_dev, path_after.st_ino)
                ):
                    raise StalePreviewError(
                        f"Live metadata changed during apply: {path.name}"
                    )
            except StalePreviewError:
                raise
            except OSError as exc:
                raise StalePreviewError(
                    f"Live metadata changed during apply: {path.name}"
                ) from exc
            yield (held_after.st_dev, held_after.st_ino)
        finally:
            if handle is not None:
                handle.close()
            elif descriptor is not None:
                os.close(descriptor)
            elif raw_windows_handle is not None:
                self._close_windows_handle(
                    raw_windows_handle,
                    operation,
                )

    def _assert_metadata_unchanged(
        self,
        path: Path,
        expected: bytes,
        operation: str,
    ) -> None:
        if self._read_metadata_bytes(path, operation) != expected:
            raise StalePreviewError(
                f"Live metadata changed during apply: {path.name}"
            )

    def _build_metadata_bytes(
        self,
        game: Path,
        entry: _ArchiveEntry,
        candidate_compressed_size: int,
        paz_checksum: int,
        paz_size: int,
        source_metadata: tuple[bytes, bytes] | None = None,
    ) -> tuple[bytes, bytes]:
        _paz_path, pamt_path, papgt_path = self._source_paths(game, entry)
        pamt_operation = (
            "metadata-build:" + pamt_path.relative_to(game).as_posix()
        )
        papgt_operation = (
            "metadata-build:" + papgt_path.relative_to(game).as_posix()
        )
        if source_metadata is None:
            source_metadata = (
                self._read_metadata_bytes(pamt_path, pamt_operation),
                self._read_metadata_bytes(papgt_path, papgt_operation),
            )
        source_pamt, source_papgt = source_metadata
        pamt = self._parse_pamt_bytes(source_pamt, pamt_operation)
        pamt_entry = self._find_entry_in_document(pamt)
        pamt_entry["compressed_size"] = candidate_compressed_size
        chunk_id = int(pamt_entry["chunk_id"])
        chunks = [chunk for chunk in pamt["chunks"] if int(chunk["id"]) == chunk_id]
        if len(chunks) != 1:
            raise ValueError(f"Expected one PAMT chunk {chunk_id}; found {len(chunks)}")
        chunks[0]["checksum"] = paz_checksum
        chunks[0]["size"] = paz_size
        pamt_bytes = bytearray(crimson_rs.serialize_pamt(pamt))
        struct.pack_into(
            "<I", pamt_bytes, 0, crimson_rs.calculate_checksum(bytes(pamt_bytes[12:]))
        )
        new_pamt = bytes(pamt_bytes)
        verified_pamt = self._parse_pamt_bytes(
            new_pamt,
            "metadata-build:candidate:0008/0.pamt",
        )
        pamt_checksum = int(verified_pamt["checksum"])

        papgt = self._parse_papgt_bytes(source_papgt, papgt_operation)
        groups = [
            item
            for item in papgt["entries"]
            if str(item["group_name"]) == self.profile.group_name
        ]
        if len(groups) != 1:
            raise ValueError(
                f"Expected one PAPGT group {self.profile.group_name}; found {len(groups)}"
            )
        groups[0]["pack_meta_checksum"] = pamt_checksum
        papgt_bytes = bytearray(crimson_rs.serialize_papgt(papgt))
        struct.pack_into(
            "<I", papgt_bytes, 4, crimson_rs.calculate_checksum(bytes(papgt_bytes[12:]))
        )
        new_papgt = bytes(papgt_bytes)
        self._parse_papgt_bytes(
            new_papgt,
            "metadata-build:candidate:meta/0.papgt",
        )
        return new_pamt, new_papgt

    def _mkdir_safe_archive_directory(
        self,
        game: Path,
        directory: Path,
        operation: str,
        *,
        exist_ok: bool,
    ) -> bool:
        self._assert_safe_archive_path(
            game,
            directory,
            operation,
            require_exists=False,
        )
        try:
            directory.mkdir()
        except FileExistsError:
            self._assert_safe_archive_path(
                game,
                directory,
                operation,
                require_exists=True,
            )
            if not exist_ok:
                raise
            if not directory.is_dir():
                raise NotADirectoryError(
                    f"Backup path component is not a directory: {directory}"
                )
            return False
        self._assert_safe_archive_path(
            game,
            directory,
            operation,
            require_exists=True,
        )
        return True

    @staticmethod
    def _open_windows_directory_guard(path: Path, operation: str) -> object:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(path),
            0x00010000 | 0x0080,  # DELETE | FILE_READ_ATTRIBUTES
            0x00000001 | 0x00000002,  # Share reads/writes, never deletion.
            None,
            3,  # OPEN_EXISTING
            0x02000000 | 0x00200000,
            None,
        )
        handle_value = (
            handle
            if isinstance(handle, int)
            else ctypes.cast(handle, ctypes.c_void_p).value
        )
        if handle_value in (None, ctypes.c_void_p(-1).value):
            error = ctypes.get_last_error()
            raise ctypes.WinError(
                error,
                f"Directory guard open failed ({operation}): {path}",
            )
        return handle

    @staticmethod
    def _close_windows_handle(handle: object, operation: str) -> None:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        if not close_handle(handle):
            error = ctypes.get_last_error()
            raise ctypes.WinError(
                error,
                f"Windows handle close failed ({operation})",
            )


    @contextmanager
    def _hold_safe_archive_directory_chain(
        self,
        root: Path,
        directory: Path,
        operation: str,
        *,
        create_missing: bool,
        root_is_held: bool = False,
        root_fd: int | None = None,
    ) -> Iterator[int | None]:
        root = root.resolve(strict=True)
        try:
            relative = directory.relative_to(root)
        except ValueError as exc:
            raise BackupConflictError(
                f"Archive directory escapes trusted game root during {operation}: "
                f"{directory}"
            ) from exc
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise BackupConflictError(
                f"Unsafe archive directory during {operation}: {directory}"
            )

        if os.name == "nt":
            handles: list[object] = []
            try:
                try:
                    current = root
                    parts = relative.parts if root_is_held else (None, *relative.parts)
                    for part in parts:
                        if part is not None:
                            current = current / part
                        try:
                            handle = self._open_windows_directory_guard(
                                current,
                                operation,
                            )
                        except FileNotFoundError:
                            if not create_missing or part is None:
                                raise
                            self._mkdir_safe_archive_directory(
                                root,
                                current,
                                f"{operation}:mkdir",
                                exist_ok=True,
                            )
                            handle = self._open_windows_directory_guard(
                                current,
                                operation,
                            )
                        handles.append(handle)
                        attributes = self._windows_path_attributes_no_follow(
                            current,
                            operation,
                        )
                        if attributes & 0x00000400:
                            raise BackupConflictError(
                                f"Archive descendant is a reparse point during "
                                f"{operation}: {current}"
                            )
                        if not attributes & 0x00000010:
                            raise BackupConflictError(
                                f"Archive path component is not a directory during "
                                f"{operation}: {current}"
                            )
                        self._assert_safe_archive_path(
                            root,
                            current,
                            operation,
                            require_exists=True,
                        )
                except OSError as exc:
                    raise BackupConflictError(
                        f"Archive directory guard failed during {operation}: {exc}"
                    ) from exc
                yield None
            finally:
                close_error: OSError | None = None
                for handle in reversed(handles):
                    try:
                        self._close_windows_handle(handle, operation)
                    except OSError as exc:
                        if close_error is None:
                            close_error = exc
                if close_error is not None:
                    raise close_error
            return

        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptors: list[int] = []
        try:
            try:
                import fcntl

                if root_is_held:
                    if root_fd is None:
                        raise ValueError("Held POSIX directory root requires its descriptor")
                    parent_fd = root_fd
                else:
                    parent_fd = os.open(root, flags)
                    descriptors.append(parent_fd)
                    fcntl.flock(parent_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                for part in relative.parts:
                    try:
                        child_fd = os.open(part, flags, dir_fd=parent_fd)
                    except FileNotFoundError:
                        if not create_missing:
                            raise
                        os.mkdir(part, dir_fd=parent_fd)
                        child_fd = os.open(part, flags, dir_fd=parent_fd)
                    if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                        os.close(child_fd)
                        raise NotADirectoryError(
                            f"Archive path component is not a directory: {part}"
                        )
                    fcntl.flock(child_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    descriptors.append(child_fd)
                    parent_fd = child_fd
            except OSError as exc:
                raise BackupConflictError(
                    f"Archive directory guard failed during {operation}: {exc}"
                ) from exc
            yield parent_fd
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @staticmethod
    def _before_backup_destination_open(
        destination: Path,
        operation: str,
    ) -> None:
        del destination, operation


    def _copy_backup_file(
        self,
        game: Path,
        source: Path,
        destination: Path,
        operation: str,
        *,
        held_backup_root: Path | None = None,
        held_backup_root_fd: int | None = None,
    ) -> None:
        guard_root = held_backup_root or game
        with self._hold_safe_archive_directory_chain(
            guard_root,
            destination.parent,
            f"{operation}:destination-chain",
            create_missing=True,
            root_is_held=held_backup_root is not None,
            root_fd=held_backup_root_fd,
        ) as parent_fd:
            self._assert_safe_archive_path(
                game,
                source,
                f"{operation}:source-open",
                require_exists=True,
            )
            self._assert_safe_archive_path(
                game,
                destination,
                f"{operation}:destination-open",
                require_exists=False,
            )
            self._before_backup_destination_open(destination, operation)
            with source.open("rb") as source_handle:
                if parent_fd is None:
                    destination_context = destination.open("xb")
                else:
                    descriptor = os.open(
                        destination.name,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o666,
                        dir_fd=parent_fd,
                    )
                    destination_context = os.fdopen(descriptor, "wb")
                with destination_context as backup_handle:
                    self._assert_safe_archive_path(
                        game,
                        destination,
                        f"{operation}:destination-write",
                        require_exists=True,
                    )
                    while True:
                        block = source_handle.read(1024 * 1024)
                        if not block:
                            break
                        self._write_all(backup_handle, block, operation)
                    self._flush_file(backup_handle, operation)
                    self._fsync_file(backup_handle, operation)
            self._sync_directory(
                destination.parent,
                f"backup-directory:{operation.removeprefix('backup:')}",
            )

    def _create_backup_root(self, game: Path, root: Path) -> None:
        current = game
        for part in root.relative_to(game).parts:
            current = current / part
            relative = current.relative_to(game).as_posix()
            created = self._mkdir_safe_archive_directory(
                game,
                current,
                f"backup-root-mkdir:{relative}",
                exist_ok=True,
            )
            if created:
                self._assert_safe_archive_path(
                    game,
                    current.parent,
                    f"backup-root-parent:{relative}",
                    require_exists=True,
                )
                self._sync_directory(
                    current.parent,
                    f"backup-root-parent:{relative}",
                )


    @staticmethod
    def _before_backup_set_step(phase: str, backup_dir: Path) -> None:
        del phase, backup_dir

    @staticmethod
    def _before_backup_guard_release(
        backup_dir: Path,
        source_hashes: dict[str, str],
    ) -> None:
        del backup_dir, source_hashes

    @staticmethod
    def _before_backup_apply_step(phase: str, backup_dir: Path) -> None:
        del phase, backup_dir

    def _assert_backup_set_complete(
        self,
        game: Path,
        backup: _HeldBackupSet,
        source_hashes: dict[str, str],
        operation: str,
    ) -> None:
        directory_stat = backup.path.stat()
        if (
            directory_stat.st_dev,
            directory_stat.st_ino,
        ) != backup.directory_identity:
            raise OSError(
                f"Backup timestamp identity changed during {operation}: "
                f"{backup.path}"
            )

        cached_hashes = {
            identity.relative_path: identity.sha256
            for identity in backup.files
        }
        if cached_hashes != source_hashes:
            raise OSError("Backup verification set is incomplete")

        manifest_path = backup.path / "manifest.json"
        self._assert_safe_archive_path(
            game,
            manifest_path,
            f"{operation}:manifest",
            require_exists=True,
        )
        if not manifest_path.is_file():
            raise OSError("Backup manifest disappeared while apply was active")

        for identity in backup.files:
            backup_file = backup.path / self._archive_relative_path(
                identity.relative_path
            )
            self._assert_safe_archive_path(
                game,
                backup_file,
                f"{operation}:{identity.relative_path}",
                require_exists=True,
            )
            current = backup_file.stat()
            fingerprint = (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
                current.st_ctime_ns,
            )
            expected_fingerprint = (
                identity.device,
                identity.inode,
                identity.size,
                identity.modified_ns,
                identity.changed_ns,
            )
            if (
                not stat.S_ISREG(current.st_mode)
                or fingerprint != expected_fingerprint
            ):
                raise OSError(
                    "Backup changed while apply was active: "
                    f"{identity.relative_path}"
                )

    def _create_backup(
        self,
        game: Path,
        paths: tuple[Path, Path, Path],
        source_hashes: dict[str, str],
        token: PreviewToken,
        slot_payload: bytes,
        guard_stack: ExitStack,
    ) -> tuple[_HeldBackupSet, _PazIdentity]:
        root = game / "bin64" / "SEModLoad" / "Backups" / "BlackstarTimer"
        self._create_backup_root(game, root)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        backup_dir = root / timestamp
        self._mkdir_safe_archive_directory(
            game,
            backup_dir,
            "backup-timestamp-mkdir",
            exist_ok=False,
        )
        self._assert_safe_archive_path(
            game,
            root,
            "backup-root-directory",
            require_exists=True,
        )
        self._sync_directory(root, "backup-root-directory")

        backup_root_fd = guard_stack.enter_context(
            self._hold_safe_archive_directory_chain(
                root,
                backup_dir,
                "backup-apply-lifetime",
                create_missing=False,
            )
        )
        directory_stat = backup_dir.stat()
        directory_identity = (
            directory_stat.st_dev,
            directory_stat.st_ino,
        )
        expected_paz: _PazIdentity | None = None
        verified_files: list[_BackupFileIdentity] = []
        for path in paths:
            relative = path.relative_to(game)
            relative_text = relative.as_posix()
            destination = backup_dir / relative
            operation = f"backup:{relative_text}"
            self._copy_backup_file(
                game,
                path,
                destination,
                operation,
                held_backup_root=backup_dir,
                held_backup_root_fd=backup_root_fd,
            )
            expected = source_hashes[relative_text]
            if path == paths[0]:
                expected_paz = self._stream_paz_identity(
                    destination,
                    token.entry_offset,
                    token.source_compressed_size,
                    replacement=slot_payload,
                    operation=f"backup-verification:{relative_text}",
                )
                backup_hash = expected_paz.source_sha256
            else:
                backup_hash = self._hash_file(
                    destination,
                    f"backup-verification:{relative_text}",
                )
            if backup_hash != expected:
                raise OSError(f"Backup verification failed: {relative_text}")
            current = destination.stat()
            verified_files.append(
                _BackupFileIdentity(
                    relative_path=relative_text,
                    sha256=backup_hash,
                    device=current.st_dev,
                    inode=current.st_ino,
                    size=current.st_size,
                    modified_ns=current.st_mtime_ns,
                    changed_ns=current.st_ctime_ns,
                )
            )
            if path == paths[0]:
                self._before_backup_set_step(
                    "after-paz-verification",
                    backup_dir,
                )

        self._before_backup_set_step("before-manifest", backup_dir)
        manifest = {
            "format_version": 1,
            "profile_id": self.profile.profile_id,
            "game_dir": str(game),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_hashes": source_hashes,
            "post_apply_hashes": {},
            "source_body_sha256": token.source_body_sha256,
            "candidate_body_sha256": token.candidate_body_sha256,
            "source_compressed_size": token.source_compressed_size,
            "candidate_compressed_size": token.candidate_compressed_size,
            "finalized": False,
            "rolled_back": False,
            "restored": False,
        }
        self._write_manifest(
            backup_dir / "manifest.json",
            manifest,
            trusted_root=game,
            held_directory_root=backup_dir,
            held_directory_root_fd=backup_root_fd,
        )
        if expected_paz is None:
            raise OSError("Backup PAZ identity was not calculated")

        backup = _HeldBackupSet(
            path=backup_dir,
            root_fd=backup_root_fd,
            directory_identity=directory_identity,
            files=tuple(verified_files),
        )
        self._assert_backup_set_complete(
            game,
            backup,
            source_hashes,
            "backup-set-created",
        )
        self._before_backup_guard_release(backup_dir, source_hashes)
        return backup, expected_paz

    def _verify_archive_set(
        self,
        game: Path,
        paths: tuple[Path, Path, Path],
        expected_hashes: dict[str, str],
        expected_status: TimerStatus,
        expected_body_hash: str,
    ) -> tuple[DetectionReport, dict[str, str]]:
        contained_paths: list[tuple[str, Path]] = []
        for path in paths:
            relative = path.resolve().relative_to(game).as_posix()
            contained_paths.append((relative, self._contained_path(game, relative)))

        paz_relative, paz_path = contained_paths[0]
        pamt_relative, pamt_path = contained_paths[1]
        papgt_relative, papgt_path = contained_paths[2]
        pamt_operation = f"post-write-verification:{pamt_relative}"
        papgt_operation = f"post-write-verification:{papgt_relative}"
        pamt_bytes = self._read_metadata_bytes(pamt_path, pamt_operation)
        papgt_bytes = self._read_metadata_bytes(papgt_path, papgt_operation)
        entry_mapping, chunk_checksum, chunk_size = self._verify_integrity_bytes(
            pamt_bytes,
            papgt_bytes,
            pamt_operation,
            papgt_operation,
        )
        entry = _ArchiveEntry.from_mapping(entry_mapping)
        expected_paz_path = self._source_paths(game, entry)[0].resolve()
        if paz_path.resolve() != expected_paz_path:
            raise ValueError("PAMT entry references an unexpected PAZ chunk")

        paz_identity = self._stream_paz_identity(
            paz_path,
            entry.chunk_offset,
            entry.compressed_size,
            operation=f"post-write-verification:{paz_relative}",
        )
        current_hashes = {
            paz_relative: paz_identity.sha256,
            pamt_relative: self._sha256_bytes(pamt_bytes),
            papgt_relative: self._sha256_bytes(papgt_bytes),
        }
        if current_hashes != expected_hashes:
            changed = sorted(
                key
                for key in current_hashes.keys() | expected_hashes.keys()
                if current_hashes.get(key) != expected_hashes.get(key)
            )
            relative = changed[0] if changed else "<archive set>"
            raise ValueError(f"Post-write hash mismatch: {relative}")
        if chunk_checksum != paz_identity.checksum:
            raise ValueError("PAZ chunk checksum mismatch")
        if chunk_size != paz_identity.size:
            raise ValueError("PAZ chunk size mismatch")
        if paz_identity.entry_bytes is None:
            raise ValueError("PAZ entry verification bytes are unavailable")

        body = self._decompress_entry(paz_identity.entry_bytes, entry)
        inspection = self._classify_inspection(game, entry, body, paths)
        report = inspection.report
        expected_values = {
            TimerStatus.VANILLA: (
                self.profile.vanilla_cooldown_seconds,
                self.profile.vanilla_duration_seconds,
            ),
            TimerStatus.APPLIED: (
                self.profile.preset_cooldown_seconds,
                self.profile.preset_duration_seconds,
            ),
        }
        if expected_status not in expected_values:
            raise ValueError(
                f"Unsupported verification status: {expected_status.value}"
            )
        cooldown, duration = expected_values[expected_status]
        if (
            report.status is not expected_status
            or report.body_sha256 != expected_body_hash
            or report.cooldown_seconds != cooldown
            or report.duration_seconds != duration
        ):
            raise ValueError(f"Post-write body verification failed: {report.reason}")
        return report, current_hashes

    def _verify_integrity_bytes(
        self,
        pamt_bytes: bytes,
        papgt_bytes: bytes,
        pamt_operation: str = "integrity:pamt",
        papgt_operation: str = "integrity:papgt",
    ) -> tuple[dict, int, int]:
        pamt = self._parse_pamt_bytes(pamt_bytes, pamt_operation)
        if int(pamt["checksum"]) != crimson_rs.calculate_checksum(pamt_bytes[12:]):
            raise ValueError("PAMT payload checksum mismatch")
        entry = self._find_entry_in_document(pamt)
        self._validate_entry(entry)
        chunk_id = int(entry["chunk_id"])
        chunks = [chunk for chunk in pamt["chunks"] if int(chunk["id"]) == chunk_id]
        if len(chunks) != 1:
            raise ValueError("PAMT chunk identity is ambiguous")

        papgt = self._parse_papgt_bytes(papgt_bytes, papgt_operation)
        if int(papgt["checksum"]) != crimson_rs.calculate_checksum(papgt_bytes[12:]):
            raise ValueError("PAPGT payload checksum mismatch")
        groups = [
            item
            for item in papgt["entries"]
            if str(item["group_name"]) == self.profile.group_name
        ]
        if len(groups) != 1 or int(groups[0]["pack_meta_checksum"]) != int(
            pamt["checksum"]
        ):
            raise ValueError("PAPGT does not reference the current PAMT checksum")
        return entry, int(chunks[0]["checksum"]), int(chunks[0]["size"])

    def _find_entry_in_document(self, pamt: dict) -> dict:
        matches = []
        expected_directory = self.profile.directory.strip("/").lower()
        expected_name = self.profile.file_name.lower()
        for directory in pamt.get("directories", []):
            if str(directory.get("path", "")).strip("/").lower() != expected_directory:
                continue
            matches.extend(
                entry
                for entry in directory.get("files", [])
                if str(entry.get("name", "")).lower() == expected_name
            )
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one {self.profile.archive_path} entry; found {len(matches)}"
            )
        return matches[0]

    def _restore_backup_files(
        self,
        game: Path,
        backup_dir: Path,
        source_hashes: dict[str, str],
        after_replace: Callable[[int, str], None] | None = None,
        *,
        preserve_paths: set[str] | None = None,
    ) -> None:
        preserved = preserve_paths or set()
        for index, (relative, expected) in enumerate(source_hashes.items(), start=1):
            if relative in preserved:
                continue
            source = self._contained_path(backup_dir, relative)
            destination = self._contained_path(game, relative)
            if not source.is_file() or self._hash_file(source) != expected:
                raise OSError(f"Backup cannot be verified: {relative}")
            if destination.is_file() and self._hash_file(destination) == expected:
                continue

            operation = f"rollback:{relative}"
            with self._hold_safe_archive_directory_chain(
                game,
                destination.parent,
                f"{operation}:destination-chain",
                create_missing=True,
            ) as parent_fd:
                if parent_fd is None:
                    descriptor, raw_path = tempfile.mkstemp(
                        prefix=f".{destination.name}.restore-",
                        dir=destination.parent,
                    )
                else:
                    candidate = (
                        f".{destination.name}.restore-"
                        + next(tempfile._get_candidate_names())
                    )
                    descriptor = os.open(
                        candidate,
                        os.O_RDWR
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=parent_fd,
                    )
                    raw_path = str(destination.parent / candidate)
                temp_path = Path(raw_path)
                try:
                    with source.open("rb") as source_handle:
                        with os.fdopen(descriptor, "wb") as temp_handle:
                            while True:
                                block = source_handle.read(1024 * 1024)
                                if not block:
                                    break
                                self._write_all(temp_handle, block, operation)
                            self._flush_file(temp_handle, operation)
                            shutil.copystat(source, temp_path)
                            self._fsync_file(temp_handle, operation)

                    if parent_fd is None:
                        self._replace_path(temp_path, destination, operation)
                    else:
                        self._replace_path(
                            temp_path,
                            destination,
                            operation,
                            directory_fd=parent_fd,
                        )
                    self._assert_safe_archive_path(
                        game,
                        destination,
                        f"{operation}:replaced-target",
                        require_exists=True,
                    )
                    self._sync_directory(
                        destination.parent,
                        f"rollback-directory:{relative}",
                    )
                except Exception:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                    temp_path.unlink(missing_ok=True)
                    raise
            if after_replace is not None:
                after_replace(index, relative)

        for relative, expected in source_hashes.items():
            if relative in preserved:
                if not self._contained_path(game, relative).is_file():
                    raise OSError(
                        f"Preserved concurrent file is missing: {relative}"
                    )
                continue
            if self._hash_file(self._contained_path(game, relative)) != expected:
                raise OSError(f"Restored file hash mismatch: {relative}")

    def _expected_archive_paths(self, game: Path) -> set[str]:
        try:
            entry = self._find_entry(game)
            paths = self._source_paths(game, entry)
            return {path.relative_to(game).as_posix() for path in paths}
        except Exception as exc:
            raise BackupConflictError(
                f"Could not resolve the enrolled timer archive paths: {exc}"
            ) from exc

    @staticmethod
    def _windows_path_attributes_no_follow(
        path: Path,
        operation: str,
    ) -> int:
        from ctypes import wintypes

        class FileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FileInformation),
        ]
        get_information.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = create_file(
            str(path),
            0x0080,  # FILE_READ_ATTRIBUTES
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,  # OPEN_EXISTING
            0x02000000 | 0x00200000,
            None,
        )
        handle_value = (
            handle
            if isinstance(handle, int)
            else ctypes.cast(handle, ctypes.c_void_p).value
        )
        if handle_value in (None, ctypes.c_void_p(-1).value):
            error = ctypes.get_last_error()
            raise ctypes.WinError(
                error,
                f"Containment handle open failed ({operation}): {path}",
            )

        information = FileInformation()
        information_error: OSError | None = None
        if not get_information(handle, ctypes.byref(information)):
            error = ctypes.get_last_error()
            information_error = ctypes.WinError(
                error,
                f"Containment handle query failed ({operation}): {path}",
            )

        close_error: OSError | None = None
        if not close_handle(handle):
            error = ctypes.get_last_error()
            close_error = ctypes.WinError(
                error,
                f"Containment handle close failed ({operation}): {path}",
            )

        if information_error is not None:
            if close_error is not None:
                raise information_error from close_error
            raise information_error
        if close_error is not None:
            raise close_error
        return int(information.dwFileAttributes)

    @classmethod
    def _assert_safe_archive_path(
        cls,
        root: Path,
        path: Path,
        operation: str,
        *,
        require_exists: bool,
    ) -> Path:
        try:
            root = root.resolve(strict=True)
        except OSError as exc:
            raise BackupConflictError(
                f"Trusted game root is unavailable during {operation}: {root}"
            ) from exc
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise BackupConflictError(
                f"Archive path escapes trusted game root during {operation}: {path}"
            ) from exc
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise BackupConflictError(
                f"Unsafe archive path during {operation}: {path}"
            )

        current = root
        for part in relative.parts:
            current = current / part
            try:
                current.lstat()
            except FileNotFoundError as exc:
                if require_exists:
                    raise BackupConflictError(
                        f"Archive path disappeared during {operation}: {current}"
                    ) from exc
                break
            except OSError as exc:
                raise BackupConflictError(
                    f"Archive path cannot be inspected during {operation}: {current}"
                ) from exc

            if os.name == "nt":
                try:
                    attributes = cls._windows_path_attributes_no_follow(
                        current,
                        operation,
                    )
                except OSError as exc:
                    raise BackupConflictError(
                        f"Archive path cannot be inspected during {operation}: "
                        f"{current}: {exc}"
                    ) from exc
                if attributes & 0x00000400:
                    raise BackupConflictError(
                        f"Archive descendant is a reparse point during {operation}: "
                        f"{current}"
                    )

            try:
                resolved_component = current.resolve(strict=True)
                resolved_component.relative_to(root)
            except (OSError, ValueError) as exc:
                raise BackupConflictError(
                    f"Archive path escapes trusted game root during {operation}: "
                    f"{current}"
                ) from exc

        try:
            resolved = path.resolve(strict=require_exists)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise BackupConflictError(
                f"Archive path escapes trusted game root during {operation}: {path}"
            ) from exc
        return path

    @staticmethod
    def _archive_relative_path(relative: str) -> Path:
        if "\\" in relative:
            raise BackupConflictError(f"Unsafe backup path: {relative}")
        parts = relative.split("/")
        if (
            not parts
            or any(part in {"", ".", ".."} or ":" in part for part in parts)
            or relative.startswith("/")
        ):
            raise BackupConflictError(f"Unsafe backup path: {relative}")
        return Path(*parts)

    @classmethod
    def _contained_path(cls, root: Path, relative: str) -> Path:
        relative_path = cls._archive_relative_path(relative)
        root = root.resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise BackupConflictError(f"Unsafe backup path: {relative}") from exc
        return candidate

    @staticmethod
    def _replace_windows_file(
        source: Path,
        destination: Path,
        operation: str,
        backup_path: Path | None = None,
    ) -> None:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        replace_file = kernel32.ReplaceFileW
        replace_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        replace_file.restype = wintypes.BOOL
        if not replace_file(
            str(destination),
            str(source),
            str(backup_path) if backup_path is not None else None,
            0,
            None,
            None,
        ):
            error = ctypes.get_last_error()
            raise ctypes.WinError(
                error,
                f"Atomic replace failed ({operation}): {destination}",
            )


    def _restore_displaced_metadata(
        self,
        displaced: Path,
        destination: Path,
        operation: str,
    ) -> None:
        if os.name == "nt":
            self._replace_windows_file(
                displaced,
                destination,
                f"{operation}:restore",
            )
        else:
            os.replace(displaced, destination)
        with destination.open("rb+") as restored_handle:
            self._fsync_file(
                restored_handle,
                f"{operation}:restored-file",
            )
        self._sync_directory(
            destination.parent,
            f"{operation}:restored-directory",
        )

    def _exchange_metadata_preimage(
        self,
        source: Path,
        destination: Path,
        expected: bytes,
        expected_identity: tuple[int, int],
        operation: str,
        *,
        directory_fd: int | None,
    ) -> Path:
        displaced = self._metadata_displaced_path(destination, operation)
        if os.name == "nt":
            self._replace_windows_file(
                source,
                destination,
                operation,
                displaced,
            )
        else:
            # The hard link is an atomic named preimage under the held parent.
            # POSIX locks remain advisory against in-place noncooperative writers.
            if directory_fd is None:
                os.link(destination, displaced, follow_symlinks=False)
            else:
                os.link(
                    destination.name,
                    displaced.name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            self._replace_path(
                source,
                destination,
                operation,
                directory_fd=directory_fd,
            )

        verification_operation = (
            f"metadata-displaced-verification:{operation}"
        )
        try:
            displaced_bytes = self._read_metadata_bytes(
                displaced,
                verification_operation,
            )
            displaced_stat = displaced.stat()
        except Exception as verification_exc:
            raise _MetadataRecoveryRequired(
                destination,
                displaced,
                verification_exc,
            ) from verification_exc

        displaced_identity = (
            displaced_stat.st_dev,
            displaced_stat.st_ino,
        )
        if (
            displaced_bytes != expected
            or displaced_identity != expected_identity
        ):
            raise _MetadataRecoveryRequired(
                destination,
                displaced,
                StalePreviewError(
                    f"Metadata changed before atomic exchange: {destination}"
                ),
                concurrent_bytes=displaced_bytes,
            )
        return displaced

    def _replace_path(
        self,
        source: Path,
        destination: Path,
        operation: str,
        *,
        directory_fd: int | None = None,
    ) -> None:
        if directory_fd is not None:
            os.replace(
                source.name,
                destination.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            return
        if os.name == "nt" and destination.exists():
            self._replace_windows_file(source, destination, operation)
            return
        os.replace(source, destination)

    @staticmethod
    def _write_all(
        handle: BinaryIO,
        data: bytes,
        operation: str,
    ) -> None:
        del operation
        view = memoryview(data)
        written_total = 0
        while written_total < len(view):
            written = handle.write(view[written_total:])
            if written is None or written <= 0:
                raise OSError("Archive write did not make progress")
            written_total += written

    @staticmethod
    def _flush_file(handle: BinaryIO, operation: str) -> None:
        del operation
        handle.flush()

    @staticmethod
    def _fsync_file(handle: BinaryIO, operation: str) -> None:
        del operation
        os.fsync(handle.fileno())

    @staticmethod
    def _sync_directory(path: Path, operation: str) -> None:
        if os.name == "nt":
            BlackstarTimerService._sync_windows_directory(path, operation)
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _sync_windows_directory(path: Path, operation: str) -> None:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        flush_file_buffers = kernel32.FlushFileBuffers
        flush_file_buffers.argtypes = [wintypes.HANDLE]
        flush_file_buffers.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = create_file(
            str(path),
            0x0002,  # FILE_WRITE_DATA
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
            None,
        )
        handle_value = (
            handle
            if isinstance(handle, int)
            else ctypes.cast(handle, ctypes.c_void_p).value
        )
        if handle_value in (None, ctypes.c_void_p(-1).value):
            error = ctypes.get_last_error()
            raise ctypes.WinError(
                error,
                f"Directory sync open failed ({operation}): {path}",
            )

        flush_error: OSError | None = None
        if not flush_file_buffers(handle):
            error = ctypes.get_last_error()
            flush_error = ctypes.WinError(
                error,
                f"Directory sync flush failed ({operation}): {path}",
            )

        close_error: OSError | None = None
        if not close_handle(handle):
            error = ctypes.get_last_error()
            close_error = ctypes.WinError(
                error,
                f"Directory sync close failed ({operation}): {path}",
            )

        if flush_error is not None:
            if close_error is not None:
                raise flush_error from close_error
            raise flush_error
        if close_error is not None:
            raise close_error


    def _atomic_write(
        self,
        path: Path,
        data: bytes,
        operation: str | None = None,
        *,
        trusted_root: Path | None = None,
        expected_current: bytes | None = None,
        metadata_guard_operation: str | None = None,
        held_directory_root: Path | None = None,
        held_directory_root_fd: int | None = None,
    ) -> None:
        operation = operation or f"atomic:{path.name}"
        if (expected_current is None) != (metadata_guard_operation is None):
            raise ValueError(
                "Metadata replacement requires both expected bytes and a guard label"
            )
        if trusted_root is not None:
            self._assert_safe_archive_path(
                trusted_root,
                path.parent,
                f"{operation}:temp-parent",
                require_exists=True,
            )
            self._assert_safe_archive_path(
                trusted_root,
                path,
                f"{operation}:target",
                require_exists=False,
            )

        if held_directory_root is not None:
            if path.parent == held_directory_root:
                directory_guard = nullcontext(held_directory_root_fd)
            else:
                directory_guard = self._hold_safe_archive_directory_chain(
                    held_directory_root,
                    path.parent,
                    f"{operation}:temp-parent-chain",
                    create_missing=False,
                    root_is_held=True,
                    root_fd=held_directory_root_fd,
                )
        elif trusted_root is not None:
            directory_guard = self._hold_safe_archive_directory_chain(
                trusted_root,
                path.parent,
                f"{operation}:temp-parent-chain",
                create_missing=False,
            )
        else:
            directory_guard = nullcontext(None)
        with directory_guard as parent_fd:
            if parent_fd is None:
                descriptor, raw_path = tempfile.mkstemp(
                    prefix=f".{path.name}.timer-",
                    dir=path.parent,
                )
            else:
                candidate = (
                    f".{path.name}.timer-"
                    + next(tempfile._get_candidate_names())
                )
                descriptor = os.open(
                    candidate,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                raw_path = str(path.parent / candidate)
            temp_path = Path(raw_path)
            try:
                if trusted_root is not None:
                    self._assert_safe_archive_path(
                        trusted_root,
                        temp_path,
                        f"{operation}:temp-write",
                        require_exists=True,
                    )
                with os.fdopen(descriptor, "wb") as handle:
                    if trusted_root is not None:
                        self._assert_safe_archive_path(
                            trusted_root,
                            temp_path,
                            f"{operation}:temp-write",
                            require_exists=True,
                        )
                    self._write_all(handle, data, operation)
                    self._flush_file(handle, operation)
                    self._fsync_file(handle, operation)

                metadata_guard = (
                    self._hold_metadata_replace_guard(
                        path,
                        expected_current,
                        metadata_guard_operation,
                    )
                    if expected_current is not None
                    and metadata_guard_operation is not None
                    else nullcontext(None)
                )
                directory_operation = (
                    "manifest-directory"
                    if operation == "manifest"
                    else f"{operation}-directory"
                )
                recovery_required: _MetadataRecoveryRequired | None = None
                with metadata_guard as expected_identity:
                    if trusted_root is not None:
                        self._assert_safe_archive_path(
                            trusted_root,
                            temp_path,
                            f"{operation}:replace-source",
                            require_exists=True,
                        )
                        self._assert_safe_archive_path(
                            trusted_root,
                            path,
                            f"{operation}:replace-target",
                            require_exists=(
                                expected_current is not None or path.exists()
                            ),
                        )

                    if expected_current is not None:
                        if expected_identity is None:
                            raise RuntimeError(
                                "Metadata exchange guard did not retain an identity"
                            )
                        self._before_metadata_exchange(path, operation)
                        try:
                            displaced_path = self._exchange_metadata_preimage(
                                temp_path,
                                path,
                                expected_current,
                                expected_identity,
                                operation,
                                directory_fd=parent_fd,
                            )
                        except _MetadataRecoveryRequired as recovery_exc:
                            recovery_required = recovery_exc
                        else:
                            try:
                                if trusted_root is not None:
                                    self._assert_safe_archive_path(
                                        trusted_root,
                                        path,
                                        f"{operation}:replaced-target",
                                        require_exists=True,
                                    )
                                with path.open("rb+") as replaced_handle:
                                    self._fsync_file(
                                        replaced_handle,
                                        f"{operation}:replaced-file",
                                    )
                                if trusted_root is not None:
                                    self._assert_safe_archive_path(
                                        trusted_root,
                                        path.parent,
                                        directory_operation,
                                        require_exists=True,
                                    )
                                self._sync_directory(
                                    path.parent,
                                    directory_operation,
                                )
                                self._remove_displaced_metadata(
                                    displaced_path,
                                    operation,
                                )
                                self._sync_directory(
                                    path.parent,
                                    f"{operation}-displaced-directory",
                                )
                            except Exception as exchange_completion_exc:
                                if not displaced_path.exists():
                                    raise
                                recovery_required = _MetadataRecoveryRequired(
                                    path,
                                    displaced_path,
                                    exchange_completion_exc,
                                )
                    else:
                        if parent_fd is None:
                            self._replace_path(temp_path, path, operation)
                        else:
                            self._replace_path(
                                temp_path,
                                path,
                                operation,
                                directory_fd=parent_fd,
                            )
                        if trusted_root is not None:
                            self._assert_safe_archive_path(
                                trusted_root,
                                path,
                                f"{operation}:replaced-target",
                                require_exists=True,
                            )
                            self._assert_safe_archive_path(
                                trusted_root,
                                path.parent,
                                directory_operation,
                                require_exists=True,
                            )
                        self._sync_directory(path.parent, directory_operation)

                if recovery_required is not None:
                    try:
                        self._restore_displaced_metadata(
                            recovery_required.displaced_path,
                            recovery_required.path,
                            operation,
                        )
                    except Exception as restore_exc:
                        raise _MetadataExchangeConflict(
                            recovery_required.path,
                            recovery_required.displaced_path,
                            restored=False,
                            detail=(
                                f"{path.name}; displaced restore failed: "
                                f"{restore_exc}"
                            ),
                        ) from restore_exc

                    concurrent_bytes = recovery_required.concurrent_bytes
                    if concurrent_bytes is not None:
                        try:
                            restored_bytes = self._read_metadata_bytes(
                                recovery_required.path,
                                f"metadata-concurrent-restored:{operation}",
                            )
                        except Exception as restored_read_exc:
                            raise _MetadataExchangeConflict(
                                recovery_required.path,
                                None,
                                restored=True,
                                detail=(
                                    f"{path.name}; restored preimage could not "
                                    f"be verified: {restored_read_exc}"
                                ),
                            ) from restored_read_exc
                        if restored_bytes != concurrent_bytes:
                            raise _MetadataExchangeConflict(
                                recovery_required.path,
                                None,
                                restored=True,
                                detail=f"{path.name}; restored preimage changed",
                            )
                        raise _MetadataExchangeConflict(
                            recovery_required.path,
                            None,
                            restored=True,
                            detail=f"{path.name}; concurrent preimage preserved",
                        )
                    raise recovery_required.original
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                temp_path.unlink(missing_ok=True)
                raise

    def _write_manifest(
        self,
        path: Path,
        manifest: dict,
        *,
        trusted_root: Path | None = None,
        held_directory_root: Path | None = None,
        held_directory_root_fd: int | None = None,
    ) -> None:
        payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        self._atomic_write(
            path,
            payload,
            "manifest",
            trusted_root=trusted_root,
            held_directory_root=held_directory_root,
            held_directory_root_fd=held_directory_root_fd,
        )

    def _read_manifest(
        self,
        path: Path,
        operation: str | None = None,
    ) -> dict:
        del operation
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupConflictError(f"Backup manifest is unreadable: {exc}") from exc
        if not isinstance(value, dict):
            raise BackupConflictError("Backup manifest root is not an object")
        return value

    @staticmethod
    def _manifest_hashes(manifest: dict, key: str) -> dict[str, str]:
        values = manifest.get(key)
        if not isinstance(values, dict) or not values:
            raise BackupConflictError(f"Backup manifest has no {key}")
        if not all(isinstance(path, str) and isinstance(value, str) for path, value in values.items()):
            raise BackupConflictError(f"Backup manifest {key} is invalid")
        return dict(values)

    def _latest_finalized_backup(self, game: Path) -> Path | None:
        root = game / "bin64" / "SEModLoad" / "Backups" / "BlackstarTimer"
        if not root.is_dir():
            return None
        for candidate in sorted(
            (path for path in root.iterdir() if path.is_dir()), reverse=True
        ):
            manifest_path = candidate / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = self._read_manifest(manifest_path)
            except BackupConflictError:
                continue
            if (
                manifest.get("profile_id") == self.profile.profile_id
                and manifest.get("finalized") is True
                and manifest.get("restored") is not True
            ):
                return candidate
        return None

    def _inject_fault(self, phase: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(phase)

    def _emit_progress(
        self,
        callback: Callable[[str, int], None] | None,
        phase: str,
        value: int,
    ) -> None:
        log.info(
            "blackstar_timer profile=%s phase=%s progress=%d",
            self.profile.profile_id,
            phase,
            value,
        )
        if callback is not None:
            callback(phase, value)

    def _process_report(self, game: Path) -> DetectionReport | None:
        try:
            if self._process_checker():
                return self._report(
                    TimerStatus.GAME_RUNNING,
                    game,
                    "Crimson Desert is running; close it before previewing or writing",
                )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._report(
                TimerStatus.UNKNOWN,
                game,
                f"Could not verify whether Crimson Desert is running: {exc}",
            )
        return None

    def _ensure_game_closed(self) -> None:
        try:
            running = self._process_checker()
        except (OSError, subprocess.SubprocessError) as exc:
            raise GameRunningError(
                f"Could not verify whether Crimson Desert is running: {exc}"
            ) from exc
        if running:
            raise GameRunningError(
                "Crimson Desert is running; close it before changing game archives"
            )

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _inspect(self, game: Path) -> _ArchiveInspection:
        try:
            source_entry = dict(self._find_entry(game))
            self._validate_entry(source_entry)
            body = self._read_body(game, source_entry)
            paths = self._source_paths(game, source_entry)
            entry = _ArchiveEntry.from_mapping(source_entry)
        except (FileNotFoundError, OSError, ValueError, KeyError, TypeError) as exc:
            raise _ArchiveSourceError(str(exc)) from exc
        return self._classify_inspection(game, entry, body, paths)

    def _classify_inspection(
        self,
        game: Path,
        entry: _ArchiveEntry,
        body: bytes,
        paths: tuple[Path, Path, Path],
    ) -> _ArchiveInspection:
        digest = hashlib.sha256(body).hexdigest()
        cooldown = self._read_u64(body, self.profile.cooldown_offset)
        duration = self._read_u64(body, self.profile.duration_offset)
        values = (cooldown, duration)
        vanilla_values = (
            self.profile.vanilla_cooldown_seconds,
            self.profile.vanilla_duration_seconds,
        )
        applied_values = (
            self.profile.preset_cooldown_seconds,
            self.profile.preset_duration_seconds,
        )
        details = {
            "body_sha256": digest,
            "cooldown_seconds": cooldown,
            "duration_seconds": duration,
            "entry_offset": entry.chunk_offset,
            "compressed_size": entry.compressed_size,
            "uncompressed_size": entry.uncompressed_size,
        }
        if (
            digest == self.profile.vanilla_body_sha256
            and values == vanilla_values
            and entry.compressed_size == self.profile.vanilla_compressed_size
        ):
            report = self._report(
                TimerStatus.VANILLA,
                game,
                "Enrolled vanilla Blackstar timer schema detected",
                **details,
            )
        elif digest == self.profile.applied_body_sha256 and values == applied_values:
            report = self._report(
                TimerStatus.APPLIED,
                game,
                "Verified Blackstar timer preset is already applied",
                **details,
            )
        elif values[0] in (vanilla_values[0], applied_values[0]) and values[1] in (
            vanilla_values[1],
            applied_values[1],
        ) and values not in (vanilla_values, applied_values):
            report = self._report(
                TimerStatus.PARTIAL,
                game,
                "Blackstar timer fields are only partially patched",
                **details,
            )
        elif values not in (vanilla_values, applied_values):
            report = self._report(
                TimerStatus.PARTIAL,
                game,
                "Blackstar timer field values do not match an enrolled state",
                **details,
            )
        else:
            report = self._report(
                TimerStatus.UNKNOWN,
                game,
                "Characterinfo body hash is not enrolled for this game version",
                **details,
            )
        return _ArchiveInspection(
            report=report,
            entry=entry,
            body=body,
            paths=paths,
        )

    def _build_candidate(self, source: bytes) -> bytes:
        candidate = bytearray(source)
        cooldown_end = self.profile.cooldown_offset + 8
        duration_end = self.profile.duration_offset + 8
        if min(self.profile.cooldown_offset, self.profile.duration_offset) < 0 or max(
            cooldown_end, duration_end
        ) > len(candidate):
            raise ValueError("Enrolled timer offsets are outside characterinfo")
        candidate[self.profile.cooldown_offset:cooldown_end] = (
            self.profile.preset_cooldown_seconds.to_bytes(8, "little")
        )
        candidate[self.profile.duration_offset:duration_end] = (
            self.profile.preset_duration_seconds.to_bytes(8, "little")
        )
        return bytes(candidate)

    def _source_paths(
        self,
        game: Path,
        entry: _ArchiveEntry | Mapping[str, object],
    ) -> tuple[Path, Path, Path]:
        group = game / self.profile.group_name
        chunk_id = (
            entry.chunk_id
            if isinstance(entry, _ArchiveEntry)
            else int(entry["chunk_id"])
        )
        return (
            group / f"{chunk_id}.paz",
            group / "0.pamt",
            game / "meta" / "0.papgt",
        )

    @staticmethod
    def _hash_file(path: Path, operation: str | None = None) -> str:
        del operation
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _find_entry(self, game: Path) -> dict:
        relative = f"{self.profile.group_name}/0.pamt"
        pamt_path = game / Path(relative)
        if not pamt_path.is_file():
            raise FileNotFoundError(f"PAMT not found: {pamt_path}")
        operation = f"source-inspection:{relative}"
        pamt_bytes = self._read_metadata_bytes(pamt_path, operation)
        pamt = self._parse_pamt_bytes(pamt_bytes, operation)
        return self._find_entry_in_document(pamt)

    def _validate_entry(self, entry: dict) -> None:
        checks = {
            "entry offset": (int(entry["chunk_offset"]), self.profile.entry_offset),
            "uncompressed size": (
                int(entry["uncompressed_size"]),
                self.profile.uncompressed_size,
            ),
            "compression": (int(entry["compression"]), self.profile.compression),
            "crypto": (int(entry["crypto"]), self.profile.crypto),
        }
        for label, (actual, expected) in checks.items():
            if actual != expected:
                raise ValueError(f"Unexpected {label}: {actual}; expected {expected}")

    def _read_body(self, game: Path, entry: dict) -> bytes:
        archive_entry = _ArchiveEntry.from_mapping(entry)
        paz_path = self._source_paths(game, archive_entry)[0]
        if not paz_path.is_file():
            raise FileNotFoundError(f"PAZ not found: {paz_path}")
        offset = archive_entry.chunk_offset
        compressed_size = archive_entry.compressed_size
        relative = paz_path.relative_to(game).as_posix()
        compressed = self._read_paz_entry(
            paz_path,
            offset,
            compressed_size,
            f"source-inspection:{relative}-entry",
        )
        return self._decompress_entry(compressed, archive_entry)

    @staticmethod
    def _read_paz_entry(
        path: Path,
        offset: int,
        size: int,
        operation: str,
    ) -> bytes:
        del operation
        with path.open("rb") as handle:
            handle.seek(offset)
            return handle.read(size)

    def _decompress_entry(
        self,
        compressed: bytes,
        entry: _ArchiveEntry,
    ) -> bytes:
        if len(compressed) != entry.compressed_size:
            raise ValueError(
                "Compressed entry is truncated: "
                f"{len(compressed)} of {entry.compressed_size} bytes"
            )
        body = self._decompress_stream(
            compressed,
            entry.compression,
            entry.uncompressed_size,
        )
        if len(body) != self.profile.uncompressed_size:
            raise ValueError(
                f"Decompressed body size is {len(body)}; "
                f"expected {self.profile.uncompressed_size}"
            )
        return body

    @staticmethod
    def _decompress_stream(
        compressed: bytes,
        compression: int,
        uncompressed_size: int,
    ) -> bytes:
        return bytes(
            crimson_rs.decompress_data(
                compressed,
                compression,
                uncompressed_size,
            )
        )

    @staticmethod
    def _read_u64(body: bytes, offset: int) -> int:
        end = offset + 8
        if offset < 0 or end > len(body):
            raise ValueError(f"Timer field offset {offset} is outside the body")
        return int.from_bytes(body[offset:end], "little", signed=False)

    def _report(
        self,
        status: TimerStatus,
        game: Path,
        reason: str,
        **details: object,
    ) -> DetectionReport:
        return DetectionReport(
            status=status,
            reason=reason,
            profile_id=self.profile.profile_id,
            game_dir=game,
            **details,
        )
