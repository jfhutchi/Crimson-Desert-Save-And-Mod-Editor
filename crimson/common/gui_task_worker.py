from __future__ import annotations

import threading
import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot


class _TaskCancelled(RuntimeError):
    pass


class GuiTaskWorker(QObject):
    """Run one long task on a QThread with progress and cooperative cancellation."""

    progress = Signal(str, int)
    completed = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, *, task: Callable[[Callable[[str, int], None]], object]) -> None:
        super().__init__()
        self._task = task
        self._cancel_requested = threading.Event()
        self._terminal_emitted = False

    @Slot()
    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def _check_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise _TaskCancelled()

    def _report(self, phase: str, value: int) -> None:
        self._check_cancelled()
        self.progress.emit(str(phase), max(0, min(99, int(value))))

    def _terminal(self, signal, *args) -> None:
        if self._terminal_emitted:
            return
        self._terminal_emitted = True
        signal.emit(*args)

    @Slot()
    def run(self) -> None:
        try:
            self._check_cancelled()
            result = self._task(self._report)
            self._check_cancelled()
            self._terminal(self.completed, result)
        except _TaskCancelled:
            self._terminal(self.cancelled)
        except Exception as exc:
            self._terminal(self.failed, str(exc), traceback.format_exc())
        finally:
            self.finished.emit()


class GuiTaskHandle(QObject):
    """Own a worker thread and marshal every callback back to the GUI thread."""

    def __init__(
        self,
        owner: QObject,
        *,
        task: Callable[[Callable[[str, int], None]], object],
        completed: Callable[[object], None],
        failed: Callable[[str, str], None],
        progress: Callable[[str, int], None] | None = None,
        cancelled: Callable[[], None] | None = None,
        finished: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(owner)
        self._owner = owner
        self._completed_callback = completed
        self._failed_callback = failed
        self._progress_callback = progress
        self._cancelled_callback = cancelled
        self._finished_callback = finished
        self._thread: QThread | None = QThread(owner)
        self._worker: GuiTaskWorker | None = GuiTaskWorker(task=task)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress, Qt.QueuedConnection)
        self._worker.completed.connect(self._on_completed, Qt.QueuedConnection)
        self._worker.failed.connect(self._on_failed, Qt.QueuedConnection)
        self._worker.cancelled.connect(self._on_cancelled, Qt.QueuedConnection)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._cleanup, Qt.QueuedConnection)
        self._thread.start()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @Slot()
    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()

    @Slot(str, int)
    def _on_progress(self, message: str, value: int) -> None:
        if self._progress_callback is not None:
            self._progress_callback(message, value)

    @Slot(object)
    def _on_completed(self, result: object) -> None:
        self._completed_callback(result)

    @Slot(str, str)
    def _on_failed(self, message: str, details: str) -> None:
        self._failed_callback(message, details)

    @Slot()
    def _on_cancelled(self) -> None:
        if self._cancelled_callback is not None:
            self._cancelled_callback()

    @Slot()
    def _cleanup(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        handles = getattr(self._owner, "_crimson_task_handles", None)
        if isinstance(handles, list) and self in handles:
            handles.remove(self)
        if self._finished_callback is not None:
            self._finished_callback()
        if thread is not None:
            thread.deleteLater()
        self.deleteLater()


def start_gui_task(
    owner: QObject,
    *,
    task: Callable[[Callable[[str, int], None]], object],
    completed: Callable[[object], None],
    failed: Callable[[str, str], None],
    progress: Callable[[str, int], None] | None = None,
    cancelled: Callable[[], None] | None = None,
    finished: Callable[[], None] | None = None,
) -> GuiTaskHandle:
    """Start a tracked background task without repeating fragile QThread plumbing."""
    handles = getattr(owner, "_crimson_task_handles", None)
    if not isinstance(handles, list):
        handles = []
        setattr(owner, "_crimson_task_handles", handles)
    handle = GuiTaskHandle(
        owner,
        task=task,
        completed=completed,
        failed=failed,
        progress=progress,
        cancelled=cancelled,
        finished=finished,
    )
    handles.append(handle)
    return handle
