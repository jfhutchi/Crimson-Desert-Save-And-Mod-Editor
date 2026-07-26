from __future__ import annotations

import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from .blackstar_timer import BlackstarTimerService, PreviewToken


class _WorkerCancelled(RuntimeError):
    pass


class BlackstarTimerWorker(QObject):
    progress = Signal(str, int)
    completed = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal()
    cancellation_changed = Signal(bool)
    finished = Signal()

    def __init__(
        self,
        *,
        action: str,
        service: BlackstarTimerService,
        game_dir: str | Path,
        token: PreviewToken | None = None,
    ) -> None:
        super().__init__()
        if action not in {"preview", "apply", "restore"}:
            raise ValueError(f"Unsupported Blackstar timer action: {action}")
        if action == "apply" and token is None:
            raise ValueError("Apply requires a matching preview token")
        self.action = action
        self.service = service
        self.game_dir = Path(game_dir)
        self.token = token
        self._cancel_requested = threading.Event()
        self._cancellation_allowed = True
        self._terminal_emitted = False

    @property
    def cancellation_allowed(self) -> bool:
        return self._cancellation_allowed

    @Slot()
    def request_cancel(self) -> None:
        if self._cancellation_allowed:
            self._cancel_requested.set()

    def _check_cancelled(self) -> None:
        if self._cancellation_allowed and self._cancel_requested.is_set():
            raise _WorkerCancelled()

    def _forward_progress(self, phase: str, value: int) -> None:
        self._check_cancelled()
        self.progress.emit(phase, max(0, min(99, int(value))))

    def _emit_completed(self, result: object) -> None:
        if self._terminal_emitted:
            return
        self._terminal_emitted = True
        self.completed.emit(result)

    def _emit_failed(self, message: str, details: str) -> None:
        if self._terminal_emitted:
            return
        self._terminal_emitted = True
        self.failed.emit(message, details)

    def _emit_cancelled(self) -> None:
        if self._terminal_emitted:
            return
        self._terminal_emitted = True
        self.cancelled.emit()

    @Slot()
    def run(self) -> None:
        try:
            self.progress.emit("preflight", 5)
            self._check_cancelled()
            if self.action == "preview":
                result = self.service.preview(
                    self.game_dir, progress=self._forward_progress
                )
                self._check_cancelled()
            else:
                self._cancellation_allowed = False
                self.cancellation_changed.emit(False)
                if self.action == "apply":
                    result = self.service.apply(
                        self.token, progress=self._forward_progress
                    )
                else:
                    result = self.service.restore(
                        self.game_dir, progress=self._forward_progress
                    )
            self.progress.emit("complete", 100)
            self._emit_completed(result)
        except _WorkerCancelled:
            self._emit_cancelled()
        except Exception as exc:
            self._emit_failed(str(exc), traceback.format_exc())
        finally:
            self.finished.emit()
