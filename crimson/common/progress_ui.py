"""Shared progress feedback so no long operation ever looks frozen.

Both applications use these helpers instead of hand-rolling QProgressDialog
plumbing: `run_blocking_task` for any slow in-process work, and
`download_icons_with_progress` for the item icon set.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from .gui_task_worker import start_gui_task

log = logging.getLogger(__name__)


def run_blocking_task(
    parent,
    *,
    title: str,
    message: str,
    task: Callable,
    completed: Callable,
    failed: Optional[Callable] = None,
) -> None:
    """Run a long task off the GUI thread behind a modal busy dialog.

    The WindowModal dialog blocks interaction, so state cannot be edited while
    the worker runs; `task(report)` executes on a worker thread and
    `completed`/`failed` run on the GUI thread after the dialog closes.
    """
    progress = QProgressDialog(message, "", 0, 100, parent)
    progress.setWindowTitle(title)
    progress.setWindowModality(Qt.WindowModal)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    progress.show()

    def _on_progress(text: str, value: int) -> None:
        progress.setLabelText(text)
        progress.setValue(max(0, min(99, int(value))))

    def _on_completed(result) -> None:
        progress.setValue(100)
        progress.close()
        completed(result)

    def _on_failed(message_text: str, details: str) -> None:
        progress.close()
        if failed is not None:
            failed(message_text, details)
        else:
            log.error("%s failed: %s\n%s", title, message_text, details)
            QMessageBox.critical(parent, title, f"{message_text}\n\n{details}")

    start_gui_task(
        parent,
        task=task,
        completed=_on_completed,
        failed=_on_failed,
        progress=_on_progress,
    )


def download_icons_with_progress(
    parent,
    icon_cache,
    *,
    status: Optional[Callable[[str], None]] = None,
    finished: Optional[Callable] = None,
) -> None:
    """Download the icon set on a worker pool behind a cancelable progress bar.

    IconCache refuses a second concurrent download itself; the dialog is
    non-modal so the app stays usable.
    """
    cancel_event = threading.Event()

    progress = QProgressDialog(
        "Contacting GitHub for the icon list...", "Cancel", 0, 100, parent
    )
    progress.setWindowTitle("Downloading Item Icons")
    progress.setWindowModality(Qt.NonModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    progress.canceled.connect(cancel_event.set)
    progress.show()

    def _on_progress(folder, downloaded, skipped, errors, total) -> None:
        done = downloaded + skipped + errors
        progress.setLabelText(
            f"Downloading item icons [{folder}]\n"
            f"{downloaded} downloaded, {skipped} cached, {errors} failed "
            f"of {total}"
        )
        progress.setValue(min(99, int(done / max(1, total) * 100)))

    def _on_finished(stats) -> None:
        progress.setValue(100)
        progress.close()
        if status is not None:
            if stats.get('cancelled'):
                status(
                    f"Icon download cancelled after {stats['downloaded']} files; "
                    "run it again anytime to resume"
                )
            else:
                status(
                    f"Icons ready: {stats['downloaded']} downloaded, "
                    f"{stats['skipped']} cached, {stats['errors']} failed"
                )
        if finished is not None:
            finished(stats)

    if not icon_cache.bulk_download_async(
        progress=_on_progress, completed=_on_finished, cancel_event=cancel_event
    ):
        progress.close()
