from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from crimson.game_mods.dragon_wheel_patch import DragonWheelPatchResult


INTERNAL_DIR = "gamedata/binary__/client/bin"
MARKER_NAME = ".se_dragon_wheel"
DRAGON_OVERLAY_GROUP = "0067"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeploymentReceipt:
    overlay_group: str
    backup_dir: Path
    papgt_groups_before: tuple[str, ...]
    papgt_groups_after: tuple[str, ...]
    candidate_pabgh_sha256: str
    candidate_pabgb_sha256: str


class DragonWheelDeploymentError(RuntimeError):
    pass


def deploy_dragon_wheel(
    game_path: str | Path,
    overlay_group: str,
    patch_result: DragonWheelPatchResult,
    backup_root: str | Path,
    crimson_rs_module: ModuleType | Any,
    timestamp: str | None = None,
    coordinator_module: ModuleType | Any | None = None,
) -> DeploymentReceipt:
    """Deploy the verified Dragon-only candidate as an owned overlay.

    The PAZ is fully built before the game tree changes. PAPGT and an existing
    owned overlay are backed up, and every live mutation has an inverse rollback.
    """
    game = Path(game_path)
    group = _validate_group(overlay_group)
    _validate_candidate(patch_result)
    papgt_path = game / "meta" / "0.papgt"
    live_overlay = game / group
    marker = live_overlay / MARKER_NAME

    if not game.is_dir():
        raise DragonWheelDeploymentError(f"Game directory does not exist: {game}")
    if not papgt_path.is_file():
        raise DragonWheelDeploymentError("meta/0.papgt is missing")
    if live_overlay.exists() and not marker.is_file():
        raise DragonWheelDeploymentError(
            f"Overlay {group} exists and is not owned by Dragon Wheel"
        )

    coordinator = _get_coordinator(coordinator_module)
    safe, reason = coordinator.pre_write(str(game), group, owner="CrimsonGameMods")
    if not safe:
        raise DragonWheelDeploymentError(reason)

    stamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(backup_root) / stamp
    if backup_dir.exists():
        raise DragonWheelDeploymentError(f"Backup already exists: {backup_dir}")

    original_overlay_existed = live_overlay.exists()
    papgt_temp = papgt_path.with_name(
        f".0.papgt.dragon-wheel-{uuid.uuid4().hex}.tmp"
    )
    staged_live = game / f".{group}.dragon-wheel-stage-{uuid.uuid4().hex}"
    rollback_overlay = game / f".{group}.dragon-wheel-rollback-{uuid.uuid4().hex}"
    live_replaced = False
    papgt_replaced = False
    before_groups: tuple[str, ...] = ()
    after_groups: tuple[str, ...] = ()

    try:
        with tempfile.TemporaryDirectory(prefix="dragon-wheel-build-") as temp_dir:
            build_overlay = Path(temp_dir) / group
            builder = crimson_rs_module.PackGroupBuilder(
                str(build_overlay),
                crimson_rs_module.Compression.NONE,
                crimson_rs_module.Crypto.NONE,
            )
            builder.add_file(INTERNAL_DIR, "reserveslot.pabgb", patch_result.pabgb)
            builder.add_file(INTERNAL_DIR, "reserveslot.pabgh", patch_result.pabgh)
            pamt_bytes = bytes(builder.finish())
            checksum = crimson_rs_module.parse_pamt_bytes(pamt_bytes)["checksum"]
            if not (build_overlay / "0.paz").is_file():
                raise DragonWheelDeploymentError("PAZ builder did not produce 0.paz")
            if not (build_overlay / "0.pamt").is_file():
                raise DragonWheelDeploymentError("PAZ builder did not produce 0.pamt")
            log.info(
                "Dragon Wheel: built candidate overlay %s with PAMT checksum %s",
                group,
                checksum,
            )
            (build_overlay / MARKER_NAME).write_text(
                json.dumps(
                    {
                        "schema_id": patch_result.report.schema_id,
                        "pabgh_sha256": patch_result.report.output_pabgh_sha256,
                        "pabgb_sha256": patch_result.report.output_pabgb_sha256,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            backup_dir.mkdir(parents=True)
            shutil.copy2(papgt_path, backup_dir / "0.papgt")
            if original_overlay_existed:
                shutil.copytree(live_overlay, backup_dir / group)
            log.info(
                "Dragon Wheel: backed up PAPGT%s to %s",
                " and the prior owned overlay" if original_overlay_existed else "",
                backup_dir,
            )

            before_doc = crimson_rs_module.parse_papgt_file(str(papgt_path))
            before_entries = list(before_doc.get("entries", []))
            before_groups = tuple(_entry_group(entry) for entry in before_entries)
            foreign_entries = [
                entry for entry in before_entries if _entry_group(entry) != group
            ]
            next_doc = dict(before_doc)
            next_doc["entries"] = foreign_entries
            next_doc = crimson_rs_module.add_papgt_entry(
                next_doc,
                group,
                checksum,
                is_optional=0,
                language=0x3FFF,
            )
            crimson_rs_module.write_papgt_file(next_doc, str(papgt_temp))
            verified_doc = crimson_rs_module.parse_papgt_file(str(papgt_temp))
            verified_entries = list(verified_doc.get("entries", []))
            after_groups = tuple(_entry_group(entry) for entry in verified_entries)
            if after_groups.count(group) != 1:
                raise DragonWheelDeploymentError(
                    "Temporary PAPGT does not contain exactly one Dragon Wheel entry"
                )
            verified_foreign = [
                entry for entry in verified_entries if _entry_group(entry) != group
            ]
            if _normalized_entries(verified_foreign) != _normalized_entries(
                foreign_entries
            ):
                raise DragonWheelDeploymentError(
                    "Temporary PAPGT did not preserve every foreign entry"
                )
            log.info(
                "Dragon Wheel: verified temporary PAPGT with %d preserved entries",
                len(verified_foreign),
            )

            shutil.copytree(build_overlay, staged_live)

        if original_overlay_existed:
            os.replace(live_overlay, rollback_overlay)
        os.replace(staged_live, live_overlay)
        live_replaced = True
        os.replace(papgt_temp, papgt_path)
        papgt_replaced = True
        log.info("Dragon Wheel: atomically installed overlay %s and PAPGT", group)

        coordinator.post_write(
            str(game),
            group,
            "Dragon Wheel",
            ["reserveslot.pabgb", "reserveslot.pabgh"],
            owner="CrimsonGameMods",
        )
        if rollback_overlay.exists():
            try:
                shutil.rmtree(rollback_overlay)
            except OSError as exc:
                log.warning(
                    "Could not remove Dragon Wheel rollback directory after commit: %s",
                    exc,
                )

        return DeploymentReceipt(
            overlay_group=group,
            backup_dir=backup_dir,
            papgt_groups_before=before_groups,
            papgt_groups_after=after_groups,
            candidate_pabgh_sha256=patch_result.report.output_pabgh_sha256,
            candidate_pabgb_sha256=patch_result.report.output_pabgb_sha256,
        )
    except Exception as exc:
        rollback_errors = _rollback_deployment(
            papgt_temp=papgt_temp,
            staged_live=staged_live,
            papgt_replaced=papgt_replaced,
            papgt_backup=backup_dir / "0.papgt",
            papgt_path=papgt_path,
            live_replaced=live_replaced,
            live_overlay=live_overlay,
            rollback_overlay=rollback_overlay,
        )
        if rollback_errors:
            detail = "; rollback incomplete: " + "; ".join(rollback_errors)
            log.error("Dragon Wheel deployment rollback incomplete: %s", detail)
        else:
            detail = "; rolled back"
            log.warning("Dragon Wheel: deployment failed and was rolled back: %s", exc)
        if isinstance(exc, DragonWheelDeploymentError):
            raise DragonWheelDeploymentError(f"{exc}{detail}") from exc
        raise DragonWheelDeploymentError(
            f"Deployment failed: {exc}{detail}"
        ) from exc


def restore_dragon_wheel(
    game_path: str | Path,
    overlay_group: str,
    crimson_rs_module: ModuleType | Any,
    coordinator_module: ModuleType | Any | None = None,
) -> tuple[str, ...]:
    """Remove only an overlay carrying this feature's ownership marker."""
    game = Path(game_path)
    group = _validate_group(overlay_group)
    overlay = game / group
    marker = overlay / MARKER_NAME
    papgt_path = game / "meta" / "0.papgt"
    if not marker.is_file():
        raise DragonWheelDeploymentError("Dragon Wheel ownership marker is missing")
    if not papgt_path.is_file():
        raise DragonWheelDeploymentError("meta/0.papgt is missing")

    coordinator = _get_coordinator(coordinator_module)
    safe, reason = coordinator.pre_restore(
        str(game), group, owner="CrimsonGameMods"
    )
    if not safe:
        raise DragonWheelDeploymentError(reason)

    original_papgt = papgt_path.read_bytes()
    papgt_temp = papgt_path.with_name(
        f".0.papgt.dragon-wheel-{uuid.uuid4().hex}.tmp"
    )
    rollback_overlay = game / f".{group}.dragon-wheel-rollback-{uuid.uuid4().hex}"
    overlay_moved = False
    papgt_replaced = False
    try:
        document = crimson_rs_module.parse_papgt_file(str(papgt_path))
        before_entries = list(document.get("entries", []))
        remaining_entries = [
            entry for entry in before_entries if _entry_group(entry) != group
        ]
        document = dict(document)
        document["entries"] = remaining_entries
        crimson_rs_module.write_papgt_file(document, str(papgt_temp))
        verified = crimson_rs_module.parse_papgt_file(str(papgt_temp))
        verified_entries = list(verified.get("entries", []))
        if _normalized_entries(verified_entries) != _normalized_entries(
            remaining_entries
        ):
            raise DragonWheelDeploymentError(
                "Temporary PAPGT did not preserve the remaining entries"
            )
        remaining = tuple(_entry_group(entry) for entry in verified_entries)
        if group in remaining:
            raise DragonWheelDeploymentError("Temporary PAPGT still contains overlay")

        os.replace(overlay, rollback_overlay)
        overlay_moved = True
        os.replace(papgt_temp, papgt_path)
        papgt_replaced = True
        coordinator.post_restore(str(game), group)
        log.info("Dragon Wheel: removed overlay %s and updated PAPGT", group)

        try:
            shutil.rmtree(rollback_overlay)
        except OSError as exc:
            log.warning("Could not remove Dragon Wheel rollback directory: %s", exc)
        return remaining
    except Exception as exc:
        rollback_errors: list[str] = []
        try:
            papgt_temp.unlink(missing_ok=True)
        except OSError as rollback_exc:
            rollback_errors.append(f"temporary PAPGT cleanup failed: {rollback_exc}")
        if papgt_replaced:
            try:
                restore_temp = papgt_path.with_name(
                    f".0.papgt.dragon-wheel-restore-{uuid.uuid4().hex}.tmp"
                )
                restore_temp.write_bytes(original_papgt)
                os.replace(restore_temp, papgt_path)
            except OSError as rollback_exc:
                rollback_errors.append(f"PAPGT restore failed: {rollback_exc}")
        if overlay_moved and rollback_overlay.exists() and not overlay.exists():
            try:
                os.replace(rollback_overlay, overlay)
            except OSError as rollback_exc:
                rollback_errors.append(f"overlay restore failed: {rollback_exc}")
        detail = (
            "; rollback incomplete: " + "; ".join(rollback_errors)
            if rollback_errors
            else "; rolled back"
        )
        if isinstance(exc, DragonWheelDeploymentError):
            raise DragonWheelDeploymentError(f"{exc}{detail}") from exc
        raise DragonWheelDeploymentError(
            f"Restore failed: {exc}{detail}"
        ) from exc


def _validate_group(group: str) -> str:
    if group != DRAGON_OVERLAY_GROUP:
        raise DragonWheelDeploymentError(
            f"Dragon Wheel must use reserved group {DRAGON_OVERLAY_GROUP}, not {group}"
        )
    return group


def _validate_candidate(result: DragonWheelPatchResult) -> None:
    if result.report.state != "patched":
        raise DragonWheelDeploymentError("Dragon Wheel candidate is not patched")
    if result.report.after_categories != (0x4E, 0x4F, 0x51):
        raise DragonWheelDeploymentError("Dragon Wheel candidate categories changed")
    actual_h = hashlib.sha256(result.pabgh).hexdigest()
    actual_b = hashlib.sha256(result.pabgb).hexdigest()
    if actual_h != result.report.output_pabgh_sha256:
        raise DragonWheelDeploymentError("Dragon Wheel PABGH hash mismatch")
    if actual_b != result.report.output_pabgb_sha256:
        raise DragonWheelDeploymentError("Dragon Wheel PABGB hash mismatch")


def _entry_group(entry: dict[str, Any]) -> str:
    group = entry.get("group_name")
    if not isinstance(group, str):
        raise DragonWheelDeploymentError("PAPGT entry has no group_name")
    return group


def _normalized_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ignore only the string-table offset recomputed by native serialization."""
    return [
        {key: value for key, value in entry.items() if key != "group_name_offset"}
        for entry in entries
    ]


def _get_coordinator(module: ModuleType | Any | None) -> ModuleType | Any:
    if module is not None:
        return module
    from crimson.game_mods import overlay_coordinator as overlay_coordinator

    return overlay_coordinator


def _atomic_restore_file(source: Path, destination: Path) -> None:
    temporary = destination.with_name(
        f".{destination.name}.dragon-wheel-restore-{uuid.uuid4().hex}.tmp"
    )
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _rollback_deployment(
    *,
    papgt_temp: Path,
    staged_live: Path,
    papgt_replaced: bool,
    papgt_backup: Path,
    papgt_path: Path,
    live_replaced: bool,
    live_overlay: Path,
    rollback_overlay: Path,
) -> list[str]:
    errors: list[str] = []
    try:
        papgt_temp.unlink(missing_ok=True)
    except OSError as exc:
        errors.append(f"temporary PAPGT cleanup failed: {exc}")
    if staged_live.exists():
        try:
            shutil.rmtree(staged_live)
        except OSError as exc:
            errors.append(f"staged overlay cleanup failed: {exc}")
    if papgt_replaced and papgt_backup.is_file():
        try:
            _atomic_restore_file(papgt_backup, papgt_path)
        except OSError as exc:
            errors.append(f"PAPGT restore failed: {exc}")
    if live_replaced and live_overlay.exists():
        try:
            shutil.rmtree(live_overlay)
        except OSError as exc:
            errors.append(f"new overlay cleanup failed: {exc}")
    if rollback_overlay.exists() and not live_overlay.exists():
        try:
            os.replace(rollback_overlay, live_overlay)
        except OSError as exc:
            errors.append(f"prior overlay restore failed: {exc}")
    return errors
