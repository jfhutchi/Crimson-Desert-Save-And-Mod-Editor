"""PAM static mesh parser. Returns ParsedModel from model_types."""

import struct
from dataclasses import dataclass

try:
    import lz4.block
    HAS_LZ4 = True
except ImportError:
    HAS_LZ4 = False

from model_types import ParsedModel, SubMesh, BoundingBox, SourceFormat
from pac_decode import decode_pam_vertices, decode_indices


@dataclass
class PamSubmesh:
    nv: int
    ni: int
    voff: int
    ioff: int
    texture_name: str
    material_name: str


def decompress_pam_geometry(data: bytes) -> bytes:
    """Decompress PAM internal geometry block if LZ4 compressed."""
    comp_size = struct.unpack_from('<I', data, 0x44)[0]
    if comp_size == 0:
        return data

    if not HAS_LZ4:
        raise RuntimeError("lz4 package required: pip install lz4")

    geom_off = struct.unpack_from('<I', data, 0x3C)[0]
    decomp_size = struct.unpack_from('<I', data, 0x40)[0]

    decompressed = lz4.block.decompress(
        data[geom_off:geom_off + comp_size],
        uncompressed_size=decomp_size
    )

    output = bytearray(data[:geom_off])
    output.extend(decompressed)
    footer_start = geom_off + comp_size
    if footer_start < len(data):
        output.extend(data[footer_start:])

    struct.pack_into('<I', output, 0x44, 0)
    return bytes(output)


class PamParser:
    """Parser for PAM static mesh format (PAR magic, various versions)."""

    FORMAT_NAME = "PAM (Static Mesh)"
    FILE_EXTENSIONS = [".pam"]
    VERSIONS = {0x00001802, 0x00001803, 0x01001806}
    PAC_VERSION = 0x01000903
    SUBMESH_TABLE_OFF = 0x410
    SUBMESH_STRIDE = 0x218

    def can_parse(self, data: bytes) -> bool:
        if len(data) < 8 or data[0:4] != b'PAR ':
            return False
        version = struct.unpack_from('<I', data, 4)[0]
        return version in self.VERSIONS

    def parse(self, data: bytes, lods: list[int] | None = None) -> ParsedModel:
        data = decompress_pam_geometry(data)
        header = self._parse_header(data)
        submeshes = self._parse_submeshes(data, header['mesh_count'])
        if not submeshes:
            raise ValueError("No submeshes found")

        stride = self._detect_stride(header, submeshes)
        total_nv = sum(s.nv for s in submeshes)
        total_ni = sum(s.ni for s in submeshes)
        geom_off = header['geom_off']
        idx_byte_start = geom_off + total_nv * stride

        index_size = 4 if total_nv > 65535 else 2
        global_bbox = BoundingBox.from_min_max(header['bbox_min'], header['bbox_max'])

        result_submeshes = []
        for sub in submeshes:
            if sub.nv == 0:
                continue

            vb = decode_pam_vertices(
                data, geom_off, sub.voff * stride,
                sub.nv, header['bbox_min'], header['bbox_max'], stride)

            ib = decode_indices(
                data, idx_byte_start + sub.ioff * index_size,
                sub.ni, index_size)

            tex_base = sub.texture_name.lower()
            if tex_base.endswith('.dds'):
                tex_base = tex_base[:-4]

            name = sub.material_name or tex_base

            result_submeshes.append(SubMesh(
                name=name,
                material_name=name,
                texture_basename=tex_base,
                lods={0: (vb, ib)},
                bbox=global_bbox,
                vertex_stride=stride,
            ))

        if not result_submeshes:
            raise ValueError("No meshes with geometry found")

        return ParsedModel(
            source_format=SourceFormat.PAM,
            format_version=header['version'],
            submeshes=result_submeshes,
            bbox=global_bbox,
            raw_size=len(data),
            lod_count=1,
            available_lods=[0],
        )

    def _parse_header(self, data: bytes) -> dict:
        magic = data[0:4]
        if magic != b'PAR ':
            raise ValueError(f"Not a PAR file (magic: {magic!r})")

        version = struct.unpack_from('<I', data, 4)[0]
        if version == self.PAC_VERSION:
            raise ValueError("This is a PAC file, not PAM")
        if version not in self.VERSIONS:
            raise ValueError(f"Unknown PAM version: 0x{version:08X}")

        mesh_count = struct.unpack_from('<I', data, 0x10)[0]
        bbox_min = struct.unpack_from('<3f', data, 0x14)
        bbox_max = struct.unpack_from('<3f', data, 0x20)
        geom_off = struct.unpack_from('<I', data, 0x3C)[0]
        geom_size = struct.unpack_from('<I', data, 0x40)[0]
        comp_geom_size = struct.unpack_from('<I', data, 0x44)[0]

        return {
            'version': version,
            'mesh_count': mesh_count,
            'bbox_min': bbox_min,
            'bbox_max': bbox_max,
            'geom_off': geom_off,
            'geom_size': geom_size,
            'comp_geom_size': comp_geom_size,
        }

    def _parse_submeshes(self, data: bytes, count: int) -> list[PamSubmesh]:
        submeshes = []
        for i in range(count):
            off = self.SUBMESH_TABLE_OFF + i * self.SUBMESH_STRIDE
            if off + self.SUBMESH_STRIDE > len(data):
                break
            nv, ni, voff, ioff = struct.unpack_from('<4I', data, off)
            tex_bytes = data[off + 16: off + 16 + 256]
            tex_name = tex_bytes.split(b'\x00', 1)[0].decode('ascii', errors='replace')
            mat_bytes = data[off + 272: off + 272 + 256]
            mat_name = mat_bytes.split(b'\x00', 1)[0].decode('ascii', errors='replace')
            submeshes.append(PamSubmesh(nv=nv, ni=ni, voff=voff, ioff=ioff,
                                        texture_name=tex_name, material_name=mat_name))
        return submeshes

    def _detect_stride(self, header: dict, submeshes: list[PamSubmesh]) -> int:
        total_nv = sum(s.nv for s in submeshes)
        total_ni = sum(s.ni for s in submeshes)
        if total_nv == 0:
            return 20

        index_size = 4 if total_nv > 65535 else 2
        geom_size = header['geom_size']
        remaining = geom_size - total_ni * index_size
        if remaining > 0 and remaining % total_nv == 0:
            return remaining // total_nv

        for s in [20, 24, 28, 32, 36, 40, 16, 12, 8]:
            if total_nv * s + total_ni * index_size <= geom_size:
                return s
        return 20
