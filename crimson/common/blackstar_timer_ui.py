from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from .blackstar_timer import (
    BlackstarTimerService,
    PreviewReport,
    TimerStatus,
    TransactionReport,
)
from .blackstar_timer_worker import BlackstarTimerWorker
from .crimson_icons import game_art_icon

log = logging.getLogger(__name__)


class BlackstarTimerPanel(QFrame):
    status_message = Signal(str)

    def __init__(
        self,
        *,
        title: str,
        service_factory: Callable[[], BlackstarTimerService] = BlackstarTimerService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("blackstarTimerPanel")
        self.setAccessibleName(title)
        self.setMinimumHeight(280)
        self._service_factory = service_factory
        self._game_dir = Path()
        self._preview_token = None
        self._thread: QThread | None = None
        self._worker: BlackstarTimerWorker | None = None
        self._progress: QProgressDialog | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(6)

        hero = QFrame()
        hero.setObjectName("blackstarHero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(0, 0, 0, 14)
        hero_layout.setSpacing(14)

        art_halo = QFrame(hero)
        art_halo.setObjectName("blackstarHeroArtHalo")
        art_halo.setFixedSize(64, 64)
        art_layout = QHBoxLayout(art_halo)
        art_layout.setContentsMargins(3, 3, 3, 3)
        art = QLabel(art_halo)
        art.setObjectName("blackstarHeroArt")
        art.setAlignment(Qt.AlignCenter)
        art.setPixmap(game_art_icon(1000799).pixmap(56, 56))
        art_layout.addWidget(art)
        hero_layout.addWidget(art_halo)

        title_stack = QVBoxLayout()
        title_stack.setContentsMargins(0, 2, 0, 0)
        title_stack.setSpacing(0)

        eyebrow = QLabel("GAME ARCHIVE SETTING")
        eyebrow.setObjectName("blackstarTimerEyebrow")
        eyebrow.setProperty("eyebrow", True)
        title_stack.addWidget(eyebrow)

        title = QLabel("Blackstar")
        title.setObjectName("blackstarTimerTitle")
        title.setProperty("displayTitle", True)
        title_stack.addWidget(title)
        hero_layout.addLayout(title_stack)
        hero_layout.addStretch(1)

        compatibility_stack = QVBoxLayout()
        compatibility_stack.setContentsMargins(0, 8, 0, 0)
        compatibility_stack.setSpacing(1)
        compatibility_eyebrow = QLabel("COMPATIBILITY")
        compatibility_eyebrow.setObjectName("blackstarCompatibilityEyebrow")
        compatibility_eyebrow.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        compatibility = QLabel("VERIFIED FOR 1.14")
        compatibility.setObjectName("blackstarCompatibility")
        compatibility.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        compatibility_stack.addWidget(compatibility_eyebrow)
        compatibility_stack.addWidget(compatibility)
        hero_layout.addLayout(compatibility_stack)
        layout.addWidget(hero)

        body = QFrame()
        body.setObjectName("blackstarTimerBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 18, 0, 0)
        body_layout.setSpacing(32)

        main = QFrame()
        main.setObjectName("blackstarTimerMain")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(6)

        section_title = QLabel("Extended Flight")
        section_title.setObjectName("blackstarTimerSectionTitle")
        main_layout.addWidget(section_title)

        info = QLabel(
            "The tested preset affects every save by changing Blackstar's game archive "
            "settings. Preview verifies the "
            "archive and prepares a reversible three-file backup before Apply becomes "
            "available: mounted time 30 minutes; summon cooldown 1 second. It does not "
            "change the loaded save or any quest-completion flags."
        )
        info.setObjectName("blackstarTimerWarning")
        info.setProperty("muted", True)
        info.setWordWrap(True)
        main_layout.addWidget(info)

        duration_row, self._duration_before, self._duration_after = self._metric_row(
            "Mounted duration",
            "Time before Blackstar returns",
            "10 min",
            "30 min",
            "blackstarDuration",
        )
        main_layout.addWidget(duration_row)

        cooldown_row, self._cooldown_before, self._cooldown_after = self._metric_row(
            "Summon cooldown",
            "Delay after mounted time expires",
            "60 min",
            "1 sec",
            "blackstarCooldown",
        )
        main_layout.addWidget(cooldown_row)

        status_block = QFrame()
        status_block.setObjectName("blackstarStatusBlock")
        status_layout = QVBoxLayout(status_block)
        status_layout.setContentsMargins(12, 4, 6, 4)
        status_layout.setSpacing(2)
        self._status = QLabel("Preview required - nothing written")
        self._status.setObjectName("blackstarTimerStatus")
        self._status.setProperty("recordValue", True)
        self._status.setWordWrap(True)
        status_layout.addWidget(self._status)
        self._status_detail = QLabel(
            "Select the Crimson Desert install folder, then run Preview."
        )
        self._status_detail.setObjectName("blackstarTimerStatusDetail")
        self._status_detail.setProperty("muted", True)
        self._status_detail.setWordWrap(True)
        status_layout.addWidget(self._status_detail)
        main_layout.addWidget(status_block)
        main_layout.addStretch()

        record = QFrame()
        record.setObjectName("changeRecord")
        record.setMinimumWidth(300)
        record_layout = QVBoxLayout(record)
        record_layout.setContentsMargins(22, 0, 0, 0)
        record_layout.setSpacing(0)
        record_title = QLabel("Change Record")
        record_title.setObjectName("changeRecordTitle")
        record_layout.addWidget(record_title)
        record_layout.addSpacing(10)
        record_layout.addWidget(self._record_row("Target", "Game archives"))
        fields_row, self._fields_value = self._record_value_row(
            "Fields changed", "2", "blackstarFieldsChanged"
        )
        record_layout.addWidget(fields_row)
        save_row, self._save_changes_value = self._record_value_row(
            "Save changes", "None", "blackstarSaveChanges"
        )
        record_layout.addWidget(save_row)
        quest_row, self._quest_changes_value = self._record_value_row(
            "Quest changes", "None", "blackstarQuestChanges"
        )
        record_layout.addWidget(quest_row)
        backup_row, self._backup_value = self._record_value_row(
            "Backup", "On apply", "blackstarBackup"
        )
        record_layout.addWidget(backup_row)
        record_layout.addSpacing(14)

        buttons = QHBoxLayout()
        self._apply_button = QPushButton("Apply Preset")
        self._apply_button.setObjectName("blackstarTimerApply")
        self._apply_button.setProperty("primaryAction", True)
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(lambda: self._start("apply"))
        buttons.addWidget(self._apply_button)

        self._preview_button = QPushButton("Preview 30m / 1s")
        self._preview_button.setObjectName("blackstarTimerPreview")
        self._preview_button.setProperty("quietAction", True)
        self._preview_button.clicked.connect(lambda: self._start("preview"))
        buttons.addWidget(self._preview_button)

        self._restore_button = QPushButton("Restore Original")
        self._restore_button.setObjectName("blackstarTimerRestore")
        self._restore_button.setProperty("quietAction", True)
        self._restore_button.setEnabled(False)
        self._restore_button.clicked.connect(lambda: self._start("restore"))
        buttons.addWidget(self._restore_button)
        record_layout.addLayout(buttons)
        record_layout.addStretch()

        body_layout.addWidget(main, 1)
        body_layout.addWidget(record, 0)
        layout.addWidget(body, 1)

    @staticmethod
    def _metric_row(
        label: str,
        detail: str,
        before: str,
        after: str,
        prefix: str,
    ) -> tuple[QFrame, QLabel, QLabel]:
        row = QFrame()
        row.setObjectName("blackstarMetricRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 8, 0, 8)
        row_layout.setSpacing(12)
        labels = QVBoxLayout()
        labels.setContentsMargins(0, 0, 0, 0)
        labels.setSpacing(0)
        name = QLabel(label)
        name.setProperty("recordValue", True)
        note = QLabel(detail)
        note.setProperty("muted", True)
        labels.addWidget(name)
        labels.addWidget(note)
        row_layout.addLayout(labels)
        row_layout.addStretch()
        before_label = QLabel(before)
        before_label.setObjectName(prefix + "Before")
        before_label.setProperty("metricBefore", True)
        before_font = before_label.font()
        before_font.setStrikeOut(True)
        before_label.setFont(before_font)
        row_layout.addWidget(before_label)
        arrow = QLabel(">")
        arrow.setProperty("eyebrow", True)
        row_layout.addWidget(arrow)
        after_label = QLabel(after)
        after_label.setObjectName(prefix + "After")
        after_label.setProperty("metricAfter", True)
        row_layout.addWidget(after_label)
        return row, before_label, after_label

    @staticmethod
    def _record_row(label: str, value: str) -> QFrame:
        row, _value = BlackstarTimerPanel._record_value_row(label, value, "")
        return row

    @staticmethod
    def _record_value_row(
        label: str,
        value: str,
        object_name: str,
    ) -> tuple[QFrame, QLabel]:
        row = QFrame()
        row.setObjectName("changeRecordRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 7, 0, 7)
        key = QLabel(label)
        key.setProperty("muted", True)
        value_label = QLabel(value)
        if object_name:
            value_label.setObjectName(object_name)
        value_label.setProperty("recordValue", True)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row_layout.addWidget(key)
        row_layout.addStretch()
        row_layout.addWidget(value_label)
        return row, value_label

    @staticmethod
    def _time_label(seconds: int | None) -> str:
        if seconds is None:
            return "-"
        if seconds < 60:
            return f"{seconds} sec"
        minutes = seconds // 60
        return f"{minutes} min"

    def set_game_path(self, game_dir: str | Path) -> None:
        normalized = Path(game_dir).expanduser() if str(game_dir).strip() else Path()
        if normalized != self._game_dir:
            self._preview_token = None
            self._apply_button.setEnabled(False)
        self._game_dir = normalized
        self._restore_button.setEnabled(self._has_owned_backup())

    def _has_owned_backup(self) -> bool:
        if not str(self._game_dir):
            return False
        root = (
            self._game_dir
            / "bin64"
            / "SEModLoad"
            / "Backups"
            / "BlackstarTimer"
        )
        return root.is_dir() and any(
            (child / "manifest.json").is_file()
            for child in root.iterdir()
            if child.is_dir()
        )

    def _start(self, action: str) -> None:
        if self._thread is not None:
            QMessageBox.information(
                self, "Blackstar Timer", "A Blackstar timer operation is already running."
            )
            return
        game = self._game_dir.expanduser().resolve()
        required = (
            game / "0008" / "0.pamt",
            game / "0008" / "0.paz",
            game / "meta" / "0.papgt",
        )
        if not all(path.is_file() for path in required):
            QMessageBox.warning(
                self,
                "Blackstar Timer",
                "Select a valid Crimson Desert install folder first.\n\n"
                "Expected 0008/0.paz, 0008/0.pamt, and meta/0.papgt.",
            )
            return
        if action == "apply" and self._preview_token is None:
            QMessageBox.warning(
                self,
                "Preview Required",
                "Run Preview first. Apply is enabled only for that exact unchanged source.",
            )
            return

        service = self._service_factory()
        thread = QThread(self)
        worker = BlackstarTimerWorker(
            action=action,
            service=service,
            game_dir=game,
            token=self._preview_token if action == "apply" else None,
        )
        worker.moveToThread(thread)
        progress = QProgressDialog(
            "Checking Crimson Desert game archives...", "Cancel", 0, 100, self
        )
        progress.setWindowTitle("Blackstar Timer")
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setValue(0)

        self._thread = thread
        self._worker = worker
        self._progress = progress
        self._set_busy(True)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.cancellation_changed.connect(self._on_cancellation_changed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._cleanup)
        progress.canceled.connect(worker.request_cancel, Qt.DirectConnection)
        progress.show()
        thread.start()

    def _set_busy(self, busy: bool) -> None:
        self._preview_button.setEnabled(not busy)
        self._apply_button.setEnabled(not busy and self._preview_token is not None)
        self._restore_button.setEnabled(not busy and self._has_owned_backup())

    def _on_progress(self, phase: str, value: int) -> None:
        label = phase.replace("_", " ").title()
        if self._progress is not None:
            self._progress.setValue(value)
            self._progress.setLabelText(f"{label}...")
        self._status.setText(label)
        self._status_detail.setText("Archive verification is running in the background.")
        self.status_message.emit(f"Blackstar Timer: {label}")

    def _on_cancellation_changed(self, enabled: bool) -> None:
        if self._progress is not None and not enabled:
            self._progress.setCancelButton(None)
            self._progress.setLabelText(
                "Writing and verifying game archives; cancellation is disabled..."
            )

    def _on_completed(self, result: object) -> None:
        if isinstance(result, PreviewReport):
            self._preview_token = result.token
            self._apply_button.setEnabled(result.token is not None)
            self._duration_before.setText(self._time_label(result.duration_before))
            self._duration_after.setText(self._time_label(result.duration_after))
            self._cooldown_before.setText(self._time_label(result.cooldown_before))
            self._cooldown_after.setText(self._time_label(result.cooldown_after))
            self._status.setText("Preview verified - nothing written")
            self._status_detail.setText(result.reason)
            self._backup_value.setText("Ready on apply")
            self._preview_button.setText("Preview Again")
        elif isinstance(result, TransactionReport):
            self._preview_token = None
            self._apply_button.setEnabled(False)
            self._status.setText(
                "Preset applied and verified"
                if result.action == "apply"
                else "Original values restored and verified"
            )
            self._status_detail.setText(result.reason)
            self._backup_value.setText(
                "Created" if result.action == "apply" else "Restored"
            )
        self._restore_button.setEnabled(self._has_owned_backup())
        self.status_message.emit(self._status.text())
        if self._progress is not None:
            self._progress.close()
        QMessageBox.information(
            self, "Blackstar Timer Report", self._format_report(result)
        )

    def _format_report(self, result: object) -> str:
        if isinstance(result, PreviewReport):
            return (
                "Mode: Preview (nothing written)\n"
                f"Status: {result.status.value}\n"
                f"Profile: {result.profile_id}\n"
                f"Cooldown: {result.cooldown_before} -> {result.cooldown_after} seconds\n"
                f"Mounted time: {result.duration_before} -> {result.duration_after} seconds\n"
                f"Candidate SHA-256: {result.candidate_body_sha256}\n"
                f"Compressed bytes: {result.candidate_compressed_size} / {result.slot_capacity}\n\n"
                "Save-file changes: none\nQuest-flag changes: none"
            )
        if isinstance(result, TransactionReport):
            backup = str(result.backup_dir) if result.backup_dir else "none"
            return (
                f"Mode: {result.action.title()}\n"
                f"Status: {result.status.value}\n"
                f"Profile: {result.profile_id}\n"
                f"Changed: {'yes' if result.changed else 'no'}\n"
                f"Cooldown: {result.cooldown_seconds} seconds\n"
                f"Mounted time: {result.duration_seconds} seconds\n"
                f"Backup: {backup}\n\n"
                "Save-file changes: none\nQuest-flag changes: none"
            )
        return str(result)

    def _on_failed(self, message: str, details: str) -> None:
        log.error("Blackstar timer worker failed: %s\n%s", message, details)
        self._preview_token = None
        self._apply_button.setEnabled(False)
        self._status.setText("Operation failed - nothing written")
        self._status_detail.setText(message)
        if self._progress is not None:
            self._progress.close()
        QMessageBox.critical(
            self,
            "Blackstar Timer Failed",
            f"{message}\n\nNo save file was changed. See the application log for details.",
        )

    def _on_cancelled(self) -> None:
        self._status.setText("Cancelled - nothing written")
        self._status_detail.setText("Cancelled before any game archive write.")
        if self._progress is not None:
            self._progress.close()
        self.status_message.emit(self._status.text())

    def _cleanup(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        self._progress = None
        self._set_busy(False)
        if thread is not None:
            thread.deleteLater()
