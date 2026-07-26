"""Core data types for parsed BlackSpace Engine mesh models.

Shared intermediate representation consumed by parsers, exporters,
and the browser viewer. Format-agnostic — both PAC (skinned) and
PAM (static) parsers produce ParsedModel instances.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np


class SourceFormat(Enum):
    PAC = "pac"
    PAM = "pam"


@dataclass
class BoundingBox:
    center: tuple[float, float, float]
    half_extent: tuple[float, float, float]

    @staticmethod
    def from_min_max(bmin: tuple, bmax: tuple) -> "BoundingBox":
        center = tuple((a + b) / 2.0 for a, b in zip(bmin, bmax))
        half_ext = tuple((b - a) / 2.0 for a, b in zip(bmin, bmax))
        return BoundingBox(center, half_ext)

    def union(self, other: "BoundingBox") -> "BoundingBox":
        s_min = tuple(c - h for c, h in zip(self.center, self.half_extent))
        s_max = tuple(c + h for c, h in zip(self.center, self.half_extent))
        o_min = tuple(c - h for c, h in zip(other.center, other.half_extent))
        o_max = tuple(c + h for c, h in zip(other.center, other.half_extent))
        new_min = tuple(min(a, b) for a, b in zip(s_min, o_min))
        new_max = tuple(max(a, b) for a, b in zip(s_max, o_max))
        return BoundingBox.from_min_max(new_min, new_max)


@dataclass
class VertexBuffer:
    positions: np.ndarray   # (N, 3) float32
    normals: np.ndarray     # (N, 3) float32
    uvs: np.ndarray         # (N, 2) float32 — raw game UVs, no V-flip
    tangents: Optional[np.ndarray] = None      # (N, 4) float32 — xyz + handedness w
    bone_indices: Optional[np.ndarray] = None  # (N, 8) uint16 — 4 from set A + 4 from set B
    bone_weights: Optional[np.ndarray] = None  # (N, 8) float32 — normalized to sum=1

    @property
    def count(self) -> int:
        return len(self.positions)


@dataclass
class IndexBuffer:
    indices: np.ndarray     # (M,) uint32

    @property
    def count(self) -> int:
        return len(self.indices)


@dataclass
class Bone:
    name: str
    parent_index: int                       # -1 = root
    inverse_bind_matrix: np.ndarray         # (4, 4) float32 — for skinning
    local_matrix: np.ndarray                # (4, 4) float32 — local-space transform
    position: np.ndarray                    # (3,) float32
    rotation: np.ndarray                    # (4,) float32 — quaternion XYZW
    scale: np.ndarray                       # (3,) float32


@dataclass
class SubMesh:
    name: str
    material_name: str
    texture_basename: str
    lods: dict[int, tuple[VertexBuffer, IndexBuffer]]
    bbox: BoundingBox
    descriptor_type: Optional[int] = None
    vertex_stride: int = 40
    uses_shared_buffer: bool = False

    def best_lod(self) -> int:
        return min(self.lods.keys())

    def get_geometry(self, lod: int = 0) -> Optional[tuple[VertexBuffer, IndexBuffer]]:
        return self.lods.get(lod)


@dataclass
class ParsedModel:
    source_format: SourceFormat
    format_version: int
    submeshes: list[SubMesh]
    bbox: BoundingBox
    warnings: list[str] = field(default_factory=list)
    sections: Optional[list[dict]] = None
    bones: Optional[list] = None
    raw_size: int = 0
    lod_count: int = 1
    available_lods: list[int] = field(default_factory=lambda: [0])
