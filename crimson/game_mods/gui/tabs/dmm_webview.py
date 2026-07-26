"""DMM Integration Tab — launches and manages DMM alongside CrimsonGameMods.

DMM runs as its own window. This tab provides launch controls, status display,
and keeps the two tools synchronized through the shared state system.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QSizePolicy, QMessageBox, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PySide6.QtGui import QFont, QColor, QBrush

from crimson.game_mods.gui.theme import COLORS

log = logging.getLogger(__name__)

HAS_WEBENGINE = True  # compat flag — always available


def _find_dmm_exe() -> Optional[str]:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent.parent

    candidates = [
        base / "dmm" / "definitive-mod-manager.exe",
        base / "DMMLoader" / "src-tauri" / "target" / "release" / "definitive-mod-manager.exe",
        base.parent / "DMMLoader" / "src-tauri" / "target" / "release" / "definitive-mod-manager.exe",
        base.parent / "ResearchFolder" / "3D Modding" / "DMM 1.3.0-pre.1.exe",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


class DmmWebViewTab(QWidget):
    """DMM integration tab — launch, monitor, and coordinate with DMM."""

    status_message = Signal(str)
    config_save_requested = Signal()

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._config = config
        self._dmm_exe = _find_dmm_exe() or config.get("dmm_exe_path", "")
        self._dmm_process: Optional[subprocess.Popen] = None
        self._build_ui()

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_status)
        self._poll.start(3000)

    def set_game_path(self, path: str) -> None:
        self._refresh_overlays()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header
        header = QLabel("Mod Manager — Definitive Mod Manager")
        header.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {COLORS['accent']};")
        layout.addWidget(header)

        desc = QLabel(
            "DMM handles DLL/ASI plugins, texture mods, audio replacements, "
            "language packs, ReShade presets, and JSON byte-patch mods. "
            "Both tools coordinate overlays automatically through the shared state system."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #999; margin-bottom: 4px;")
        layout.addWidget(desc)

        # DMM Controls
        ctrl_group = QGroupBox("DMM Controls")
        ctrl_layout = QVBoxLayout(ctrl_group)

        # Status row
        status_row = QHBoxLayout()
        self._status_dot = QLabel("●")
        self._status_dot.setFixedWidth(20)
        status_row.addWidget(self._status_dot)
        self._status_label = QLabel("Checking...")
        self._status_label.setStyleSheet("font-weight: bold;")
        status_row.addWidget(self._status_label, 1)
        ctrl_layout.addLayout(status_row)

        # Path row
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("DMM:"))
        self._path_label = QLabel(self._dmm_exe or "Not found")
        self._path_label.setStyleSheet("color: #888;")
        self._path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_row.addWidget(self._path_label, 1)

        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(70)
        browse_btn.clicked.connect(self._browse_dmm)
        path_row.addWidget(browse_btn)
        ctrl_layout.addLayout(path_row)

        # Buttons
        btn_row = QHBoxLayout()

        self._launch_btn = QPushButton("Launch DMM")
        self._launch_btn.setObjectName("accentBtn")
        self._launch_btn.setMinimumHeight(36)
        self._launch_btn.clicked.connect(self._launch_dmm)
        btn_row.addWidget(self._launch_btn)

        sync_btn = QPushButton("Sync Game Path")
        sync_btn.setToolTip("Write our game path into DMM's config")
        sync_btn.clicked.connect(self._sync_game_path)
        btn_row.addWidget(sync_btn)

        btn_row.addStretch(1)
        ctrl_layout.addLayout(btn_row)

        layout.addWidget(ctrl_group)

        # Overlay Status
        overlay_group = QGroupBox("Active Overlays")
        overlay_layout = QVBoxLayout(overlay_group)

        self._overlay_table = QTableWidget(0, 4)
        self._overlay_table.setHorizontalHeaderLabels(
            ["Group", "Owner", "Content", "Size"])
        hdr = self._overlay_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._overlay_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._overlay_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._overlay_table.verticalHeader().setVisible(False)
        overlay_layout.addWidget(self._overlay_table)

        self._overlay_summary = QLabel("")
        self._overlay_summary.setStyleSheet("color: #888; font-size: 11px;")
        overlay_layout.addWidget(self._overlay_summary)

        layout.addWidget(overlay_group, 1)

        self._update_status()
        self._refresh_overlays()

    def _browse_dmm(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate DMM", "", "Executables (*.exe)")
        if path:
            self._dmm_exe = path
            self._config["dmm_exe_path"] = path
            self._path_label.setText(path)
            self.config_save_requested.emit()
            self._update_status()

    def _launch_dmm(self):
        if not self._dmm_exe or not os.path.isfile(self._dmm_exe):
            QMessageBox.warning(self, "Not Found",
                                "DMM executable not found. Use Browse to locate it.")
            return

        if self._dmm_process and self._dmm_process.poll() is None:
            QMessageBox.information(self, "Running",
                                    "DMM is already running.")
            return

        # Sync game path before launching
        self._sync_game_path(silent=True)

        try:
            dmm_dir = os.path.dirname(self._dmm_exe)
            self._dmm_process = subprocess.Popen(
                [self._dmm_exe], cwd=dmm_dir,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            self._update_status()
            self.status_message.emit("DMM launched")
        except Exception as e:
            QMessageBox.critical(self, "Failed", f"Could not launch DMM:\n{e}")

    def _sync_game_path(self, silent=False):
        game_path = self._config.get("game_install_path", "")
        if not game_path or not self._dmm_exe:
            if not silent:
                QMessageBox.warning(self, "Cannot Sync",
                                    "Set game path and DMM location first.")
            return

        config_path = Path(self._dmm_exe).parent / "config.json"
        try:
            cfg = {}
            if config_path.is_file():
                with open(config_path, "r") as f:
                    cfg = json.load(f)
            cfg["gamePath"] = game_path.replace("/", "\\")
            if not cfg.get("modsPath"):
                mods_dir = Path(self._dmm_exe).parent / "mods"
                mods_dir.mkdir(exist_ok=True)
                cfg["modsPath"] = str(mods_dir)
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)
            if not silent:
                self.status_message.emit(f"Game path synced to DMM")
        except Exception as e:
            if not silent:
                QMessageBox.critical(self, "Sync Failed", str(e))

    def _update_status(self):
        exe_ok = self._dmm_exe and os.path.isfile(self._dmm_exe)
        running = self._dmm_process and self._dmm_process.poll() is None

        if not exe_ok:
            self._status_dot.setStyleSheet("color: #FF4444; font-size: 16px;")
            self._status_label.setText("DMM not found")
            self._status_label.setStyleSheet("font-weight: bold; color: #FF4444;")
            self._launch_btn.setEnabled(False)
        elif running:
            self._status_dot.setStyleSheet("color: #44FF44; font-size: 16px;")
            self._status_label.setText("DMM is running")
            self._status_label.setStyleSheet("font-weight: bold; color: #44FF44;")
            self._launch_btn.setText("Running...")
            self._launch_btn.setEnabled(False)
        else:
            self._status_dot.setStyleSheet("color: #66BBFF; font-size: 16px;")
            self._status_label.setText("DMM ready")
            self._status_label.setStyleSheet("font-weight: bold; color: #66BBFF;")
            self._launch_btn.setText("Launch DMM")
            self._launch_btn.setEnabled(True)

    def _refresh_overlays(self):
        game_path = self._config.get("game_install_path", "")
        self._overlay_table.setRowCount(0)
        if not game_path or not os.path.isdir(game_path):
            self._overlay_summary.setText("Set game path to see overlays")
            return

        from crimson.game_mods.overlay_coordinator import OUR_GROUPS, DMM_PREFIXES
        from crimson.game_mods.shared_state import load_state

        state = load_state(game_path)
        groups = []

        for entry in sorted(os.listdir(game_path)):
            entry_path = os.path.join(game_path, entry)
            if not os.path.isdir(entry_path):
                continue
            paz = os.path.join(entry_path, "0.paz")
            if not os.path.isfile(paz):
                continue
            try:
                num = int(entry)
                if num < 36:
                    continue
            except ValueError:
                pass

            if entry in OUR_GROUPS:
                owner = "CrimsonGameMods"
                content = OUR_GROUPS[entry]
            elif entry.startswith(DMM_PREFIXES):
                owner = "DMM"
                content = f"DMM overlay"
            elif entry == "0036":
                owner = "Mod Loader"
                content = "Legacy overlay"
            else:
                owner = "Unknown"
                content = ""

            if entry in state.overlays:
                content = state.overlays[entry].content or content
                owner = state.overlays[entry].owner or owner

            size = os.path.getsize(paz)
            groups.append((entry, owner, content, size))

        self._overlay_table.setRowCount(len(groups))
        our = dmm = other = 0
        for i, (name, owner, content, size) in enumerate(groups):
            name_item = QTableWidgetItem(name)
            name_item.setFont(QFont("Consolas", 9))
            self._overlay_table.setItem(i, 0, name_item)

            owner_item = QTableWidgetItem(owner)
            if owner == "CrimsonGameMods":
                owner_item.setForeground(QBrush(QColor(COLORS["accent"])))
                our += 1
            elif "DMM" in owner:
                owner_item.setForeground(QBrush(QColor("#44AAFF")))
                dmm += 1
            else:
                owner_item.setForeground(QBrush(QColor("#888")))
                other += 1
            self._overlay_table.setItem(i, 1, owner_item)
            self._overlay_table.setItem(i, 2, QTableWidgetItem(content))

            size_str = f"{size / 1024 / 1024:.1f} MB" if size > 1024 * 1024 else f"{size / 1024:.0f} KB"
            size_item = QTableWidgetItem(size_str)
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._overlay_table.setItem(i, 3, size_item)

        parts = []
        if our: parts.append(f"{our} ours")
        if dmm: parts.append(f"{dmm} DMM")
        if other: parts.append(f"{other} other")
        self._overlay_summary.setText(
            f"{len(groups)} overlay(s): " + ", ".join(parts) if parts else "No overlays")

    def _poll_status(self):
        if self._dmm_process and self._dmm_process.poll() is not None:
            self._dmm_process = None
            self._update_status()
            self._refresh_overlays()
