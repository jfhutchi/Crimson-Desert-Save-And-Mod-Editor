"""Wavefront OBJ + MTL exporter for parsed mesh models.

Provides:
- ObjExporter class (plugin interface via MeshExporter)
- write_obj_from_model() / write_mtl_from_model() — numpy-direct writers for ParsedModel
- write_obj() / write_mtl() — legacy writers for old Mesh objects (backward compat)
"""

import os

import numpy as np

from exporters.base import MeshExporter, ExportResult, ExportWarning, ExportOptions
from model_types import ParsedModel
from pac_parser import material_to_dds_basename


# ---- Numpy-direct writers (operate on ParsedModel, no intermediate objects) ----


def write_obj_from_model(model: ParsedModel, obj_path: str, mtl_filename: str,
                         lod: int = 0):
    """Write OBJ directly from ParsedModel numpy arrays. No Python loops over vertices."""
    with open(obj_path, 'wb') as f:
        f.write(f"# Crimson Desert PAC export\nmtllib {mtl_filename}\n\n".encode())

        vert_offset = 0
        for sm in model.submeshes:
            geom = sm.get_geometry(lod)
            if geom is None:
                continue
            vb, ib = geom

            f.write(f"o {sm.name}\nusemtl {sm.material_name}\n".encode())

            # Positions — np.savetxt writes bytes to binary file
            np.savetxt(f, vb.positions, fmt='v %.6f %.6f %.6f')

            # Normals
            np.savetxt(f, vb.normals, fmt='vn %.6f %.6f %.6f')

            # UVs with V-flip
            uvs = vb.uvs.copy()
            uvs[:, 1] = 1.0 - uvs[:, 1]
            np.savetxt(f, uvs, fmt='vt %.6f %.6f')

            # Faces — fully vectorized via np.savetxt on (N_tri, 9) array
            n_idx = (ib.count // 3) * 3  # ensure complete triangles
            tri = ib.indices[:n_idx].reshape(-1, 3).astype(np.int64) + vert_offset + 1
            face_data = np.column_stack([
                tri[:, 0], tri[:, 0], tri[:, 0],
                tri[:, 1], tri[:, 1], tri[:, 1],
                tri[:, 2], tri[:, 2], tri[:, 2],
            ])
            np.savetxt(f, face_data, fmt='f %d/%d/%d %d/%d/%d %d/%d/%d')

            vert_offset += vb.count
            f.write(b"\n")


def write_mtl_from_model(model: ParsedModel, mtl_path: str,
                         texture_rel_dir: str = "",
                         available_textures: set = None,
                         diffuse_overrides: dict = None):
    """Write MTL from ParsedModel submeshes (no legacy Mesh objects needed)."""
    with open(mtl_path, 'w') as f:
        f.write(f"# Materials for {os.path.basename(mtl_path).replace('.mtl', '')}\n\n")

        seen = set()
        for sm in model.submeshes:
            mat = sm.material_name
            if mat in seen or mat == "(null)":
                continue
            seen.add(mat)

            dds_base = material_to_dds_basename(mat)
            tex_prefix = (".\\" + texture_rel_dir + "\\" + dds_base) if texture_rel_dir else dds_base

            f.write(f"newmtl {mat}\n")
            f.write("Ka 0.2 0.2 0.2\n")
            f.write("Kd 0.8 0.8 0.8\n")
            f.write("Ks 0.5 0.5 0.5\n")
            f.write("Ns 100.0\n")

            def _tex_exists(suffix, _base=dds_base):
                name = f"{_base}{suffix}.dds"
                return available_textures is None or name in available_textures

            override = (diffuse_overrides or {}).get(mat)
            if override:
                rel = (".\\" + texture_rel_dir + "\\" + override) if texture_rel_dir else override
                f.write(f"map_Kd {rel}\n")
            elif _tex_exists(""):
                f.write(f"map_Kd {tex_prefix}.dds\n")
            elif _tex_exists("_ma"):
                f.write(f"map_Kd {tex_prefix}_ma.dds\n")

            if _tex_exists("_n"):
                f.write(f"bump {tex_prefix}_n.dds\n")

            if _tex_exists("_sp"):
                f.write(f"map_Ks {tex_prefix}_sp.dds\n")
            elif _tex_exists("_mg"):
                f.write(f"map_Ks {tex_prefix}_mg.dds\n")

            if _tex_exists("_disp"):
                f.write(f"disp {tex_prefix}_disp.dds\n")

            f.write("\n")


# ---- Plugin class ----


class ObjExporter(MeshExporter):
    format_id = "obj"
    format_name = "Wavefront OBJ"
    file_extension = ".obj"

    def export_to_disk(self, model: ParsedModel, output_dir: str,
                       options: ExportOptions = None) -> ExportResult:
        opts = options or ExportOptions()
        warnings = []

        has_geometry = any(
            sm.get_geometry(opts.lod) is not None for sm in model.submeshes
        )
        if not has_geometry:
            return ExportResult(success=False, warnings=[
                ExportWarning("error", "geometry", "No meshes with geometry found")])

        base_name = opts.name_hint or model.submeshes[0].name.lower().replace(' ', '_')
        obj_filename = base_name + '.obj'
        mtl_filename = base_name + '.mtl'

        os.makedirs(output_dir, exist_ok=True)
        obj_path = os.path.join(output_dir, obj_filename)
        mtl_path = os.path.join(output_dir, mtl_filename)

        write_obj_from_model(model, obj_path, mtl_filename, lod=opts.lod)
        write_mtl_from_model(model, mtl_path, opts.texture_rel_dir,
                             opts.available_textures, opts.diffuse_overrides)

        # Collect stats
        total_verts = 0
        total_tris = 0
        names = []
        for sm in model.submeshes:
            geom = sm.get_geometry(opts.lod)
            if geom is None:
                continue
            vb, ib = geom
            total_verts += vb.count
            total_tris += ib.count // 3
            names.append(sm.name)

        # Texture stats
        tex_expected = 0
        tex_found = 0
        for sm in model.submeshes:
            if sm.texture_basename:
                tex_expected += 1
                if opts.available_textures and f"{sm.texture_basename}.dds" in opts.available_textures:
                    tex_found += 1

        return ExportResult(
            success=True,
            output_files=[obj_path, mtl_path],
            stats={
                'meshes': len(names),
                'vertices': total_verts,
                'triangles': total_tris,
                'names': names,
            },
            warnings=warnings,
            textures_extracted=tex_found,
            textures_expected=tex_expected,
        )


# ---- Legacy standalone functions (backward compat with old Mesh objects) ----


def write_mtl(meshes, mtl_path: str, texture_rel_dir: str = "",
              available_textures: set = None, diffuse_overrides: dict = None):
    """Write an MTL file from a list of legacy Mesh objects. Deprecated."""
    with open(mtl_path, 'w') as f:
        f.write(f"# Materials for {os.path.basename(mtl_path).replace('.mtl', '')}\n\n")

        seen = set()
        for mesh in meshes:
            if mesh.material in seen or mesh.material == "(null)":
                continue
            seen.add(mesh.material)

            dds_base = material_to_dds_basename(mesh.material)
            tex_prefix = (".\\" + texture_rel_dir + "\\" + dds_base) if texture_rel_dir else dds_base

            f.write(f"newmtl {mesh.material}\n")
            f.write("Ka 0.2 0.2 0.2\n")
            f.write("Kd 0.8 0.8 0.8\n")
            f.write("Ks 0.5 0.5 0.5\n")
            f.write("Ns 100.0\n")

            def _tex_exists(suffix, _dds_base=dds_base):
                name = f"{_dds_base}{suffix}.dds"
                return available_textures is None or name in available_textures

            override = (diffuse_overrides or {}).get(mesh.material)
            if override:
                rel = (".\\" + texture_rel_dir + "\\" + override) if texture_rel_dir else override
                f.write(f"map_Kd {rel}\n")
            elif _tex_exists(""):
                f.write(f"map_Kd {tex_prefix}.dds\n")
            elif _tex_exists("_ma"):
                f.write(f"map_Kd {tex_prefix}_ma.dds\n")

            if _tex_exists("_n"):
                f.write(f"bump {tex_prefix}_n.dds\n")

            if _tex_exists("_sp"):
                f.write(f"map_Ks {tex_prefix}_sp.dds\n")
            elif _tex_exists("_mg"):
                f.write(f"map_Ks {tex_prefix}_mg.dds\n")

            if _tex_exists("_disp"):
                f.write(f"disp {tex_prefix}_disp.dds\n")

            f.write("\n")


def write_obj(meshes, obj_path: str, mtl_filename: str):
    """Write an OBJ file from a list of legacy Mesh objects. Deprecated."""
    with open(obj_path, 'w') as f:
        f.write(f"# Crimson Desert PAC export\n")
        f.write(f"mtllib {mtl_filename}\n\n")

        vert_offset = 0

        for mesh in meshes:
            f.write(f"o {mesh.name}\n")
            f.write(f"usemtl {mesh.material}\n")

            for v in mesh.vertices:
                x, y, z = v.pos
                f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")

            for v in mesh.vertices:
                nx, ny, nz = v.normal
                f.write(f"vn {nx:.6f} {ny:.6f} {nz:.6f}\n")

            for v in mesh.vertices:
                u, v_coord = v.uv
                f.write(f"vt {u:.6f} {1.0 - v_coord:.6f}\n")

            for i in range(0, len(mesh.indices), 3):
                i0 = mesh.indices[i] + vert_offset + 1
                i1 = mesh.indices[i + 1] + vert_offset + 1
                i2 = mesh.indices[i + 2] + vert_offset + 1
                f.write(f"f {i0}/{i0}/{i0} {i1}/{i1}/{i1} {i2}/{i2}/{i2}\n")

            vert_offset += len(mesh.vertices)
            f.write("\n")
