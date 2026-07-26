"""PAB skeleton parser for Crimson Desert.

Parses .pab files to extract bone hierarchies with names, parent indices,
and transform matrices. Used to add armature data to PAC mesh exports.

PAB format (PAR v5.1):
  Header: 22 bytes
    [0x00] 4B  magic "PAR "
    [0x04] 4B  version (u16 major, u16 minor) — observed: 0x01, 0x05, 0x00, 0x01
    [0x08] 12B hash/flags (observed: sequential 0x02..0x0F then zeros)
    [0x14] 2B  bone_count (uint16 LE)

  Per bone (variable size = 305 + name_len):
    [4B]  bone_hash (uint32 LE)
    [1B]  name_length (uint8)
    [NB]  bone_name (ASCII, length-prefixed, NOT null-terminated)
    [4B]  parent_index (int32 LE, -1 = root)
    [64B] bind_matrix (4x4 float32, row-major)
    [64B] inverse_bind_matrix (4x4 float32)
    [64B] bind_matrix_copy
    [64B] inverse_bind_matrix_copy
    [12B] scale (3x float32)
    [16B] rotation_quaternion (4x float32: x, y, z, w)
    [12B] position (3x float32)

  After all bones: remaining bytes are skeleton metadata (constraints, IK, etc.)

Verified against:
  - blackstar_cd_m0004_00_dragon.pab (274 bones, 88950 bytes)
  - goldstar_cd_m0004_00_golemdragon.pab (428 bones, 138915 bytes)
  - Stride confirmed: bone 0 "Bip01" (5 chars) = 310 bytes, bone 1 (16 chars) = 321 bytes
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Optional

import logging

logger = logging.getLogger("Model.pab_skeleton_parser")

PAR_MAGIC = b"PAR "
HEADER_SIZE = 0x16  # 22 bytes: magic(4) + version(4) + hash(12) + bone_count(2)
BONE_FIXED_SIZE = 305  # 4(hash) + 1(name_len) + 4(parent) + 256(matrices) + 12(scale) + 16(rot) + 12(pos)


@dataclass
class Bone:
    """A single bone in the skeleton hierarchy."""
    index: int = 0
    name: str = ""
    name_hash: int = 0
    parent_index: int = -1
    bind_matrix: tuple = ()       # 16 floats (4x4 row-major)
    inv_bind_matrix: tuple = ()   # 16 floats
    bind_matrix2: tuple = ()      # 16 floats (copy)
    inv_bind_matrix2: tuple = ()  # 16 floats (copy)
    scale: tuple = (1.0, 1.0, 1.0)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)  # quaternion xyzw
    position: tuple = (0.0, 0.0, 0.0)
    # Raw byte range in source file
    file_offset: int = 0
    file_end: int = 0

    @property
    def raw_size(self) -> int:
        return BONE_FIXED_SIZE + len(self.name)


@dataclass
class Skeleton:
    """Parsed skeleton with bone hierarchy."""
    path: str = ""
    bones: list[Bone] = field(default_factory=list)
    bone_count: int = 0
    tail_data: bytes = b""  # data after bone list (constraints, IK, etc.)
    tail_offset: int = 0

    def get_bone_by_name(self, name: str) -> Optional[Bone]:
        for b in self.bones:
            if b.name == name:
                return b
        return None

    def get_children(self, bone_index: int) -> list[Bone]:
        return [b for b in self.bones if b.parent_index == bone_index]

    def get_root_bones(self) -> list[Bone]:
        return [b for b in self.bones if b.parent_index == -1]

    def find_bone_index(self, name: str) -> int:
        """Return index of bone by name, or -1 if not found."""
        for b in self.bones:
            if b.name == name:
                return b.index
        return -1


def _read_bone(data: bytes, off: int, index: int) -> tuple[Bone, int]:
    """Read a single bone record starting at offset. Returns (Bone, next_offset)."""
    start = off
    bone = Bone(index=index, file_offset=start)

    # Hash (4 bytes)
    bone.name_hash = struct.unpack_from('<I', data, off)[0]
    off += 4

    # Name: length-prefixed (1 byte length + N bytes ASCII)
    name_len = data[off]
    off += 1
    bone.name = data[off:off + name_len].decode('ascii', 'replace')
    off += name_len

    # Parent index (int32, -1 = root)
    bone.parent_index = struct.unpack_from('<i', data, off)[0]
    off += 4

    # 4 matrices: bind, inverse_bind, bind_copy, inverse_bind_copy (each 4x4 float = 64B)
    bone.bind_matrix = struct.unpack_from('<16f', data, off); off += 64
    bone.inv_bind_matrix = struct.unpack_from('<16f', data, off); off += 64
    bone.bind_matrix2 = struct.unpack_from('<16f', data, off); off += 64
    bone.inv_bind_matrix2 = struct.unpack_from('<16f', data, off); off += 64

    # Scale (3 floats)
    bone.scale = struct.unpack_from('<fff', data, off); off += 12

    # Rotation quaternion (4 floats: x, y, z, w)
    bone.rotation = struct.unpack_from('<ffff', data, off); off += 16

    # Position (3 floats)
    bone.position = struct.unpack_from('<fff', data, off); off += 12

    bone.file_end = off
    return bone, off


def parse_pab(data: bytes, filename: str = "") -> Skeleton:
    """Parse a .pab skeleton file.

    Returns a Skeleton with bone names, parent indices, and transforms.
    """
    if len(data) < HEADER_SIZE or data[:4] != PAR_MAGIC:
        raise ValueError(f"Not a valid PAB file: {data[:4]!r}")

    skeleton = Skeleton(path=filename)

    # Bone count at offset 0x14 as uint16 LE
    bone_count = struct.unpack_from('<H', data, 0x14)[0]
    skeleton.bone_count = bone_count

    if bone_count == 0:
        return skeleton

    off = HEADER_SIZE
    for i in range(bone_count):
        if off + 10 > len(data):  # minimum: hash(4) + len(1) + name(1) + parent(4)
            logger.warning("PAB %s: truncated at bone %d, offset 0x%X", filename, i, off)
            break

        bone, off = _read_bone(data, off, i)
        skeleton.bones.append(bone)

    # Everything after the bone list is tail data (constraints, IK, etc.)
    skeleton.tail_offset = off
    skeleton.tail_data = data[off:]

    logger.info("Parsed PAB %s: %d bones, %d bytes tail data",
                filename, len(skeleton.bones), len(skeleton.tail_data))
    return skeleton


def serialize_pab(skeleton: Skeleton, original_header: bytes = None) -> bytes:
    """Serialize a Skeleton back to .pab binary format.

    Args:
        skeleton: The skeleton to serialize.
        original_header: If provided, use the first 0x14 bytes from this
                        (preserves magic/version/hash). Otherwise uses defaults.
    """
    # Header
    if original_header and len(original_header) >= 0x14:
        header = bytearray(original_header[:0x14])
    else:
        header = bytearray(b'PAR \x01\x05\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0f\x00\x00\x00')
    header += struct.pack('<H', len(skeleton.bones))

    # Bone data
    bone_data = bytearray()
    for bone in skeleton.bones:
        bone_data += struct.pack('<I', bone.name_hash)
        name_bytes = bone.name.encode('ascii')
        bone_data += struct.pack('B', len(name_bytes))
        bone_data += name_bytes
        bone_data += struct.pack('<i', bone.parent_index)
        bone_data += struct.pack('<16f', *bone.bind_matrix)
        bone_data += struct.pack('<16f', *bone.inv_bind_matrix)
        bone_data += struct.pack('<16f', *bone.bind_matrix2)
        bone_data += struct.pack('<16f', *bone.inv_bind_matrix2)
        bone_data += struct.pack('<3f', *bone.scale)
        bone_data += struct.pack('<4f', *bone.rotation)
        bone_data += struct.pack('<3f', *bone.position)

    return bytes(header) + bytes(bone_data) + skeleton.tail_data


def inject_bone(target_skel: Skeleton, source_bone: Bone,
                parent_name: str = None, parent_index: int = None) -> Bone:
    """Inject a bone from one skeleton into another.

    The bone is appended at the end of the bone list. Parent index is
    resolved by name in the target skeleton if parent_name is given,
    otherwise parent_index is used directly.

    Returns the newly added Bone (with updated index and parent).
    """
    new_bone = Bone(
        index=len(target_skel.bones),
        name=source_bone.name,
        name_hash=source_bone.name_hash,
        bind_matrix=source_bone.bind_matrix,
        inv_bind_matrix=source_bone.inv_bind_matrix,
        bind_matrix2=source_bone.bind_matrix2,
        inv_bind_matrix2=source_bone.inv_bind_matrix2,
        scale=source_bone.scale,
        rotation=source_bone.rotation,
        position=source_bone.position,
    )

    if parent_name is not None:
        idx = target_skel.find_bone_index(parent_name)
        if idx < 0:
            raise ValueError(f"Parent bone '{parent_name}' not found in target skeleton")
        new_bone.parent_index = idx
    elif parent_index is not None:
        new_bone.parent_index = parent_index
    else:
        new_bone.parent_index = source_bone.parent_index

    target_skel.bones.append(new_bone)
    target_skel.bone_count = len(target_skel.bones)
    return new_bone


def find_matching_pab(pac_path: str, pamt_entries) -> Optional[str]:
    """Find a .pab file matching a .pac file path."""
    stem = pac_path.lower().replace('.pac', '')
    for entry in pamt_entries:
        if entry.path.lower().replace('.pab', '') == stem:
            return entry.path
    return None


def is_skeleton_file(path: str) -> bool:
    """Check if a file is a skeleton file."""
    return os.path.splitext(path.lower())[1] == ".pab"
