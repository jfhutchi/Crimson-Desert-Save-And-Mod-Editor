"""Mod Loader tab — deep DMM (Definitive Mod Manager) integration.

NOT a thin launcher.  This tab:
 1. Auto-detects DMM exe (bundled or external)
 2. Reads DMM's config.json + mods folder directly — shows every mod
    with title, author, version, target file, patch count
 3. Identifies which DMM mods target iteminfo.pabgb (convertible)
 4. Runs the Stacker Inspector to convert byte patches → semantic fields
 5. Shows overlay status across BOTH tools in one unified view
 6. Syncs game path bidirectionally
 7. Can launch DMM for mod types we don't handle (DLL/ASI/texture/audio)
 8. Maintains shared state file so tools stay aware of each other

DMM handles: DLL/ASI, textures, audio, language, ReShade, browser mods.
We handle:   ItemBuffs, Stores, SkillTree, DropSets, Spawns, BagSpace,
             Stacker (field-level iteminfo merge), FieldEdit.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import struct
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer, QThread, QSize
from PySide6.QtGui import QColor, QFont, QIcon, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QMessageBox, QFrame, QSizePolicy, QTextEdit,
    QAbstractItemView, QSplitter, QTabWidget, QProgressBar,
    QCheckBox,
)

from crimson.game_mods.gui.theme import COLORS
from crimson.game_mods.gui.utils import make_scope_label
from crimson.game_mods.shared_state import (
    load_state, save_state, record_overlay, scan_dmm_into_state,
    get_dmm_iteminfo_mods, ModdingState, DmmModEntry,
)

log = logging.getLogger(__name__)

DMM_EXE_NAMES = [
    "definitive-mod-manager.exe",
    "Definitive Mod Manager.exe",
]

OUR_GROUPS = {
    "0058": "ItemBuffs (iteminfo)",
    "0059": "ItemBuffs (equipslot)",
    "0060": "Stores",
    "0061": "Reserved",
    "0062": "Stacker (merged items)",
    "0063": "Stacker (equipslot) / SkillTree",
    "0065": "Kliff Gun Fix (characterinfo)",
    "0066": "ItemBuffs (index files)",
}
DMM_PREFIXES = ("dmmsa", "dmmgen", "dmmequ", "dmmlang")


def _find_dmm_exe(search_root: Optional[Path] = None) -> Optional[str]:
    """Search for DMM exe in bundled location, sibling dirs, common paths."""
    candidates = []

    if getattr(sys, "frozen", False):
        our_dir = Path(sys.executable).parent
    else:
        our_dir = Path(__file__).resolve().parent.parent.parent

    # Bundled inside our directory (Phase 3 unified build)
    candidates.append(our_dir / "dmm")
    candidates.append(our_dir / "DMMLoader")
    candidates.append(our_dir)
    # Sibling directories
    candidates.append(our_dir.parent / "DMMLoader")
    candidates.append(our_dir.parent / "Definitive Mod Manager")
    candidates.append(our_dir.parent / "DMM")

    if search_root:
        candidates.append(search_root)

    for base in [
        Path(os.environ.get("LOCALAPPDATA", "x")) / "Programs",
        Path(os.environ.get("PROGRAMFILES", "x")),
        Path(os.environ.get("USERPROFILE", "x")) / "Desktop",
        Path(os.environ.get("USERPROFILE", "x")) / "Downloads",
    ]:
        if base.is_dir():
            candidates.append(base)

    for cdir in candidates:
        if not cdir.is_dir():
            continue
        for name in DMM_EXE_NAMES:
            p = cdir / name
            if p.is_file():
                return str(p)
        # One level deep
        try:
            for sub in cdir.iterdir():
                if sub.is_dir():
                    for name in DMM_EXE_NAMES:
                        if (sub / name).is_file():
                            return str(sub / name)
        except PermissionError:
            continue
    return None


def _read_dmm_config(dmm_exe_path: str) -> Optional[dict]:
    """Read DMM's config.json from next to its exe."""
    p = Path(dmm_exe_path).parent / "config.json"
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("Failed to read DMM config: %s", e)
        return None


def _scan_dmm_mods_folder(mods_path: str) -> list[dict]:
    """Scan DMM mods folder, parse each JSON, return mod details."""
    if not mods_path or not os.path.isdir(mods_path):
        return []
    results = []
    for fname in sorted(os.listdir(mods_path)):
        if not fname.lower().endswith(".json"):
            continue
        fpath = os.path.join(mods_path, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            results.append({
                "file_name": fname, "title": fname, "author": "?",
                "version": "?", "patch_count": 0, "game_files": [],
                "targets_iteminfo": False, "error": True, "path": fpath,
            })
            continue
        info = doc.get("modinfo") or doc
        patches = doc.get("patches", [])
        game_files = set()
        patch_count = 0
        targets_iteminfo = False
        for patch in patches:
            gf = patch.get("game_file", "")
            game_files.add(gf)
            patch_count += len(patch.get("changes", []))
            if "iteminfo.pabgb" in gf.lower():
                targets_iteminfo = True
        results.append({
            "file_name": fname,
            "title": info.get("title") or info.get("name") or fname,
            "author": info.get("author") or "—",
            "version": info.get("version") or "—",
            "patch_count": patch_count,
            "game_files": sorted(game_files),
            "targets_iteminfo": targets_iteminfo,
            "error": False,
            "path": fpath,
        })
    return results


def _scan_all_overlays(game_path: str) -> list[dict]:
    """Scan game directory for all overlay groups, classify by owner."""
    if not game_path or not os.path.isdir(game_path):
        return []
    groups = []
    for entry in sorted(os.listdir(game_path)):
        entry_path = os.path.join(game_path, entry)
        if not os.path.isdir(entry_path):
            continue
        paz = os.path.join(entry_path, "0.paz")
        pamt = os.path.join(entry_path, "0.pamt")
        if not os.path.isfile(paz) and not os.path.isfile(pamt):
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
            content = f"DMM overlay ({entry})"
        elif entry == "0036":
            owner = "DMM / JMM"
            content = "Mod loader overlay"
        else:
            owner = "Unknown"
            content = ""

        paz_size = os.path.getsize(paz) if os.path.isfile(paz) else 0
        pamt_size = os.path.getsize(pamt) if os.path.isfile(pamt) else 0
        groups.append({
            "name": entry, "owner": owner, "content": content,
            "paz_size": paz_size, "pamt_size": pamt_size,
            "path": entry_path,
        })
    return groups


class ConvertWorker(QThread):
    """Background thread for converting DMM byte-patch mods to semantic."""
    progress = Signal(str)
    finished = Signal(list)  # list of result dicts

    def __init__(self, mod_paths: list[str], vanilla_bytes: bytes):
        super().__init__()
        self._mod_paths = mod_paths
        self._vanilla = vanilla_bytes

    def run(self):
        from crimson.game_mods.gui.tabs import iteminfo_inspector
        results = []
        for path in self._mod_paths:
            fname = os.path.basename(path)
            self.progress.emit(f"Inspecting {fname}...")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                changes = iteminfo_inspector.collect_iteminfo_patches(doc)
                if not changes:
                    results.append({
                        "file": fname, "status": "skip",
                        "reason": "no iteminfo patches", "inspections": [],
                    })
                    continue
                inspections = iteminfo_inspector.inspect_patches(
                    self._vanilla, changes)
                applied = sum(1 for i in inspections
                              if i.field_path is not None)
                stale = sum(1 for i in inspections
                            if i.status == iteminfo_inspector.PATCH_STALE)
                no_entry = sum(1 for i in inspections
                               if i.status == iteminfo_inspector.PATCH_NO_ENTRY)
                results.append({
                    "file": fname, "status": "ok",
                    "total": len(inspections),
                    "resolved": applied,
                    "stale": stale,
                    "no_entry": no_entry,
                    "inspections": inspections,
                    "doc": doc,
                    "path": path,
                })
            except Exception as e:
                results.append({
                    "file": fname, "status": "error",
                    "reason": str(e), "inspections": [],
                })
        self.finished.emit(results)


class ModLoaderTab(QWidget):
    """Deep DMM integration — manages mods, converts patches, coordinates overlays."""

    status_message = Signal(str)
    config_save_requested = Signal()

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._config = config
        self._game_path: str = config.get("game_install_path", "")
        self._dmm_exe_path: str = config.get("dmm_exe_path", "")
        self._dmm_config: Optional[dict] = None
        self._dmm_process: Optional[subprocess.Popen] = None
        self._all_dmm_mods: list[dict] = []
        self._convert_worker: Optional[ConvertWorker] = None

        self._build_ui()

        if not self._dmm_exe_path:
            found = _find_dmm_exe()
            if found:
                self._dmm_exe_path = found
                self._config["dmm_exe_path"] = found
                self.config_save_requested.emit()

        self._refresh_all()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_dmm_status)
        self._poll_timer.start(5000)

    def set_game_path(self, path: str) -> None:
        self._game_path = path
        self._refresh_all()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(make_scope_label("game"))

        header = QLabel("Mod Loader — Unified Mod Management")
        header.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {COLORS['accent']};")
        root.addWidget(header)

        desc = QLabel(
            "Manage ALL mod types from one place. Game data mods (ItemBuffs, "
            "Stores, Stacker) run natively. DLL/ASI, textures, audio, and "
            "third-party JSON mods are handled through DMM's engine. "
            "Both tools coordinate overlays automatically."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #999; margin-bottom: 2px;")
        root.addWidget(desc)

        # ── DMM Connection ───────────────────────────────────────────
        conn_group = QGroupBox("DMM Engine")
        conn_layout = QVBoxLayout(conn_group)
        conn_layout.setSpacing(4)

        row1 = QHBoxLayout()
        self._status_dot = QLabel("●")
        self._status_dot.setFixedWidth(18)
        row1.addWidget(self._status_dot)
        self._status_label = QLabel("Scanning...")
        self._status_label.setStyleSheet("font-weight: bold;")
        row1.addWidget(self._status_label, 1)

        self._launch_btn = QPushButton("Launch DMM")
        self._launch_btn.setObjectName("accentBtn")
        self._launch_btn.setFixedHeight(30)
        self._launch_btn.clicked.connect(self._launch_dmm)
        row1.addWidget(self._launch_btn)
        conn_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Path:"))
        self._path_label = QLabel("—")
        self._path_label.setStyleSheet("color: #888;")
        self._path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row2.addWidget(self._path_label, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(70)
        browse_btn.clicked.connect(self._browse_dmm)
        row2.addWidget(browse_btn)
        detect_btn = QPushButton("Auto-Detect")
        detect_btn.setFixedWidth(85)
        detect_btn.clicked.connect(self._auto_detect_dmm)
        row2.addWidget(detect_btn)
        conn_layout.addLayout(row2)

        self._dmm_info = QLabel("")
        self._dmm_info.setStyleSheet("color: #888; font-size: 11px;")
        self._dmm_info.setWordWrap(True)
        conn_layout.addWidget(self._dmm_info)

        self._path_warn = QLabel("")
        self._path_warn.setStyleSheet("color: #FF8800; font-size: 11px;")
        self._path_warn.setWordWrap(True)
        self._path_warn.setVisible(False)
        conn_layout.addWidget(self._path_warn)

        # Sync + Refresh row
        sync_row = QHBoxLayout()
        self._sync_btn = QPushButton("Sync Game Path to DMM")
        self._sync_btn.setToolTip("Write our game path into DMM's config")
        self._sync_btn.clicked.connect(self._sync_game_path_to_dmm)
        sync_row.addWidget(self._sync_btn)
        refresh_btn = QPushButton("Refresh All")
        refresh_btn.clicked.connect(self._refresh_all)
        sync_row.addWidget(refresh_btn)
        sync_row.addStretch(1)
        conn_layout.addLayout(sync_row)

        root.addWidget(conn_group)

        # ── Main content: tabs for Mods + Overlays + Conversion ──────
        self._inner_tabs = QTabWidget()
        self._inner_tabs.setTabPosition(QTabWidget.North)

        # Tab 1: DMM Mods Browser
        self._mods_widget = self._build_mods_tab()
        self._inner_tabs.addTab(self._mods_widget, "DMM Mods")

        # Tab 2: Overlay Status
        self._overlay_widget = self._build_overlay_tab()
        self._inner_tabs.addTab(self._overlay_widget, "Overlays")

        # Tab 3: Conversion
        self._convert_widget = self._build_convert_tab()
        self._inner_tabs.addTab(self._convert_widget, "Convert to Semantic")

        root.addWidget(self._inner_tabs, 1)

    def _build_mods_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        info = QLabel(
            "All mods in DMM's mods folder. Mods targeting iteminfo.pabgb "
            "can be converted to semantic field patches and pulled into the Stacker."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(info)

        self._mods_table = QTableWidget(0, 6)
        self._mods_table.setHorizontalHeaderLabels([
            "Mod Name", "Author", "Version", "Target Files",
            "Patches", "ItemInfo?"
        ])
        hdr = self._mods_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self._mods_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._mods_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._mods_table.verticalHeader().setVisible(False)
        layout.addWidget(self._mods_table, 1)

        self._mods_summary = QLabel("")
        self._mods_summary.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._mods_summary)

        # Extra mod types
        extra_frame = QFrame()
        extra_layout = QHBoxLayout(extra_frame)
        extra_layout.setContentsMargins(0, 0, 0, 0)
        extra_layout.setSpacing(12)
        self._asi_label = QLabel("")
        self._asi_label.setStyleSheet("color: #66AAFF; font-size: 11px;")
        extra_layout.addWidget(self._asi_label)
        self._tex_label = QLabel("")
        self._tex_label.setStyleSheet("color: #AAFF66; font-size: 11px;")
        extra_layout.addWidget(self._tex_label)
        self._browser_label = QLabel("")
        self._browser_label.setStyleSheet("color: #FFAA66; font-size: 11px;")
        extra_layout.addWidget(self._browser_label)
        extra_layout.addStretch(1)
        layout.addWidget(extra_frame)

        return w

    def _build_overlay_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        info = QLabel(
            "Live overlay status in your game directory. Shows every "
            "overlay group, who wrote it, and when. Both CrimsonGameMods "
            "and DMM overlays coexist — the game loads them all."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(info)

        self._overlay_table = QTableWidget(0, 5)
        self._overlay_table.setHorizontalHeaderLabels([
            "Group", "Owner", "Content", "PAZ Size", "PAMT Size"
        ])
        hdr = self._overlay_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._overlay_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._overlay_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._overlay_table.verticalHeader().setVisible(False)
        layout.addWidget(self._overlay_table, 1)

        self._overlay_summary = QLabel("")
        self._overlay_summary.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._overlay_summary)

        return w

    def _build_convert_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        info = QLabel(
            "Convert DMM's legacy JSON byte-patch mods into semantic "
            "field-level patches. This uses the Stacker Inspector to "
            "resolve each (entry, rel_offset) to a named field, decode "
            "the value, and classify as Applied / Stale / Missing.\n\n"
            "Converted mods can then be pulled into the Stacker for "
            "field-level merging with other iteminfo mods."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #CCC; font-size: 12px;")
        layout.addWidget(info)

        btn_row = QHBoxLayout()
        self._convert_btn = QPushButton("Analyze DMM ItemInfo Mods")
        self._convert_btn.setObjectName("accentBtn")
        self._convert_btn.setMinimumHeight(34)
        self._convert_btn.clicked.connect(self._run_conversion)
        btn_row.addWidget(self._convert_btn)

        self._export_btn = QPushButton("Export as Format 3 JSON")
        self._export_btn.setToolTip(
            "Export the analyzed mods as semantic Format 3 JSON "
            "that the Stacker can import directly"
        )
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_format3)
        btn_row.addWidget(self._export_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self._convert_progress = QProgressBar()
        self._convert_progress.setVisible(False)
        layout.addWidget(self._convert_progress)

        self._convert_table = QTableWidget(0, 6)
        self._convert_table.setHorizontalHeaderLabels([
            "Mod", "Total Patches", "Resolved", "Stale",
            "Missing Entry", "Status"
        ])
        hdr = self._convert_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, 6):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self._convert_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._convert_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._convert_table.verticalHeader().setVisible(False)
        layout.addWidget(self._convert_table, 1)

        self._convert_log = QTextEdit()
        self._convert_log.setReadOnly(True)
        self._convert_log.setMaximumHeight(150)
        self._convert_log.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 11px; "
            "background: #111; color: #CCC;"
        )
        layout.addWidget(self._convert_log)

        self._last_convert_results: list = []

        return w

    # ── Actions ──────────────────────────────────────────────────────

    def _browse_dmm(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate DMM Executable",
            str(Path(self._dmm_exe_path).parent) if self._dmm_exe_path else "",
            "Executables (*.exe)",
        )
        if not path:
            return
        self._dmm_exe_path = path
        self._config["dmm_exe_path"] = path
        self.config_save_requested.emit()
        self._refresh_all()
        self.status_message.emit(f"DMM path set: {path}")

    def _auto_detect_dmm(self) -> None:
        found = _find_dmm_exe()
        if found:
            self._dmm_exe_path = found
            self._config["dmm_exe_path"] = found
            self.config_save_requested.emit()
            self._refresh_all()
            self.status_message.emit(f"DMM auto-detected: {found}")
        else:
            QMessageBox.information(
                self, "Not Found",
                "Could not find DMM.\nUse Browse to locate it manually,\n"
                "or place it next to CrimsonGameMods."
            )

    def _launch_dmm(self) -> None:
        if not self._dmm_exe_path or not os.path.isfile(self._dmm_exe_path):
            QMessageBox.warning(self, "DMM Not Found",
                                "Locate DMM first using Browse or Auto-Detect.")
            return
        if self._dmm_process and self._dmm_process.poll() is None:
            QMessageBox.information(self, "Running", "DMM is already running.")
            return
        try:
            self._dmm_process = subprocess.Popen(
                [self._dmm_exe_path],
                cwd=os.path.dirname(self._dmm_exe_path),
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            self._update_connection_status()
            self.status_message.emit("DMM launched")
        except Exception as e:
            QMessageBox.critical(self, "Launch Failed", f"Could not launch DMM:\n{e}")

    def _sync_game_path_to_dmm(self) -> None:
        if not self._game_path:
            QMessageBox.warning(self, "No Game Path", "Set game path first.")
            return
        if not self._dmm_exe_path or not os.path.isfile(self._dmm_exe_path):
            QMessageBox.warning(self, "No DMM", "Locate DMM first.")
            return
        config_path = Path(self._dmm_exe_path).parent / "config.json"
        try:
            dmm_cfg = {}
            if config_path.is_file():
                with open(config_path, "r", encoding="utf-8") as f:
                    dmm_cfg = json.load(f)
            dmm_cfg["gamePath"] = self._game_path.replace("/", "\\")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(dmm_cfg, f, indent=2)
            self._refresh_all()
            self.status_message.emit(f"DMM game path synced to {self._game_path}")
        except Exception as e:
            QMessageBox.critical(self, "Failed", f"Could not write DMM config:\n{e}")

    # ── Conversion Pipeline ──────────────────────────────────────────

    def _run_conversion(self) -> None:
        """Analyze all DMM iteminfo mods through the Stacker Inspector."""
        if not self._dmm_exe_path or not os.path.isfile(self._dmm_exe_path):
            QMessageBox.warning(self, "No DMM", "Locate DMM first.")
            return
        if not self._game_path:
            QMessageBox.warning(self, "No Game Path", "Set game path first.")
            return

        # Find iteminfo mods
        iteminfo_mods = [m for m in self._all_dmm_mods if m.get("targets_iteminfo")]
        if not iteminfo_mods:
            QMessageBox.information(
                self, "No ItemInfo Mods",
                "None of the mods in DMM's folder target iteminfo.pabgb."
            )
            return

        # Load vanilla iteminfo
        vanilla_path = None
        for candidate in [
            os.path.join(self._game_path, "0008", "0.paz"),
            os.path.join(self._game_path, "0008", "0.paz.sebak"),
        ]:
            if os.path.isfile(candidate):
                vanilla_path = candidate
                break

        if not vanilla_path:
            QMessageBox.warning(
                self, "No Vanilla",
                "Could not find vanilla game data (0008/0.paz).\n"
                "Set the correct game path."
            )
            return

        self._convert_log.clear()
        self._convert_log.append("Loading vanilla iteminfo.pabgb...")
        self._convert_btn.setEnabled(False)
        self._convert_progress.setVisible(True)
        self._convert_progress.setRange(0, 0)

        try:
            from crimson.game_mods import crimson_rs as crimson_rs
            if hasattr(crimson_rs, "extract_file_from_paz"):
                vanilla_bytes = crimson_rs.extract_file_from_paz(
                    vanilla_path, "gamedata/binary__/client/bin/iteminfo.pabgb"
                )
            elif hasattr(crimson_rs, "extract_file"):
                vanilla_bytes = bytes(crimson_rs.extract_file(
                    os.path.dirname(vanilla_path), "0008",
                    "gamedata/binary__/client/bin", "iteminfo.pabgb"
                ))
            else:
                self._convert_log.append(
                    "ERROR: crimson_rs is too old — missing extract functions.\n"
                    "Update crimson_rs to use the conversion pipeline."
                )
                self._convert_btn.setEnabled(True)
                self._convert_progress.setVisible(False)
                return
            if not vanilla_bytes:
                self._convert_log.append("ERROR: Could not extract iteminfo from PAZ")
                self._convert_btn.setEnabled(True)
                self._convert_progress.setVisible(False)
                return
        except AttributeError as e:
            self._convert_log.append(
                f"ERROR: crimson_rs function not available: {e}\n"
                f"Update crimson_rs to the latest version."
            )
            self._convert_btn.setEnabled(True)
            self._convert_progress.setVisible(False)
            return
        except Exception as e:
            self._convert_log.append(f"ERROR: {e}")
            self._convert_btn.setEnabled(True)
            self._convert_progress.setVisible(False)
            return

        self._convert_log.append(
            f"Vanilla loaded: {len(vanilla_bytes):,} bytes. "
            f"Analyzing {len(iteminfo_mods)} mod(s)..."
        )

        mod_paths = [m["path"] for m in iteminfo_mods]
        self._convert_worker = ConvertWorker(mod_paths, vanilla_bytes)
        self._convert_worker.progress.connect(
            lambda msg: self._convert_log.append(msg))
        self._convert_worker.finished.connect(self._on_conversion_done)
        self._convert_worker.start()

    def _on_conversion_done(self, results: list) -> None:
        self._convert_btn.setEnabled(True)
        self._convert_progress.setVisible(False)
        self._last_convert_results = results

        self._convert_table.setRowCount(len(results))
        all_ok = True
        for i, r in enumerate(results):
            self._convert_table.setItem(i, 0, QTableWidgetItem(r["file"]))

            if r["status"] == "ok":
                total = r["total"]
                resolved = r["resolved"]
                stale = r["stale"]
                no_entry = r["no_entry"]
                pct = (resolved / total * 100) if total else 0

                self._convert_table.setItem(i, 1, self._num_item(total))
                self._convert_table.setItem(i, 2, self._num_item(resolved))
                self._convert_table.setItem(i, 3, self._num_item(stale))
                self._convert_table.setItem(i, 4, self._num_item(no_entry))

                if pct >= 90:
                    status_text = f"{pct:.0f}% resolved"
                    color = QColor("#44FF44")
                elif pct >= 50:
                    status_text = f"{pct:.0f}% resolved"
                    color = QColor("#FFAA00")
                else:
                    status_text = f"{pct:.0f}% resolved"
                    color = QColor("#FF4444")
                    all_ok = False

                status_item = QTableWidgetItem(status_text)
                status_item.setForeground(QBrush(color))
                self._convert_table.setItem(i, 5, status_item)

                self._convert_log.append(
                    f"  {r['file']}: {resolved}/{total} patches resolved "
                    f"({stale} stale, {no_entry} missing)"
                )
            elif r["status"] == "skip":
                for c in range(1, 5):
                    self._convert_table.setItem(i, c, QTableWidgetItem("—"))
                skip_item = QTableWidgetItem(r.get("reason", "skipped"))
                skip_item.setForeground(QBrush(QColor("#888")))
                self._convert_table.setItem(i, 5, skip_item)
            else:
                for c in range(1, 5):
                    self._convert_table.setItem(i, c, QTableWidgetItem("—"))
                err_item = QTableWidgetItem(f"Error: {r.get('reason', '?')}")
                err_item.setForeground(QBrush(QColor("#FF4444")))
                self._convert_table.setItem(i, 5, err_item)
                all_ok = False

        ok_count = sum(1 for r in results if r["status"] == "ok")
        self._convert_log.append(
            f"\nDone. {ok_count}/{len(results)} mods analyzed successfully."
        )

        has_results = any(r["status"] == "ok" and r["resolved"] > 0
                          for r in results)
        self._export_btn.setEnabled(has_results)

        if has_results:
            self._convert_log.append(
                "Click 'Export as Format 3 JSON' to create Stacker-compatible "
                "semantic patches, or use Stacker's 'Pull from DMM' button."
            )

    def _export_format3(self) -> None:
        """Export successfully analyzed mods as Format 3 semantic JSON."""
        if not self._last_convert_results:
            return

        export_dir = QFileDialog.getExistingDirectory(
            self, "Export Directory for Format 3 JSONs")
        if not export_dir:
            return

        exported = 0
        for r in self._last_convert_results:
            if r["status"] != "ok" or r["resolved"] == 0:
                continue
            inspections = r["inspections"]
            intents = []
            for insp in inspections:
                if insp.field_path is None or insp.new_value is None:
                    continue
                intents.append({
                    "entry": insp.entry,
                    "field": insp.field_path,
                    "value": insp.new_value,
                    "type": insp.field_ty or "unknown",
                })
            if not intents:
                continue

            doc = r.get("doc", {})
            info = doc.get("modinfo") or doc
            out = {
                "format": 3,
                "modinfo": {
                    "title": info.get("title") or info.get("name") or r["file"],
                    "author": info.get("author", ""),
                    "version": info.get("version", ""),
                    "description": (
                        f"Semantic conversion of {r['file']}. "
                        f"{len(intents)} field edits from {r['total']} byte patches."
                    ),
                    "converted_from": r["file"],
                    "conversion_tool": "CrimsonGameMods Stacker Inspector",
                },
                "intents": intents,
            }
            base = Path(r["file"]).stem
            out_path = os.path.join(export_dir, f"{base}_semantic.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)
            exported += 1

        self._convert_log.append(f"\nExported {exported} Format 3 JSON(s) to {export_dir}")
        self.status_message.emit(f"Exported {exported} semantic mod(s)")
        QMessageBox.information(
            self, "Exported",
            f"Exported {exported} mod(s) as Format 3 JSON.\n\n"
            f"Folder: {export_dir}\n\n"
            "You can now drop these into the Stacker Tool for "
            "field-level merging."
        )

    # ── Refresh ──────────────────────────────────────────────────────

    def _refresh_all(self) -> None:
        self._dmm_config = None
        if self._dmm_exe_path and os.path.isfile(self._dmm_exe_path):
            self._dmm_config = _read_dmm_config(self._dmm_exe_path)
        self._update_connection_status()
        self._refresh_mods()
        self._refresh_overlays()

        # Update shared state
        if self._game_path and self._dmm_exe_path:
            try:
                scan_dmm_into_state(self._game_path, self._dmm_exe_path)
            except Exception as e:
                log.warning("Shared state update failed: %s", e)

    def _update_connection_status(self) -> None:
        exe_ok = self._dmm_exe_path and os.path.isfile(self._dmm_exe_path)
        running = self._dmm_process and self._dmm_process.poll() is None

        if not exe_ok:
            self._status_dot.setStyleSheet("color: #FF4444; font-size: 16px;")
            self._status_label.setText("DMM not found")
            self._status_label.setStyleSheet("font-weight: bold; color: #FF4444;")
            self._path_label.setText("—")
            self._dmm_info.setText(
                "Place DMM next to CrimsonGameMods, or use Browse/Auto-Detect.")
            self._launch_btn.setEnabled(False)
            self._launch_btn.setText("Launch DMM")
            self._sync_btn.setEnabled(False)
            self._path_warn.setVisible(False)
            return

        self._path_label.setText(self._dmm_exe_path)
        self._sync_btn.setEnabled(True)

        if running:
            self._status_dot.setStyleSheet("color: #44FF44; font-size: 16px;")
            self._status_label.setText("DMM Running")
            self._status_label.setStyleSheet("font-weight: bold; color: #44FF44;")
            self._launch_btn.setText("Running...")
            self._launch_btn.setEnabled(False)
        else:
            self._status_dot.setStyleSheet("color: #66BBFF; font-size: 16px;")
            self._status_label.setText("DMM Ready")
            self._status_label.setStyleSheet("font-weight: bold; color: #66BBFF;")
            self._launch_btn.setText("Launch DMM")
            self._launch_btn.setEnabled(True)

        if self._dmm_config:
            dmm_game = self._dmm_config.get("gamePath", "")
            dmm_mods = self._dmm_config.get("modsPath", "")
            parts = []
            if dmm_game:
                parts.append(f"Game: {dmm_game}")
            if dmm_mods:
                parts.append(f"Mods: {dmm_mods}")
            self._dmm_info.setText("  |  ".join(parts) if parts else "")

            if dmm_game and self._game_path:
                ours = os.path.normcase(os.path.normpath(self._game_path))
                theirs = os.path.normcase(os.path.normpath(dmm_game))
                if ours != theirs:
                    self._path_warn.setText(
                        f"Game path mismatch!  Ours: {self._game_path}  "
                        f"|  DMM: {dmm_game}  — Click 'Sync Game Path to DMM'")
                    self._path_warn.setVisible(True)
                else:
                    self._path_warn.setVisible(False)
            else:
                self._path_warn.setVisible(False)
        else:
            self._dmm_info.setText("No config — launch DMM once to initialize")
            self._path_warn.setVisible(False)

    def _refresh_mods(self) -> None:
        self._mods_table.setRowCount(0)
        self._all_dmm_mods = []

        if not self._dmm_config:
            self._mods_summary.setText("DMM not connected")
            self._asi_label.setText("")
            self._tex_label.setText("")
            self._browser_label.setText("")
            return

        mods_path = self._dmm_config.get("modsPath", "")
        self._all_dmm_mods = _scan_dmm_mods_folder(mods_path)

        active_names = set()
        for am in self._dmm_config.get("activeMods", []):
            fn = am.get("fileName", "") if isinstance(am, dict) else str(am)
            active_names.add(fn)

        self._mods_table.setRowCount(len(self._all_dmm_mods))
        iteminfo_count = 0

        for i, mod in enumerate(self._all_dmm_mods):
            is_active = mod["file_name"] in active_names

            name_item = QTableWidgetItem(mod["title"])
            if not is_active:
                name_item.setForeground(QBrush(QColor("#666")))
            self._mods_table.setItem(i, 0, name_item)

            author_item = QTableWidgetItem(mod["author"])
            if not is_active:
                author_item.setForeground(QBrush(QColor("#555")))
            self._mods_table.setItem(i, 1, author_item)

            ver_item = QTableWidgetItem(mod["version"])
            self._mods_table.setItem(i, 2, ver_item)

            files_str = ", ".join(mod["game_files"][:3])
            if len(mod["game_files"]) > 3:
                files_str += f" +{len(mod['game_files'])-3}"
            files_item = QTableWidgetItem(files_str)
            files_item.setStyleSheet("font-size: 10px;")
            self._mods_table.setItem(i, 3, files_item)

            patch_item = QTableWidgetItem(str(mod["patch_count"]))
            patch_item.setTextAlignment(Qt.AlignCenter)
            self._mods_table.setItem(i, 4, patch_item)

            if mod["targets_iteminfo"]:
                ii_item = QTableWidgetItem("Yes")
                ii_item.setForeground(QBrush(QColor("#44FF44")))
                iteminfo_count += 1
            else:
                ii_item = QTableWidgetItem("—")
                ii_item.setForeground(QBrush(QColor("#555")))
            ii_item.setTextAlignment(Qt.AlignCenter)
            self._mods_table.setItem(i, 5, ii_item)

        active_count = len(active_names & {m["file_name"] for m in self._all_dmm_mods})
        self._mods_summary.setText(
            f"{len(self._all_dmm_mods)} mod(s) in folder, "
            f"{active_count} active, "
            f"{iteminfo_count} target iteminfo (convertible)"
        )

        # Extra mod types
        asi = self._dmm_config.get("activeAsiMods", [])
        tex = self._dmm_config.get("activeTextures", [])
        browser = self._dmm_config.get("activeBrowserMods", [])
        self._asi_label.setText(f"ASI/DLL: {len(asi)}" if asi else "")
        self._tex_label.setText(f"Textures: {len(tex)}" if tex else "")
        self._browser_label.setText(f"Browser: {len(browser)}" if browser else "")

    def _refresh_overlays(self) -> None:
        self._overlay_table.setRowCount(0)

        groups = _scan_all_overlays(self._game_path)
        if not groups:
            self._overlay_summary.setText(
                "No overlays" if self._game_path else "Set game path to scan")
            return

        # Also read shared state for richer content descriptions
        state = load_state(self._game_path) if self._game_path else None

        self._overlay_table.setRowCount(len(groups))
        our_count = dmm_count = other_count = 0

        for i, g in enumerate(groups):
            name_item = QTableWidgetItem(g["name"])
            name_item.setFont(QFont("Consolas", 9))
            self._overlay_table.setItem(i, 0, name_item)

            owner_item = QTableWidgetItem(g["owner"])
            if g["owner"] == "CrimsonGameMods":
                owner_item.setForeground(QBrush(QColor(COLORS["accent"])))
                our_count += 1
            elif "DMM" in g["owner"] or "JMM" in g["owner"]:
                owner_item.setForeground(QBrush(QColor("#44AAFF")))
                dmm_count += 1
            else:
                owner_item.setForeground(QBrush(QColor("#888")))
                other_count += 1
            self._overlay_table.setItem(i, 1, owner_item)

            content = g["content"]
            if state and g["name"] in state.overlays:
                content = state.overlays[g["name"]].content or content
            self._overlay_table.setItem(i, 2, QTableWidgetItem(content))

            paz_item = QTableWidgetItem(self._fmt_size(g["paz_size"]))
            paz_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._overlay_table.setItem(i, 3, paz_item)

            pamt_item = QTableWidgetItem(self._fmt_size(g["pamt_size"]))
            pamt_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._overlay_table.setItem(i, 4, pamt_item)

        parts = []
        if our_count:
            parts.append(f"{our_count} ours")
        if dmm_count:
            parts.append(f"{dmm_count} DMM")
        if other_count:
            parts.append(f"{other_count} other")
        self._overlay_summary.setText(
            f"{len(groups)} overlay(s): " + ", ".join(parts))

    def _poll_dmm_status(self) -> None:
        if self._dmm_process and self._dmm_process.poll() is not None:
            self._dmm_process = None
            self._refresh_all()

    @staticmethod
    def _fmt_size(n: int) -> str:
        if n == 0:
            return "—"
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n / (1024 * 1024):.1f} MB"

    @staticmethod
    def _num_item(val: int) -> QTableWidgetItem:
        item = QTableWidgetItem(str(val))
        item.setTextAlignment(Qt.AlignCenter)
        return item
