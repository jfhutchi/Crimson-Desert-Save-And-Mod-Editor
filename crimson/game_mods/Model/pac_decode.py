"""Numpy-accelerated vertex and index decode for BlackSpace Engine meshes."""

import numpy as np
from model_types import VertexBuffer, IndexBuffer

PAC_STRIDE = 40


def _decode_r10g10b10a2(packed: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Decode R10G10B10A2 packed u32 into (xyz float32, a 2-bit).

    Returns (N,3) float32 in [-1,1] with game axis permutation (G,B,R) and (N,) uint8 alpha.
    """
    r_raw = (packed >> 0) & 0x3FF
    g_raw = (packed >> 10) & 0x3FF
    b_raw = (packed >> 20) & 0x3FF
    a_raw = (packed >> 30) & 0x3
    xyz = np.empty((len(packed), 3), dtype=np.float32)
    xyz[:, 0] = g_raw / 511.5 - 1.0  # x = channel G
    xyz[:, 1] = b_raw / 511.5 - 1.0  # y = channel B
    xyz[:, 2] = r_raw / 511.5 - 1.0  # z = channel R
    return xyz, a_raw.astype(np.uint8)


def decode_pac_vertices(data: bytes, section_offset: int, vertex_count: int,
                        center: tuple, half_extent: tuple,
                        vertex_start: int = 0) -> VertexBuffer:
    """Decode PAC vertices (40-byte stride) into numpy arrays.

    Reads all known attributes: position, UV, normal, tangent, bone indices, bone weights.
    """
    n = vertex_count
    base = section_offset + vertex_start

    buf = np.frombuffer(data, dtype=np.uint8, count=n * PAC_STRIDE, offset=base)
    vert = buf.reshape(n, PAC_STRIDE)

    # Position: 3 x uint16 at +0, dequantize with center/half_extent / 32767
    pos_u16 = vert[:, 0:6].copy().view('<u2')  # (n, 3)
    cx, cy, cz = center
    hx, hy, hz = half_extent
    positions = np.empty((n, 3), dtype=np.float32)
    positions[:, 0] = cx + (pos_u16[:, 0] / 32767.0) * hx
    positions[:, 1] = cy + (pos_u16[:, 1] / 32767.0) * hy
    positions[:, 2] = cz + (pos_u16[:, 2] / 32767.0) * hz

    # UV: 2 x float16 at +8
    uv_f16 = vert[:, 8:12].copy().view('<f2')  # (n, 2)
    uvs = uv_f16.astype(np.float32)

    # Tangent: R10G10B10A2 at +12 (same packing as normal, 2-bit alpha = handedness)
    tan_packed = vert[:, 12:16].copy().view('<u4').ravel()
    tan_xyz, tan_a = _decode_r10g10b10a2(tan_packed)
    tangents = np.empty((n, 4), dtype=np.float32)
    tangents[:, :3] = tan_xyz
    # Map 2-bit alpha to handedness: 0→+1, 1→+1, 2→-1, 3→-1 (common convention)
    tangents[:, 3] = np.where(tan_a >= 2, -1.0, 1.0)

    # Normal: R10G10B10A2 at +16, axes permuted (G, B, R)
    nor_packed = vert[:, 16:20].copy().view('<u4').ravel()
    normals, _ = _decode_r10g10b10a2(nor_packed)

    # Bone indices: R10G10B10A2 at +20 (set A) and +24 (set B)
    bone_a = vert[:, 20:24].copy().view('<u4').ravel()
    bone_b = vert[:, 24:28].copy().view('<u4').ravel()
    bone_indices = np.empty((n, 8), dtype=np.uint16)
    bone_indices[:, 0] = (bone_a >> 0) & 0x3FF
    bone_indices[:, 1] = (bone_a >> 10) & 0x3FF
    bone_indices[:, 2] = (bone_a >> 20) & 0x3FF
    bone_indices[:, 3] = (bone_a >> 30) & 0x3
    bone_indices[:, 4] = (bone_b >> 0) & 0x3FF
    bone_indices[:, 5] = (bone_b >> 10) & 0x3FF
    bone_indices[:, 6] = (bone_b >> 20) & 0x3FF
    bone_indices[:, 7] = (bone_b >> 30) & 0x3

    # Bone weights: 4 x uint8 at +28 (set A) and +32 (set B), normalized
    weight_a = vert[:, 28:32].astype(np.float32)  # (n, 4)
    weight_b = vert[:, 32:36].astype(np.float32)  # (n, 4)
    bone_weights = np.empty((n, 8), dtype=np.float32)
    bone_weights[:, :4] = weight_a
    bone_weights[:, 4:] = weight_b
    weight_sums = bone_weights.sum(axis=1, keepdims=True)
    weight_sums = np.maximum(weight_sums, 1e-8)
    bone_weights /= weight_sums  # normalize to sum=1

    return VertexBuffer(positions=positions, normals=normals, uvs=uvs,
                        tangents=tangents, bone_indices=bone_indices,
                        bone_weights=bone_weights)


def decode_pam_vertices(data: bytes, geom_offset: int, byte_offset: int,
                        vertex_count: int, bbox_min: tuple, bbox_max: tuple,
                        stride: int = 20) -> VertexBuffer:
    """Decode PAM vertices (variable stride) into numpy arrays."""
    n = vertex_count
    base = geom_offset + byte_offset
    extent = (bbox_max[0] - bbox_min[0],
              bbox_max[1] - bbox_min[1],
              bbox_max[2] - bbox_min[2])

    buf = np.frombuffer(data, dtype=np.uint8, count=n * stride, offset=base)
    vert = buf.reshape(n, stride)

    # Position: 3 x uint16 at +0, dequantize with bbox_min + u16/65535 * extent
    pos_u16 = vert[:, 0:6].copy().view('<u2')  # (n, 3)
    positions = np.empty((n, 3), dtype=np.float32)
    positions[:, 0] = bbox_min[0] + (pos_u16[:, 0] / 65535.0) * extent[0]
    positions[:, 1] = bbox_min[1] + (pos_u16[:, 1] / 65535.0) * extent[1]
    positions[:, 2] = bbox_min[2] + (pos_u16[:, 2] / 65535.0) * extent[2]

    # UV: 2 x float16 at +8 (if stride >= 12)
    if stride >= 12:
        uv_f16 = vert[:, 8:12].copy().view('<f2')  # (n, 2)
        uvs = uv_f16.astype(np.float32)
    else:
        uvs = np.zeros((n, 2), dtype=np.float32)

    # Normal: R10G10B10A2 at +12 (if stride >= 16), same permutation as PAC
    if stride >= 16:
        packed = vert[:, 12:16].copy().view('<u4').ravel()  # (n,)
        r_raw = (packed >> 0) & 0x3FF
        g_raw = (packed >> 10) & 0x3FF
        b_raw = (packed >> 20) & 0x3FF
        normals = np.empty((n, 3), dtype=np.float32)
        normals[:, 0] = g_raw / 511.5 - 1.0
        normals[:, 1] = b_raw / 511.5 - 1.0
        normals[:, 2] = r_raw / 511.5 - 1.0
    else:
        normals = np.tile(np.array([0.0, 1.0, 0.0], dtype=np.float32), (n, 1))

    return VertexBuffer(positions=positions, normals=normals, uvs=uvs)


def decode_indices(data: bytes, offset: int, count: int,
                   index_size: int = 2) -> IndexBuffer:
    """Decode index buffer. index_size=2 for uint16, 4 for uint32."""
    dtype = '<u2' if index_size == 2 else '<u4'
    indices = np.frombuffer(data, dtype=dtype, count=count, offset=offset)
    return IndexBuffer(indices=indices.astype(np.uint32))
