from __future__ import annotations

from collections.abc import Callable, Sequence
import time
import traceback

from PySide6.QtCore import QObject, QTimer, Signal, Slot


class IncrementalGuiJob(QObject):
    """Consume GUI-only work in short batches so Windows can keep repainting."""

    progress = Signal(int, int)
    completed = Signal()
    cancelled = Signal()
    failed = Signal(str, str)

    def __init__(
        self,
        owner: QObject,
        *,
        items: Sequence[object],
        consume: Callable[[int, object], None],
        batch_size: int = 96,
        time_budget_ms: int = 9,
    ) -> None:
        super().__init__(owner)
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if time_budget_ms < 1:
            raise ValueError("time_budget_ms must be positive")
        self._items = items
        self._consume = consume
        self._batch_size = batch_size
        self._time_budget_seconds = time_budget_ms / 1000.0
        self._index = 0
        self._cancelled = False
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            raise RuntimeError("Incremental GUI job is already running")
        self._running = True
        QTimer.singleShot(0, self._consume_batch)

    @Slot()
    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def _consume_batch(self) -> None:
        if not self._running:
            return
        if self._cancelled:
            self._running = False
            self.cancelled.emit()
            return

        total = len(self._items)
        deadline = time.perf_counter() + self._time_budget_seconds
        consumed = 0
        try:
            while (
                self._index < total
                and consumed < self._batch_size
                and time.perf_counter() < deadline
            ):
                index = self._index
                self._consume(index, self._items[index])
                self._index += 1
                consumed += 1
        except Exception as exc:
            self._running = False
            self.failed.emit(str(exc), traceback.format_exc())
            return

        self.progress.emit(self._index, total)
        if self._index >= total:
            self._running = False
            self.completed.emit()
        else:
            # A short non-zero delay lets paint, input, and native window-message
            # events run before the next batch on Windows.
            QTimer.singleShot(1, self._consume_batch)
