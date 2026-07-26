"""PAC skinned mesh parser for BlackSpace Engine (Crimson Desert).

Extracts mesh descriptors, geometry, and materials from PAC binary files
and returns a ParsedModel. Consolidates all PAC-specific parsing logic
into a single class with one public entry point: parse().

Module-level functions:
    decompress_type1_pac() - type 1 internal LZ4 decompression
    material_to_dds_basename() - material name to DDS filename conversion
"""

import struct

import numpy as np

try:
    import lz4.block
    HAS_LZ4 = True
except ImportError:
    HAS_LZ4 = False

from model_types import ParsedModel, SubMesh, VertexBuffer, IndexBuffer, BoundingBox, SourceFormat, Bone
from pac_decode import decode_pac_vertices, decode_indices, PAC_STRIDE


# ── Module-level functions (used by callers before/after parsing) ──


def decompress_type1_pac(raw_data: bytes, orig_size: int) -> bytes:
    """Decompress a type 1 PAC file with internal section-level LZ4.

    Type 1 files store the 80-byte header uncompressed, but individual
    sections may be LZ4 block compressed. The section size table uses
    (u32 comp_size, u32 decomp_size) per slot.
    """
    if not HAS_LZ4:
        raise RuntimeError("lz4 package required for type 1 decompression: pip install lz4")

    output = bytearray(raw_data[:0x50])
    file_offset = 0x50

    for slot in range(8):
        off = 0x10 + slot * 8
        comp = struct.unpack_from('<I', raw_data, off)[0]
        decomp = struct.unpack_from('<I', raw_data, off + 4)[0]
        if decomp == 0:
            continue
        if comp > 0:
            blob = raw_data[file_offset:file_offset + comp]
            output.extend(lz4.block.decompress(blob, uncompressed_size=decomp))
            file_offset += comp
        else:
            output.extend(raw_data[file_offset:file_offset + decomp])
            file_offset += decomp

    for slot in range(8):
        struct.pack_into('<I', output, 0x10 + slot * 8, 0)

    return bytes(output)


def material_to_dds_basename(mat_name: str) -> str:
    """Convert material name to DDS texture base filename.

    PHW body parts (nude/head) need '_00_' inserted before the numeric ID.
    All other types map directly via lowercase.
    """
    lower = mat_name.lower()

    if lower.startswith('cd_phw_00_nude_') or lower.startswith('cd_phw_00_head_'):
        prefix_end = len('cd_phw_00_')
        rest = lower[prefix_end:]
        parts = rest.split('_')
        for i, p in enumerate(parts):
            if p.isdigit() and len(p) == 4:
                parts.insert(i, '00')
                break
        return 'cd_phw_00_' + '_'.join(parts)

    return lower


# ── Internal descriptor dataclass ──


class _MeshDescriptor:
    __slots__ = ('display_name', 'material_name', 'center', 'half_extent',
                 'vertex_counts', 'index_counts', 'attr_count', 'section0_offset')

    def __init__(self, display_name, material_name, center, half_extent,
                 vertex_counts, index_counts, attr_count, section0_offset):
        self.display_name = display_name
        self.material_name = material_name
        self.center = center
        self.half_extent = half_extent
        self.vertex_counts = vertex_counts
        self.index_counts = index_counts
        self.attr_count = attr_count
        self.section0_offset = section0_offset


# ── Parser class ──


class PacParser:
    """Parser for PAC skinned mesh format (PAR magic, version 0x01000903)."""

    FORMAT_NAME = "PAC (Skinned Mesh)"
    FILE_EXTENSIONS = [".pac"]
    VERSION = 0x01000903

    _ATTR4_PATTERN = bytes([0x04, 0x00, 0x01, 0x02, 0x03])
    _ATTR3_PATTERN = bytes([0x03, 0x00, 0x01, 0x02])
    _ATTR3_VARIANT = bytes([0x03, 0x00, 0x01, 0x01])
    _ATTR2_PATTERN = bytes([0x02, 0x00, 0x01])

    def can_parse(self, data: bytes) -> bool:
        return len(data) >= 8 and data[0:4] == b'PAR '

    def parse(self, data: bytes, lods: list[int] | None = None) -> ParsedModel:
        header = self._parse_header(data)

        # Detect still-compressed type 1 data
        for i in range(8):
            comp = struct.unpack_from('<I', data, 0x10 + i * 8)[0]
            decomp = struct.unpack_from('<I', data, 0x10 + i * 8 + 4)[0]
            if comp > 0 and decomp > 0:
                raise ValueError(
                    "PAC data has compressed sections (type 1). "
                    "Call decompress_type1_pac(data, orig_size) before parse().")

        sec_by_idx = {s['index']: s for s in header['sections']}

        if 0 not in sec_by_idx:
            raise ValueError("No section 0 (metadata) found")

        sec0 = sec_by_idx[0]
        descriptors = self._find_mesh_descriptors(data, sec0['offset'], sec0['size'])
        if not descriptors:
            raise ValueError("No mesh descriptors found in section 0")

        available_lods = []
        for lod_idx in range(4):
            geom_idx = 4 - lod_idx
            has_verts = any(d.vertex_counts[lod_idx] > 0 for d in descriptors)
            if geom_idx in sec_by_idx and has_verts:
                available_lods.append(lod_idx)

        if lods is None:
            lods = available_lods

        warnings = []
        all_submeshes = []

        for lod in lods:
            geom_idx = 4 - lod
            if geom_idx not in sec_by_idx:
                warnings.append(f"No geometry section for LOD {lod} (section {geom_idx})")
                continue

            lod_submeshes, lod_warnings = self._build_submeshes(
                data, sec_by_idx, descriptors, lod)
            warnings.extend(lod_warnings)

            for sm in lod_submeshes:
                existing = next((s for s in all_submeshes
                                 if s.name == sm.name and s.material_name == sm.material_name), None)
                if existing is not None:
                    existing.lods.update(sm.lods)
                else:
                    all_submeshes.append(sm)

        if not all_submeshes:
            raise ValueError("No meshes with geometry found")

        bbox = all_submeshes[0].bbox
        for sm in all_submeshes[1:]:
            bbox = bbox.union(sm.bbox)

        # Parse bones from section 0 (Format 1: named bones only)
        bones = self._parse_bones(data, sec0, descriptors, warnings)

        return ParsedModel(
            source_format=SourceFormat.PAC,
            format_version=header['version'],
            submeshes=all_submeshes,
            bbox=bbox,
            warnings=warnings,
            sections=header['sections'],
            bones=bones,
            raw_size=len(data),
            lod_count=len(available_lods),
            available_lods=available_lods,
        )

    def _parse_header(self, data: bytes) -> dict:
        magic = data[0:4]
        if magic != b'PAR ':
            raise ValueError(f"Not a PAC file (magic: {magic!r}, expected b'PAR ')")

        version = struct.unpack_from('<I', data, 4)[0]

        sections = []
        offset = 0x50
        for i in range(8):
            slot_off = 0x10 + i * 8
            comp_size = struct.unpack_from('<I', data, slot_off)[0]
            decomp_size = struct.unpack_from('<I', data, slot_off + 4)[0]
            stored_size = comp_size if comp_size > 0 else decomp_size
            if decomp_size > 0:
                sections.append({'index': i, 'offset': offset, 'size': decomp_size})
                offset += stored_size

        return {'version': version, 'sections': sections}

    def _find_mesh_descriptors(self, data: bytes, sec0_offset: int,
                               sec0_size: int) -> list[_MeshDescriptor]:
        region = data[sec0_offset:sec0_offset + sec0_size]
        found = []

        # 4-attribute descriptors (standard meshes, 4 LODs)
        pos = 0
        while True:
            idx = region.find(self._ATTR4_PATTERN, pos)
            if idx == -1:
                break
            desc_start = idx - 35
            if desc_start >= 0 and region[desc_start] == 0x01:
                floats = struct.unpack_from('<8f', region, desc_start + 3)
                vc = [struct.unpack_from('<H', region, desc_start + 40 + i * 2)[0]
                      for i in range(4)]
                ic = [struct.unpack_from('<I', region, desc_start + 48 + i * 4)[0]
                      for i in range(4)]
                names = self._find_name_strings(region, desc_start)
                found.append((desc_start, _MeshDescriptor(
                    display_name=names[0], material_name=names[1],
                    center=(floats[2], floats[3], floats[4]),
                    half_extent=(floats[5], floats[6], floats[7]),
                    vertex_counts=vc, index_counts=ic,
                    attr_count=4, section0_offset=desc_start,
                )))
            pos = idx + 5

        # 3-attribute descriptors (cloth/sim meshes, 3 LODs)
        for attr3_pattern in (self._ATTR3_PATTERN, self._ATTR3_VARIANT):
            pos = 0
            while True:
                idx = region.find(attr3_pattern, pos)
                if idx == -1:
                    break
                desc_start = idx - 35
                if desc_start >= 0 and region[desc_start] == 0x01:
                    if idx >= 1 and region[idx - 1] == 0x04:
                        pos = idx + 4
                        continue
                    floats = struct.unpack_from('<8f', region, desc_start + 3)
                    vc3 = [struct.unpack_from('<H', region, desc_start + 40 + i * 2)[0]
                           for i in range(3)]
                    ic3 = [struct.unpack_from('<I', region, desc_start + 46 + i * 4)[0]
                           for i in range(3)]
                    vc = vc3 + [0]
                    ic = ic3 + [0]
                    names = self._find_name_strings(region, desc_start)
                    found.append((desc_start, _MeshDescriptor(
                        display_name=names[0], material_name=names[1],
                        center=(floats[2], floats[3], floats[4]),
                        half_extent=(floats[5], floats[6], floats[7]),
                        vertex_counts=vc, index_counts=ic,
                        attr_count=3, section0_offset=desc_start,
                    )))
                pos = idx + 4

        # 2-attribute descriptors (accessories/physics meshes, 2 LODs)
        pos = 0
        while True:
            idx = region.find(self._ATTR2_PATTERN, pos)
            if idx == -1:
                break
            desc_start = idx - 35
            if desc_start >= 0 and region[desc_start] == 0x01:
                if idx >= 1 and region[idx - 1] in (0x03, 0x04):
                    pos = idx + 3
                    continue
                floats = struct.unpack_from('<8f', region, desc_start + 3)
                vc2 = [struct.unpack_from('<H', region, desc_start + 40 + i * 2)[0]
                       for i in range(2)]
                ic2 = [struct.unpack_from('<I', region, desc_start + 44 + i * 4)[0]
                       for i in range(2)]
                if vc2[0] > 50000 or ic2[0] > 500000:
                    pos = idx + 3
                    continue
                vc = vc2 + [0, 0]
                ic = ic2 + [0, 0]
                names = self._find_name_strings(region, desc_start)
                found.append((desc_start, _MeshDescriptor(
                    display_name=names[0], material_name=names[1],
                    center=(floats[2], floats[3], floats[4]),
                    half_extent=(floats[5], floats[6], floats[7]),
                    vertex_counts=vc, index_counts=ic,
                    attr_count=2, section0_offset=desc_start,
                )))
            pos = idx + 3

        found.sort(key=lambda x: x[0])
        return [desc for _, desc in found]

    def _find_name_strings(self, region: bytes, desc_start: int) -> tuple[str, str]:
        names = []
        cursor = desc_start

        for _ in range(2):
            found = False
            for back in range(1, 200):
                candidate_len = region[cursor - back]
                if candidate_len == 0:
                    continue
                if candidate_len == back - 1:
                    name_bytes = region[cursor - back + 1:cursor]
                    try:
                        name = name_bytes.decode('ascii')
                        if all(32 <= c < 127 for c in name_bytes):
                            names.append(name)
                            cursor = cursor - back
                            found = True
                            break
                    except (UnicodeDecodeError, ValueError):
                        continue
            if not found:
                names.append(f"unknown_{desc_start:x}")

        names.reverse()
        return (names[0], names[1])

    def _find_section_layout(self, data: bytes, geom_sec: dict,
                             descriptors: list[_MeshDescriptor], lod: int,
                             total_indices: int) -> tuple[int, int]:
        """Returns (vert_start, index_start) as byte offsets within the section."""
        sec_off = geom_sec['offset']
        sec_size = geom_sec['size']
        total_verts = sum(d.vertex_counts[lod] for d in descriptors)

        primary_bytes = total_verts * PAC_STRIDE
        index_bytes = total_indices * 2
        if primary_bytes + index_bytes >= sec_size:
            return 0, primary_bytes

        gap = sec_size - primary_bytes - index_bytes
        if gap <= 0:
            return 0, primary_bytes

        first_vc = next((d.vertex_counts[lod] for d in descriptors
                         if d.vertex_counts[lod] > 0), 0)
        if first_vc == 0:
            return 0, primary_bytes

        secondary_bytes = (gap // PAC_STRIDE) * PAC_STRIDE
        first_desc = next(d for d in descriptors if d.vertex_counts[lod] > 0)

        def _scan_idx_start(after_verts):
            for adj in range(0, sec_size - after_verts, 2):
                t = after_verts + adj
                if t + 6 > sec_size:
                    break
                if struct.unpack_from('<H', data, sec_off + t)[0] == 0:
                    v1 = struct.unpack_from('<H', data, sec_off + t + 2)[0]
                    v2 = struct.unpack_from('<H', data, sec_off + t + 4)[0]
                    if v1 < first_vc and v2 < first_vc:
                        return t
            return None

        def _measure_quality(v_start, i_start):
            if i_start is None or i_start + total_indices * 2 > sec_size:
                return 999.0
            vb = decode_pac_vertices(data, sec_off, first_vc,
                                     first_desc.center, first_desc.half_extent,
                                     vertex_start=v_start)
            pos = vb.positions
            first_ic = next((d.index_counts[lod] for d in descriptors
                             if d.index_counts[lod] > 0), 0)
            n_tris = first_ic // 3
            sample_indices = list(range(0, n_tris, max(1, n_tris // 30)))[:30]
            total_edge = 0.0
            for t in sample_indices:
                i0 = struct.unpack_from('<H', data, sec_off + i_start + t * 6)[0]
                i1 = struct.unpack_from('<H', data, sec_off + i_start + t * 6 + 2)[0]
                i2 = struct.unpack_from('<H', data, sec_off + i_start + t * 6 + 4)[0]
                if max(i0, i1, i2) >= len(pos):
                    return 999.0
                p0, p1, p2 = pos[i0], pos[i1], pos[i2]
                total_edge += max(float(np.linalg.norm(p1 - p0)),
                                  float(np.linalg.norm(p2 - p1)),
                                  float(np.linalg.norm(p0 - p2)))
            return total_edge

        best_vs = 0
        best_is = primary_bytes + secondary_bytes
        best_q = (_measure_quality(0, best_is)
                  if best_is + total_indices * 2 <= sec_size else 999.0)

        for n_sec in range(0, gap // PAC_STRIDE + 1):
            vs = n_sec * PAC_STRIDE
            all_end = vs + primary_bytes
            if all_end >= sec_size:
                break
            idx = _scan_idx_start(all_end)
            if idx is None or idx + total_indices * 2 > sec_size:
                continue
            q = _measure_quality(vs, idx)
            if q < best_q:
                best_q = q
                best_vs = vs
                best_is = idx

        return best_vs, best_is

    def _build_submeshes(self, data: bytes, sec_by_idx: dict,
                         descriptors: list[_MeshDescriptor],
                         lod: int) -> tuple[list[SubMesh], list[str]]:
        warnings = []
        geom_idx = 4 - lod
        geom_sec = sec_by_idx[geom_idx]

        total_verts = sum(d.vertex_counts[lod] for d in descriptors)
        total_indices = sum(d.index_counts[lod] for d in descriptors)

        vert_base, index_byte_offset = self._find_section_layout(
            data, geom_sec, descriptors, lod, total_indices)

        primary_bytes = total_verts * PAC_STRIDE
        index_bytes = total_indices * 2
        sec_size = geom_sec['size']
        if primary_bytes + index_bytes < sec_size:
            gap = sec_size - primary_bytes - index_bytes
            warnings.append(
                f"LOD {lod}: gap of {gap} bytes in geometry section "
                f"(secondary physics data, heuristic layout used)")

        # Precompute per-descriptor vertex byte offsets
        desc_vert_offsets = []
        off = vert_base
        for d in descriptors:
            desc_vert_offsets.append(off)
            off += d.vertex_counts[lod] * PAC_STRIDE

        # First pass: detect shared buffers
        partner_map = {}
        idx_off_check = index_byte_offset
        for di, desc in enumerate(descriptors):
            ic = desc.index_counts[lod]
            vc = desc.vertex_counts[lod]
            if vc == 0:
                idx_off_check += ic * 2
                continue
            raw_ib = decode_indices(data, geom_sec['offset'] + idx_off_check, ic)
            max_idx = int(raw_ib.indices.max()) if raw_ib.count > 0 else 0
            if max_idx >= vc:
                for pj, pd in enumerate(descriptors):
                    if pd.vertex_counts[lod] > max_idx and pj != di:
                        partner_map[di] = pj
                        break
            idx_off_check += ic * 2

        # Second pass: build SubMesh instances
        submeshes = []
        idx_byte_cursor = index_byte_offset

        for di, desc in enumerate(descriptors):
            vc = desc.vertex_counts[lod]
            ic = desc.index_counts[lod]
            if vc == 0:
                idx_byte_cursor += ic * 2
                continue

            ib = decode_indices(data, geom_sec['offset'] + idx_byte_cursor, ic)

            is_shared = di in partner_map
            if is_shared:
                pj = partner_map[di]
                vb = decode_pac_vertices(
                    data, geom_sec['offset'],
                    descriptors[pj].vertex_counts[lod],
                    desc.center, desc.half_extent,
                    vertex_start=desc_vert_offsets[pj])
            else:
                vb = decode_pac_vertices(
                    data, geom_sec['offset'], vc,
                    desc.center, desc.half_extent,
                    vertex_start=desc_vert_offsets[di])

            tex_base = ""
            if desc.material_name != "(null)":
                tex_base = material_to_dds_basename(desc.material_name)

            bbox = BoundingBox(center=desc.center, half_extent=desc.half_extent)

            submeshes.append(SubMesh(
                name=desc.display_name,
                material_name=desc.material_name,
                texture_basename=tex_base,
                lods={lod: (vb, ib)},
                bbox=bbox,
                descriptor_type=desc.attr_count,
                vertex_stride=PAC_STRIDE,
                uses_shared_buffer=is_shared,
            ))

            idx_byte_cursor += ic * 2

        return submeshes, warnings

    def _parse_bones(self, data: bytes, sec0: dict, descriptors: list,
                     warnings: list) -> list[Bone] | None:
        """Parse bone hierarchy from section 0 (Format 1: named bones only).

        Format 1 is detected by sec0[0] & 0x01 (weapons, chains, attachments).
        Format 2 (body/armor, ~85% of files) stores compact unnamed entries —
        the actual skeleton lives in external skeleton definition files.
        """
        sec0_off = sec0['offset']
        sec0_data = data[sec0_off:sec0_off + sec0['size']]

        # Only Format 1 has named bones
        if not (sec0_data[0] & 0x01):
            return None

        # Find end of last mesh descriptor
        desc_sizes = {4: 64, 3: 58, 2: 52}
        last_end = 0
        for desc in descriptors:
            desc_size = desc_sizes.get(desc.attr_count, 64)
            end = desc.section0_offset + desc_size
            if end > last_end:
                last_end = end

        off = last_end
        if off + 4 >= len(sec0_data):
            return None

        # Read bone count (u32 for Format 1)
        bone_count = struct.unpack_from('<I', sec0_data, off)[0]
        off += 4

        if bone_count == 0 or bone_count > 1000:
            return None

        bones = []
        try:
            for _ in range(bone_count):
                if off + 5 > len(sec0_data):
                    break
                _hash = struct.unpack_from('<I', sec0_data, off)[0]
                off += 4
                name_len = sec0_data[off]
                off += 1
                if off + name_len > len(sec0_data):
                    break
                # Validate name is ASCII printable
                name_bytes = sec0_data[off:off + name_len]
                if not all(32 <= b < 127 for b in name_bytes):
                    break
                name = name_bytes.decode('ascii')
                off += name_len
                if off + 4 + 256 + 40 > len(sec0_data):
                    break
                parent_idx = struct.unpack_from('<i', sec0_data, off)[0]
                off += 4

                # 4 matrices: world, inv_world, local, inv_local (each 4x4 float32)
                world = np.frombuffer(sec0_data, dtype='<f4', count=16,
                                      offset=off).reshape(4, 4).copy()
                off += 64
                inv_world = np.frombuffer(sec0_data, dtype='<f4', count=16,
                                          offset=off).reshape(4, 4).copy()
                off += 64
                local = np.frombuffer(sec0_data, dtype='<f4', count=16,
                                      offset=off).reshape(4, 4).copy()
                off += 64
                _inv_local = np.frombuffer(sec0_data, dtype='<f4', count=16,
                                           offset=off).reshape(4, 4).copy()
                off += 64

                # Decomposed transform: scale(3) + quaternion(4) + position(3)
                scale = np.frombuffer(sec0_data, dtype='<f4', count=3,
                                      offset=off).copy()
                off += 12
                quat = np.frombuffer(sec0_data, dtype='<f4', count=4,
                                     offset=off).copy()
                off += 16
                pos = np.frombuffer(sec0_data, dtype='<f4', count=3,
                                    offset=off).copy()
                off += 12

                bones.append(Bone(
                    name=name,
                    parent_index=parent_idx,
                    inverse_bind_matrix=inv_world,
                    local_matrix=local,
                    position=pos,
                    rotation=quat,
                    scale=scale,
                ))
        except Exception:
            if not bones:
                return None
            warnings.append(f"Bone parsing stopped after {len(bones)}/{bone_count} bones")

        return bones if bones else None
