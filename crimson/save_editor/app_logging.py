from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def application_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "CrimsonSaveEditor"
    return Path.home() / ".local" / "state" / "CrimsonSaveEditor"


def configure_logging(base_dir: Path | None = None) -> Path:
    target = (base_dir or application_data_dir()) / "logs" / "crimson-save-editor.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        if getattr(handler, "_crimson_save_editor_handler", False):
            root.removeHandler(handler)
            handler.close()
    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = RotatingFileHandler(
        target,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler._crimson_save_editor_handler = True
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    if not getattr(sys, "frozen", False):
        console = logging.StreamHandler(sys.stdout)
        console._crimson_save_editor_handler = True
        console.setFormatter(formatter)
        root.addHandler(console)
    return target


def new_operation_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _fields(values: dict[str, object]) -> str:
    return " ".join(f"{key}={value!r}" for key, value in sorted(values.items()))


@contextlib.contextmanager
def phase(
    logger: logging.Logger,
    operation_id: str,
    name: str,
    **fields: object,
) -> Iterator[None]:
    started = time.perf_counter()
    logger.info("operation=%s phase_start=%s %s", operation_id, name, _fields(fields))
    try:
        yield
    except Exception:
        logger.exception(
            "operation=%s phase_failure=%s elapsed_ms=%.1f %s",
            operation_id,
            name,
            (time.perf_counter() - started) * 1000,
            _fields(fields),
        )
        raise
    logger.info(
        "operation=%s phase_success=%s elapsed_ms=%.1f %s",
        operation_id,
        name,
        (time.perf_counter() - started) * 1000,
        _fields(fields),
    )
