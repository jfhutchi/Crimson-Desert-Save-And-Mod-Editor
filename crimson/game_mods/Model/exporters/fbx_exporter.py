"""FBX ASCII 7.4 exporter.

No external dependencies — writes FBX ASCII format directly.
Supports: per-submesh materials with texture references, normals, UVs,
tangents, skeleton, bone weights. Blender imports this natively.
"""

import os

import numpy as np

from exporters.base import MeshExporter, ExportResult, ExportWarning, ExportOptions
from model_types import ParsedModel
from pac_parser import material_to_dds_basename


def _fbx_floats(arr):
    return ','.join(f'{v:.6f}' for v in arr.ravel())


def _fbx_ints(arr):
    return ','.join(str(int(v)) for v in arr.ravel())


def _tex_exists(dds_base, suffix, available):
    if available is None:
        return True
    return f"{dds_base}{suffix}.dds" in available


def _write_fbx(model: ParsedModel, fbx_path: str, lod: int = 0,
               texture_rel_dir: str = "", available_textures: set = None,
               diffuse_overrides: dict = None):
    """Write FBX ASCII 7.4 file from ParsedModel."""
    # ---- Collect geometry ----
    all_pos = []
    all_nor = []
    all_uv = []
    all_tan = []
    all_idx = []
    submesh_ranges = []  # (start_tri, tri_count, material_name) for material layer
    vertex_offset = 0
    tri_offset = 0
    has_tangents = False

    for sm in model.submeshes:
        geom = sm.get_geometry(lod)
        if geom is None:
            continue
        vb, ib = geom
        all_pos.append(vb.positions)
        all_nor.append(vb.normals)
        uvs = vb.uvs.copy()
        uvs[:, 1] = 1.0 - uvs[:, 1]
        all_uv.append(uvs)
        if vb.tangents is not None:
            all_tan.append(vb.tangents[:, :3])
            has_tangents = True
        idx = ib.indices.astype(np.int32) + vertex_offset
        all_idx.append(idx)
        n_tris = len(idx) // 3
        submesh_ranges.append((tri_offset, n_tris, sm.material_name))
        vertex_offset += vb.count
        tri_offset += n_tris

    if not all_pos:
        return

    positions = np.concatenate(all_pos)
    normals = np.concatenate(all_nor)
    uvs = np.concatenate(all_uv)
    indices = np.concatenate(all_idx)
    tangents = np.concatenate(all_tan) if has_tangents else None
    total_verts = len(positions)
    total_idx = len(indices)
    total_tris = total_idx // 3

    # FBX polygon vertex indices: last index of each tri negated-minus-1
    face_idx = indices.copy()
    face_idx[2::3] = -(face_idx[2::3] + 1)

    # Per-polygon-vertex expanded arrays
    nor_expanded = normals[indices]
    uv_expanded = uvs[indices]
    tan_expanded = tangents[indices] if has_tangents else None

    # ---- ID generation ----
    geom_id = 300000
    model_id = 200000
    skin_id = 600000
    _next_id = [900000]

    def _new_id():
        _next_id[0] += 1
        return _next_id[0]

    # ---- Build unique materials ----
    unique_mats = []
    mat_name_to_idx = {}
    for _, _, mat_name in submesh_ranges:
        if mat_name not in mat_name_to_idx and mat_name != "(null)":
            mat_name_to_idx[mat_name] = len(unique_mats)
            unique_mats.append(mat_name)

    # Per-triangle material index
    mat_indices = np.zeros(total_tris, dtype=np.int32)
    for start_tri, n_tris, mat_name in submesh_ranges:
        idx = mat_name_to_idx.get(mat_name, 0)
        mat_indices[start_tri:start_tri + n_tris] = idx

    # Material + texture IDs
    mat_ids = {}     # mat_name → material_id
    tex_objects = []  # list of (tex_id, video_id, mat_id, property_name, filepath)

    for mat_name in unique_mats:
        mat_id = _new_id()
        mat_ids[mat_name] = mat_id

        dds_base = material_to_dds_basename(mat_name)
        tex_dir = texture_rel_dir

        def _add_tex(filename, prop_name):
            tex_id = _new_id()
            video_id = _new_id()
            path = os.path.join(tex_dir, filename) if tex_dir else filename
            tex_objects.append((tex_id, video_id, mat_id, prop_name, path, filename))

        # Diffuse: dye override > base > _ma
        override = (diffuse_overrides or {}).get(mat_name)
        if override:
            _add_tex(override, "DiffuseColor")
        elif _tex_exists(dds_base, "", available_textures):
            _add_tex(f"{dds_base}.dds", "DiffuseColor")
        elif _tex_exists(dds_base, "_ma", available_textures):
            _add_tex(f"{dds_base}_ma.dds", "DiffuseColor")

        # Normal
        if _tex_exists(dds_base, "_n", available_textures):
            _add_tex(f"{dds_base}_n.dds", "NormalMap")

        # Specular
        if _tex_exists(dds_base, "_sp", available_textures):
            _add_tex(f"{dds_base}_sp.dds", "SpecularColor")
        elif _tex_exists(dds_base, "_mg", available_textures):
            _add_tex(f"{dds_base}_mg.dds", "SpecularColor")

        # Displacement
        if _tex_exists(dds_base, "_disp", available_textures):
            _add_tex(f"{dds_base}_disp.dds", "DisplacementColor")

    # ---- Skeleton ----
    has_skeleton = model.bones is not None and len(model.bones) > 0
    bone_ids = {}

    # ---- Write FBX ----
    lines = []
    w = lines.append

    w('; FBX 7.4.0 project file')
    w('; Crimson Desert PAC Exporter')
    w('FBXHeaderExtension:  {')
    w('    FBXHeaderVersion: 1003')
    w('    FBXVersion: 7400')
    w('}')
    w('')
    w('GlobalSettings:  {')
    w('    Version: 1000')
    w('    Properties70:  {')
    w('        P: "UpAxis", "int", "Integer", "",1')
    w('        P: "UpAxisSign", "int", "Integer", "",1')
    w('        P: "FrontAxis", "int", "Integer", "",2')
    w('        P: "FrontAxisSign", "int", "Integer", "",1')
    w('        P: "CoordAxis", "int", "Integer", "",0')
    w('        P: "CoordAxisSign", "int", "Integer", "",1')
    w('        P: "UnitScaleFactor", "double", "Number", "",100')
    w('    }')
    w('}')
    w('')

    # ---- Objects section ----
    w('Objects:  {')

    # Geometry
    w(f'    Geometry: {geom_id}, "Geometry::Mesh", "Mesh" {{')
    w(f'        Vertices: *{total_verts * 3} {{')
    w(f'            a: {_fbx_floats(positions)}')
    w('        }')
    w(f'        PolygonVertexIndex: *{total_idx} {{')
    w(f'            a: {_fbx_ints(face_idx)}')
    w('        }')

    # Normals
    w('        LayerElementNormal: 0 {')
    w('            Version: 101')
    w('            Name: "Normals"')
    w('            MappingInformationType: "ByPolygonVertex"')
    w('            ReferenceInformationType: "Direct"')
    w(f'            Normals: *{total_idx * 3} {{')
    w(f'                a: {_fbx_floats(nor_expanded)}')
    w('            }')
    w('        }')

    # Tangents
    if has_tangents and tan_expanded is not None:
        w('        LayerElementTangent: 0 {')
        w('            Version: 101')
        w('            Name: "Tangents"')
        w('            MappingInformationType: "ByPolygonVertex"')
        w('            ReferenceInformationType: "Direct"')
        w(f'            Tangents: *{total_idx * 3} {{')
        w(f'                a: {_fbx_floats(tan_expanded)}')
        w('            }')
        w('        }')

    # UVs
    w('        LayerElementUV: 0 {')
    w('            Version: 101')
    w('            Name: "UVMap"')
    w('            MappingInformationType: "ByPolygonVertex"')
    w('            ReferenceInformationType: "Direct"')
    w(f'            UV: *{total_idx * 2} {{')
    w(f'                a: {_fbx_floats(uv_expanded[:, :2])}')
    w('            }')
    w('        }')

    # Material layer (per-triangle material assignment)
    if unique_mats:
        w('        LayerElementMaterial: 0 {')
        w('            Version: 101')
        w('            Name: "Materials"')
        w('            MappingInformationType: "ByPolygon"')
        w('            ReferenceInformationType: "IndexToDirect"')
        w(f'            Materials: *{total_tris} {{')
        w(f'                a: {_fbx_ints(mat_indices)}')
        w('            }')
        w('        }')

    # Layer definition
    w('        Layer: 0 {')
    w('            Version: 100')
    w('            LayerElement:  {')
    w('                Type: "LayerElementNormal"')
    w('                TypedIndex: 0')
    w('            }')
    w('            LayerElement:  {')
    w('                Type: "LayerElementUV"')
    w('                TypedIndex: 0')
    w('            }')
    if has_tangents:
        w('            LayerElement:  {')
        w('                Type: "LayerElementTangent"')
        w('                TypedIndex: 0')
        w('            }')
    if unique_mats:
        w('            LayerElement:  {')
        w('                Type: "LayerElementMaterial"')
        w('                TypedIndex: 0')
        w('            }')
    w('        }')
    w('    }')

    # Model node
    w(f'    Model: {model_id}, "Model::Mesh", "Mesh" {{')
    w('        Version: 232')
    w('    }')

    # Material objects
    for mat_name in unique_mats:
        mid = mat_ids[mat_name]
        w(f'    Material: {mid}, "Material::{mat_name}", "" {{')
        w('        Version: 102')
        w('        ShadingModel: "phong"')
        w('        Properties70:  {')
        w('            P: "DiffuseColor", "Color", "", "A",0.8,0.8,0.8')
        w('            P: "SpecularColor", "Color", "", "A",0.2,0.2,0.2')
        w('            P: "Shininess", "double", "Number", "",25')
        w('            P: "ShininessExponent", "double", "Number", "",25')
        w('            P: "ReflectionFactor", "double", "Number", "",0')
        w('        }')
        w('    }')

    # Texture + Video objects
    for tex_id, video_id, _mid, prop_name, filepath, filename in tex_objects:
        w(f'    Texture: {tex_id}, "Texture::{filename}", "" {{')
        w('        Type: "TextureVideoClip"')
        w(f'        FileName: "{filepath}"')
        w(f'        RelativeFilename: "{filepath}"')
        w('    }')
        w(f'    Video: {video_id}, "Video::{filename}", "Clip" {{')
        w('        Type: "Clip"')
        w(f'        FileName: "{filepath}"')
        w(f'        RelativeFilename: "{filepath}"')
        w('    }')

    # Skeleton bones
    if has_skeleton:
        node_attr_ids = {}
        for i, bone in enumerate(model.bones):
            bid = 700000 + i
            naid = 750000 + i
            bone_ids[i] = bid
            node_attr_ids[i] = naid

            # NodeAttribute tells Blender this is a skeleton bone, not an empty
            w(f'    NodeAttribute: {naid}, "NodeAttribute::{bone.name}", "LimbNode" {{')
            w('        TypeFlags: "Skeleton"')
            w('    }')

            w(f'    Model: {bid}, "Model::{bone.name}", "LimbNode" {{')
            w('        Version: 232')
            w('        Properties70:  {')
            pos = bone.position
            w(f'            P: "Lcl Translation", "Lcl Translation", "", "A",{pos[0]:.6f},{pos[1]:.6f},{pos[2]:.6f}')
            w('        }')
            w('    }')

        # Skin deformer
        w(f'    Deformer: {skin_id}, "Deformer::Skin", "Skin" {{')
        w('        Version: 101')
        w('        Link_DeformAcuracy: 50')
        w('    }')

        # Cluster per bone
        for i, bone in enumerate(model.bones):
            cluster_id = 800000 + i
            vert_indices = []
            vert_weights = []
            global_vert_off = 0
            for sm in model.submeshes:
                geom = sm.get_geometry(lod)
                if geom is None:
                    continue
                vb = geom[0]
                if vb.bone_indices is not None and vb.bone_weights is not None:
                    for slot in range(8):
                        mask = (vb.bone_indices[:, slot] == i) & (vb.bone_weights[:, slot] > 1e-6)
                        matched = np.where(mask)[0]
                        for vi in matched:
                            vert_indices.append(int(vi) + global_vert_off)
                            vert_weights.append(float(vb.bone_weights[vi, slot]))
                global_vert_off += vb.count

            w(f'    Deformer: {cluster_id}, "SubDeformer::{bone.name}", "Cluster" {{')
            w('        Version: 100')
            if vert_indices:
                w(f'        Indexes: *{len(vert_indices)} {{')
                w(f'            a: {",".join(str(v) for v in vert_indices)}')
                w('        }')
                w(f'        Weights: *{len(vert_weights)} {{')
                w(f'            a: {",".join(f"{v:.6f}" for v in vert_weights)}')
                w('        }')
            ibm = bone.inverse_bind_matrix.T.ravel()
            w(f'        Transform: *16 {{')
            w(f'            a: {_fbx_floats(ibm)}')
            w('        }')
            try:
                world = np.linalg.inv(bone.inverse_bind_matrix).T.ravel()
            except np.linalg.LinAlgError:
                world = np.eye(4, dtype=np.float32).ravel()
            w(f'        TransformLink: *16 {{')
            w(f'            a: {_fbx_floats(world)}')
            w('        }')
            w('    }')

    w('}')
    w('')

    # ---- Connections ----
    w('Connections:  {')
    w(f'    C: "OO",{model_id},0')
    w(f'    C: "OO",{geom_id},{model_id}')

    # Materials → Model
    for mat_name in unique_mats:
        w(f'    C: "OO",{mat_ids[mat_name]},{model_id}')

    # Textures → Materials, Videos → Textures
    for tex_id, video_id, mid, prop_name, _fp, _fn in tex_objects:
        w(f'    C: "OP",{tex_id},{mid},"{prop_name}"')
        w(f'    C: "OO",{video_id},{tex_id}')

    # Skeleton connections
    if has_skeleton:
        w(f'    C: "OO",{skin_id},{geom_id}')
        for i, bone in enumerate(model.bones):
            bid = bone_ids[i]
            naid = node_attr_ids[i]
            cluster_id = 800000 + i
            # NodeAttribute → Model (makes Blender create armature bone)
            w(f'    C: "OO",{naid},{bid}')
            # Bone hierarchy
            if bone.parent_index >= 0 and bone.parent_index in bone_ids:
                w(f'    C: "OO",{bid},{bone_ids[bone.parent_index]}')
            else:
                w(f'    C: "OO",{bid},0')
            # Cluster connections
            w(f'    C: "OO",{cluster_id},{skin_id}')
            w(f'    C: "OO",{bid},{cluster_id}')

    w('}')

    with open(fbx_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


class FbxExporter(MeshExporter):
    format_id = "fbx"
    format_name = "FBX ASCII 7.4"
    file_extension = ".fbx"

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
        fbx_filename = base_name + '.fbx'
        os.makedirs(output_dir, exist_ok=True)
        fbx_path = os.path.join(output_dir, fbx_filename)

        _write_fbx(model, fbx_path, lod=opts.lod,
                    texture_rel_dir=opts.texture_rel_dir,
                    available_textures=opts.available_textures,
                    diffuse_overrides=opts.diffuse_overrides)

        # Stats
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

        mat_count = len(set(sm.material_name for sm in model.submeshes
                            if sm.material_name != "(null)"))
        if model.bones:
            warnings.append(ExportWarning("info", "skeleton",
                f"Exported {len(model.bones)} bones"))

        return ExportResult(
            success=True,
            output_files=[fbx_path],
            stats={
                'meshes': len(names),
                'vertices': total_verts,
                'triangles': total_tris,
                'names': names,
                'materials': mat_count,
                'has_skeleton': model.bones is not None,
                'bone_count': len(model.bones) if model.bones else 0,
            },
            warnings=warnings,
        )
