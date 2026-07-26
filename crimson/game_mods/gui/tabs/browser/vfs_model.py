from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from crimson.game_mods import dmm_parser as dmm
import logging
from crimson.game_mods.gui.tabs.browser.vfs_node import VirtualNode

log = logging.getLogger(__name__)


class VirtualFileSystemModel(QAbstractItemModel):
    def __init__(self, parent=None, dir="data"):
        super().__init__(parent)

        self.root_dir = dir
        self.root_node = VirtualNode("Root", is_dir=True)

        papgt = dmm.parse_papgt_file(f"{dir}/meta/0.papgt")

        self.top_level_groups: list[VirtualNode] = []

        for entry in papgt["entries"]:
            group_name = entry["group_name"]

            new_node = VirtualNode(
                group_name, is_dir=True, parent=self.root_node
            )
            new_node.language = VirtualNode.LANGUAGES[entry["language"]]
            self.top_level_groups.append(new_node)
            self.root_node.append_child(new_node)

        for group_node in self.top_level_groups:
            self._populate_children(group_node.absolute_path)
            group_node.is_populated = True
        self._sort_children(self.root_node)

    def _get_node_by_path(self, base_path: str) -> VirtualNode | None:
        current_node = self.root_node

        if not base_path:
            return current_node

        segments = [s for s in base_path.split("/") if s]

        for segment in segments:
            found_child = False
            for child in current_node.child_items:
                if child.name == segment and child.is_dir:
                    current_node = child
                    found_child = True
                    break

            if not found_child:
                return None

        return current_node

    def _populate_children(self, absolute_path: str):

        if not absolute_path or "meta/0.papgt" in absolute_path:
            return

        try:
            pamt = dmm.parse_pamt_file(
                f"{self.root_dir}/{absolute_path}/0.pamt"
            )
        except Exception as e:
            log.warning(
                f"WARNING: Could not parse PAMT for {absolute_path}. Skipping. Error: {e}"
            )
            return

        if "directories" not in pamt:
            return

        base_node = self._get_node_by_path(absolute_path)
        if not base_node:
            return

        for dir_entry in pamt["directories"]:
            full_path = dir_entry["path"]
            segments = [s for s in full_path.split("/") if s]

            current_node = base_node
            start_idx = 0

            for i, segment in enumerate(segments):
                existing_child = None
                for child in current_node.child_items:
                    if child.name == segment:
                        existing_child = child
                        break

                if existing_child:
                    current_node = existing_child
                    start_idx = i + 1
                else:
                    break

            for segment in segments[start_idx:]:
                new_node = VirtualNode(segment, is_dir=True)
                new_node.parent_item = current_node
                current_node.append_child(new_node)
                current_node = new_node

            for file in dir_entry["files"]:
                new_node = VirtualNode(file["name"], is_dir=False)
                new_node.parent_item = current_node
                current_node.append_child(new_node)

    def _sort_children(self, node: VirtualNode):
        if not node.is_dir:
            return
        node.child_items.sort(key=lambda n: (not n.is_dir, n.name))
        for child in node.child_items:
            self._sort_children(child)

    def index(
        self, row: int, column: int, parent=QModelIndex()
    ) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        if not parent.isValid():
            parent_node = self.root_node
        else:
            parent_node = parent.internalPointer()

        child_node = parent_node.child(row)
        if child_node:
            return self.createIndex(row, column, child_node)
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()

        child_node = index.internalPointer()
        parent_node = child_node.parent_item

        if parent_node == self.root_node or parent_node is None:
            return QModelIndex()

        row = parent_node.row()
        return self.createIndex(row, 0, parent_node)

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.column() > 0:
            return 0

        if not parent.isValid():
            parent_node = self.root_node
        else:
            parent_node = parent.internalPointer()

        return parent_node.child_count() if parent_node else 0

    def columnCount(self, parent=QModelIndex()) -> int:
        return 2

    def data(
        self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if not index.isValid():
            return None

        node = index.internalPointer()

        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                if node in self.top_level_groups and node.language != "ALL":
                    return " ".join(
                        [
                            f"{node.name}",
                            f"({node.language})",
                        ]
                    )
                return node.name
            elif index.column() == 1:
                if node.is_dir:
                    return "Folder"

                for (
                    extension,
                    custom_type,
                ) in VirtualNode.KNOWN_FORMATS.items():
                    if node.name.endswith(extension):
                        return custom_type

                return "File"

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return ["Name", "Type"][section]
        return None
