"""Focused, schema-locked Dragon summon-wheel workflow."""
from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from crimson.game_mods.dragon_wheel_deploy import (
    DRAGON_OVERLAY_GROUP,
    INTERNAL_DIR,
    MARKER_NAME,
    DragonWheelDeploymentError,
    deploy_dragon_wheel,
    restore_dragon_wheel,
)
from crimson.game_mods.dragon_wheel_patch import (
    DragonWheelPatchResult,
    ReserveSlotCompatibilityError,
    analyze_reserveslot,
    enable_dragon_category,
)
from crimson.game_mods.gui.theme import COLORS


OVERLAY_GROUP = DRAGON_OVERLAY_GROUP
DRAGON_DEPLOYMENT_ENABLED = False
DRAGON_DEPLOYMENT_DISABLED_REASON = (
    "Dragon deployment is disabled after reproducible Crimson Desert startup "
    "crashes. Analyze, Preview, and Restore remain available; Apply will not write."
)

log = logging.getLogger(__name__)


class ReserveSlotTab(QWidget):
    config_save_requested = Signal()
    status_message = Signal(str)

    def __init__(
        self,
        config: dict,
        game_path_getter: Callable[[], str],
        rebuild_papgt_fn=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._game_path_getter = game_path_getter
        self._explicit_game_path = ""
        self._source_hashes: tuple[str, str] | None = None
        self._preview_result: DragonWheelPatchResult | None = None
        self._build_ui()

    def set_game_path(self, path: str) -> None:
        self._explicit_game_path = path
        self._invalidate_preview("Game path changed. Analyze and preview again.")

    def set_experimental_mode(self, enabled: bool) -> None:
        del enabled

    def get_staged_files(self) -> dict[str, bytes]:
        """This tab owns its transaction and must not join broad staged writes."""
        return {}

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        heading = QLabel("Dragon Summon Wheel")
        heading.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 20px; font-weight: bold;"
        )
        layout.addWidget(heading)

        explanation = QLabel(
            "Adds only the Dragon category to the normal F1 mount wheel using "
            "a schema-locked ReserveSlot overlay. Unknown game-data layouts are "
            "refused before any write."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        safety = QLabel(
            "Modifies game-data overlays only. Does not edit save files or quest flags.\n"
            "Apply is disabled after reproducible game startup crashes. Restore remains "
            "available for an owned overlay."
        )
        safety.setWordWrap(True)
        safety.setStyleSheet(
            f"color: {COLORS['text']}; background: {COLORS['panel']}; "
            f"border: 2px solid {COLORS['accent']}; border-radius: 6px; "
            "padding: 10px; font-weight: bold;"
        )
        layout.addWidget(safety)

        workflow = QGroupBox("Safe workflow")
        workflow_layout = QHBoxLayout(workflow)
        self._analyze_btn = QPushButton("1. Analyze Game Files")
        self._preview_btn = QPushButton("2. Preview Dragon Wheel Patch")
        self._apply_btn = QPushButton("3. Apply Dragon Wheel Patch")
        self._restore_btn = QPushButton("Restore Dragon Wheel Patch")
        self._analyze_btn.clicked.connect(self._analyze_game_files)
        self._preview_btn.clicked.connect(self._preview_patch)
        self._apply_btn.clicked.connect(self._apply_patch)
        self._restore_btn.clicked.connect(self._restore_patch)
        self._apply_btn.setEnabled(False)
        self._apply_btn.setToolTip(DRAGON_DEPLOYMENT_DISABLED_REASON)
        for button in (
            self._analyze_btn,
            self._preview_btn,
            self._apply_btn,
            self._restore_btn,
        ):
            workflow_layout.addWidget(button)
        layout.addWidget(workflow)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("Ready")
        layout.addWidget(self._progress)

        self._report = QPlainTextEdit()
        self._report.setReadOnly(True)
        self._report.setPlaceholderText(
            "Analyze reads the installed vanilla ReserveSlot files. Preview is a dry run "
            "and does not write to the game."
        )
        layout.addWidget(self._report, 1)

    def _game_path(self) -> str:
        return self._explicit_game_path or self._game_path_getter() or ""

    def _extract_source(self) -> tuple[bytes, bytes]:
        game_path = self._game_path()
        if not game_path:
            raise DragonWheelDeploymentError("Set the Crimson Desert game path first")

        from crimson.game_mods import crimson_rs as crimson_rs

        log.info("Dragon Wheel: extracting vanilla ReserveSlot from %s", game_path)
        self._set_progress(15, "Reading ReserveSlot header")
        pabgh = bytes(
            crimson_rs.extract_file(
                game_path,
                "0008",
                INTERNAL_DIR,
                "reserveslot.pabgh",
            )
        )
        self._set_progress(30, "Reading ReserveSlot body")
        pabgb = bytes(
            crimson_rs.extract_file(
                game_path,
                "0008",
                INTERNAL_DIR,
                "reserveslot.pabgb",
            )
        )
        log.info(
            "Dragon Wheel: extracted pabgh=%d bytes sha256=%s, "
            "pabgb=%d bytes sha256=%s",
            len(pabgh),
            _sha(pabgh),
            len(pabgb),
            _sha(pabgb),
        )
        return pabgh, pabgb

    def _analyze_game_files(self) -> None:
        self._invalidate_preview()
        try:
            pabgh, pabgb = self._extract_source()
            self._set_progress(55, "Validating exact schema")
            report = analyze_reserveslot(pabgh, pabgb)
            installed = (Path(self._game_path()) / OVERLAY_GROUP / MARKER_NAME).is_file()
            lines = [
                "ANALYSIS - READ ONLY",
                f"Schema: {report.schema_id}",
                f"Vanilla source state: {report.state}",
                f"Current categories: {_format_categories(report.before_categories)}",
                f"PABGH SHA-256: {report.source_pabgh_sha256}",
                f"PABGB SHA-256: {report.source_pabgb_sha256}",
                f"Owned {OVERLAY_GROUP} overlay installed: {'yes' if installed else 'no'}",
                "",
                "No files were changed. Run Preview for the exact dry-run diff.",
            ]
            self._report.setPlainText("\n".join(lines))
            self._set_progress(100, "Compatible schema detected")
            self.status_message.emit("Dragon Wheel analysis completed without writing")
            log.info("Dragon Wheel: compatible schema %s detected", report.schema_id)
        except (ReserveSlotCompatibilityError, DragonWheelDeploymentError) as exc:
            self._show_refusal("Analyze refused", exc)
        except Exception as exc:
            self._show_failure("Analyze failed", exc)

    def _preview_patch(self) -> None:
        self._invalidate_preview()
        try:
            pabgh, pabgb = self._extract_source()
            self._set_progress(55, "Detecting Dragon wheel category")
            result = enable_dragon_category(pabgh, pabgb)
            self._set_progress(75, "Verifying reversible candidate")
            self._source_hashes = (
                result.report.source_pabgh_sha256,
                result.report.source_pabgb_sha256,
            )
            self._preview_result = result
            self._apply_btn.setEnabled(
                DRAGON_DEPLOYMENT_ENABLED and not result.report.already_enabled
            )
            lines = [
                "DRY RUN - NOTHING WRITTEN",
                f"Schema: {result.report.schema_id}",
                f"Categories: {_format_categories(result.report.before_categories)} "
                f"-> {_format_categories(result.report.after_categories)}",
                f"Inserted category: 0x4F (Dragon)",
                f"ReserveSlot body growth: +{result.report.byte_growth} byte",
                "Adjusted PABGH entry offsets: "
                + ", ".join(str(index) for index in result.report.adjusted_entry_indexes),
                f"Candidate PABGH SHA-256: {result.report.output_pabgh_sha256}",
                f"Candidate PABGB SHA-256: {result.report.output_pabgb_sha256}",
                "",
                "Save-file changes: none",
                "Quest-flag changes: none",
                f"Planned overlay: {OVERLAY_GROUP}",
                f"Backup root: {self._backup_root()}",
            ]
            if not DRAGON_DEPLOYMENT_ENABLED:
                lines.append(f"Apply disabled: {DRAGON_DEPLOYMENT_DISABLED_REASON}")
            elif result.report.already_enabled:
                lines.append("Dragon is already present; Apply remains disabled.")
            else:
                lines.append("Preview verified. Apply is now enabled for this exact source.")
            self._report.setPlainText("\n".join(lines))
            self._set_progress(100, "Dry run verified - no files written")
            self.status_message.emit("Dragon Wheel dry run completed without writing")
            log.info(
                "Dragon Wheel: dry run %s -> %s, output hashes %s / %s",
                result.report.before_categories,
                result.report.after_categories,
                result.report.output_pabgh_sha256,
                result.report.output_pabgb_sha256,
            )
        except (ReserveSlotCompatibilityError, DragonWheelDeploymentError) as exc:
            self._show_refusal("Preview refused", exc)
        except Exception as exc:
            self._show_failure("Preview failed", exc)

    def _apply_patch(self) -> None:
        if not DRAGON_DEPLOYMENT_ENABLED:
            self._apply_btn.setEnabled(False)
            self._show_refusal(
                "Apply disabled",
                DragonWheelDeploymentError(DRAGON_DEPLOYMENT_DISABLED_REASON),
            )
            return

        preview = self._preview_result
        source_hashes = self._source_hashes
        if preview is None or source_hashes is None:
            self._show_refusal(
                "Apply refused",
                DragonWheelDeploymentError("Run Preview before Apply"),
            )
            return

        try:
            self._set_progress(5, "Re-reading source before write")
            pabgh, pabgb = self._extract_source()
            current_hashes = (_sha(pabgh), _sha(pabgb))
            if current_hashes != source_hashes:
                raise DragonWheelDeploymentError(
                    "ReserveSlot changed after Preview. Preview again before writing."
                )
            candidate = enable_dragon_category(pabgh, pabgb)
            if candidate.pabgh != preview.pabgh or candidate.pabgb != preview.pabgb:
                raise DragonWheelDeploymentError(
                    "Rebuilt candidate differs from Preview. No files were written."
                )

            answer = QMessageBox.question(
                self,
                "Apply Dragon Wheel Patch",
                "This will add only category 0x4F (Dragon) to the normal mount wheel.\n\n"
                f"Overlay: {OVERLAY_GROUP}\n"
                f"Backup: {self._backup_root()}\n"
                "Save files: unchanged\nQuest flags: unchanged\n\n"
                "Crimson Desert must be closed. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self._set_progress(0, "Apply cancelled - nothing written")
                return

            from crimson.game_mods import crimson_rs as crimson_rs

            self._set_progress(60, "Building and backing up overlay transaction")
            receipt = deploy_dragon_wheel(
                game_path=self._game_path(),
                overlay_group=OVERLAY_GROUP,
                patch_result=candidate,
                backup_root=self._backup_root(),
                crimson_rs_module=crimson_rs,
            )
            self._report.appendPlainText(
                "\nAPPLIED\n"
                f"Overlay: {receipt.overlay_group}\n"
                f"Backup: {receipt.backup_dir}\n"
                f"PAPGT groups before: {', '.join(receipt.papgt_groups_before)}\n"
                f"PAPGT groups after: {', '.join(receipt.papgt_groups_after)}\n"
                "Fully restart Crimson Desert before testing."
            )
            self._invalidate_preview()
            self._set_progress(100, "Applied - restart Crimson Desert")
            self.status_message.emit("Dragon Wheel overlay applied with backup")
            log.info(
                "Dragon Wheel: deployed overlay %s; backup=%s",
                receipt.overlay_group,
                receipt.backup_dir,
            )
            QMessageBox.information(
                self,
                "Dragon Wheel Applied",
                f"Overlay {receipt.overlay_group} was installed.\n"
                f"Backup: {receipt.backup_dir}\n\n"
                "Fully restart Crimson Desert, then check the F1 mount wheel.",
            )
        except (ReserveSlotCompatibilityError, DragonWheelDeploymentError) as exc:
            self._show_refusal("Apply refused", exc)
        except PermissionError as exc:
            self._show_failure("Apply failed - run as Administrator", exc)
        except Exception as exc:
            self._show_failure("Apply failed", exc)

    def _restore_patch(self) -> None:
        game_path = self._game_path()
        if not game_path:
            self._show_refusal(
                "Restore refused",
                DragonWheelDeploymentError("Set the Crimson Desert game path first"),
            )
            return
        answer = QMessageBox.question(
            self,
            "Restore Dragon Wheel Patch",
            f"Remove only the owned {OVERLAY_GROUP} Dragon Wheel overlay?\n\n"
            "Foreign and vanilla overlays will be preserved. Crimson Desert must be closed.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            from crimson.game_mods import crimson_rs as crimson_rs

            self._set_progress(40, "Verifying ownership and rebuilding PAPGT")
            remaining = restore_dragon_wheel(
                game_path,
                OVERLAY_GROUP,
                crimson_rs,
            )
            self._invalidate_preview()
            self._report.setPlainText(
                "RESTORED\n"
                f"Owned overlay {OVERLAY_GROUP} removed.\n"
                f"Remaining PAPGT groups: {', '.join(remaining)}\n"
                "Save files and quest flags were not changed.\n"
                "Fully restart Crimson Desert."
            )
            self._set_progress(100, "Restored - restart Crimson Desert")
            self.status_message.emit("Dragon Wheel overlay restored")
            log.info("Dragon Wheel: restored overlay %s", OVERLAY_GROUP)
            QMessageBox.information(
                self,
                "Dragon Wheel Restored",
                "The owned Dragon Wheel overlay was removed. Fully restart the game.",
            )
        except DragonWheelDeploymentError as exc:
            self._show_refusal("Restore refused", exc)
        except PermissionError as exc:
            self._show_failure("Restore failed - run as Administrator", exc)
        except Exception as exc:
            self._show_failure("Restore failed", exc)

    def _backup_root(self) -> Path:
        if getattr(sys, "frozen", False):
            application_dir = Path(sys.executable).resolve().parent
        else:
            application_dir = Path(__file__).resolve().parents[2]
        return application_dir / "backups" / "dragon-wheel"

    def _invalidate_preview(self, message: str | None = None) -> None:
        self._source_hashes = None
        self._preview_result = None
        self._apply_btn.setEnabled(False)
        if message:
            self._progress.setValue(0)
            self._progress.setFormat(message)

    def _set_progress(self, value: int, message: str) -> None:
        self._progress.setValue(value)
        self._progress.setFormat(message)
        QApplication.processEvents()

    def _show_refusal(self, title: str, error: Exception) -> None:
        self._invalidate_preview()
        message = str(error)
        self._report.setPlainText(
            f"REFUSED - NOTHING WRITTEN\n{message}\n\n"
            "The installed ReserveSlot schema or transaction preconditions did not match."
        )
        self._set_progress(0, "Refused - nothing written")
        self.status_message.emit(message)
        log.warning("Dragon Wheel: %s: %s", title, error)
        QMessageBox.warning(self, title, f"{message}\n\nNothing was written.")

    def _show_failure(self, title: str, error: Exception) -> None:
        self._invalidate_preview()
        message = str(error)
        self._report.setPlainText(f"FAILED\n{message}")
        self._set_progress(0, "Failed")
        self.status_message.emit(message)
        log.exception("Dragon Wheel: %s", title, exc_info=error)
        QMessageBox.critical(self, title, message)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _format_categories(categories: tuple[int, ...]) -> str:
    return "[" + ", ".join(f"0x{value:02X}" for value in categories) + "]"
