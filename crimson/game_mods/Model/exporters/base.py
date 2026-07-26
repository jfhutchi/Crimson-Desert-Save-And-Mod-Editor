"""Base class and result types for mesh exporters.

Plugin contract:
- Each exporter subclasses MeshExporter and sets format_id/format_name/file_extension.
- export_to_disk() receives a ParsedModel (format-agnostic) + ExportOptions.
- Returns ExportResult with output file paths, stats, and warnings.
- Register new exporters in exporters/__init__.py.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from model_types import ParsedModel


@dataclass
class ExportOptions:
    """Options shared across all export formats."""
    lod: int = 0
    name_hint: str = ""
    texture_rel_dir: str = ""
    available_textures: Optional[set] = None
    diffuse_overrides: Optional[dict] = None


@dataclass
class ExportWarning:
    severity: str    # "info", "warning", "error"
    source: str      # "texture", "geometry", "material"
    message: str


@dataclass
class ExportResult:
    success: bool
    output_files: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    warnings: list[ExportWarning] = field(default_factory=list)
    textures_extracted: int = 0
    textures_expected: int = 0


class MeshExporter(ABC):
    format_id: str = ""
    format_name: str = ""
    file_extension: str = ""

    @abstractmethod
    def export_to_disk(self, model: ParsedModel, output_dir: str,
                       options: ExportOptions = None) -> ExportResult:
        """Export model to disk. Options default to ExportOptions() if None."""
        ...

    def export_to_bytes(self, model: ParsedModel, **kwargs) -> bytes:
        raise NotImplementedError(f"{self.format_name} doesn't support in-memory export")
