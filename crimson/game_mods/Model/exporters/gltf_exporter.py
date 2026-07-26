"""glTF 2.0 exporter producing binary GLB files.

No external dependencies — writes GLB format directly using struct + json.
Supports: per-submesh materials with PBR textures, tangents, skeleton, bone weights.
"""

import json
import os
import struct

import numpy as np

from exporters.base import MeshExporter, ExportResult, ExportWarning, ExportOptions
from model_types import ParsedModel, Bone
from pac_parser import material_to_dds_basename


# glTF constants
FLOAT = 5126
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963


def _pad_to_4(data: bytes) -> bytes:
    rem = len(data) % 4
    return data + b'\x00' * (4 - rem) if rem else data


def _write_glb(json_dict: dict, bin_data: bytes, path: str):
    json_bytes = json.dumps(json_dict, separators=(',', ':')).encode('utf-8')
    rem = len(json_bytes) % 4
    if rem:
        json_bytes += b' ' * (4 - rem)
    bin_data = _pad_to_4(bin_data)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
    with open(path, 'wb') as f:
        f.write(struct.pack('<4sII', b'glTF', 2, total))
        f.write(struct.pack('<II', len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        f.write(struct.pack('<II', len(bin_data), 0x004E4942))
        f.write(bin_data)


def _tex_exists(dds_base, suffix, available):
    """Check if a texture file exists in the available set."""
    if available is None:
        return True  # assume all exist when set not provided
    return f"{dds_base}{suffix}.dds" in available


def _build_glb(model: ParsedModel, lod: int = 0, texture_rel_dir: str = "",
               available_textures: set = None, diffuse_overrides: dict = None):
    """Build glTF JSON and binary buffer from a ParsedModel."""
    bin_parts = []
    buffer_views = []
    accessors = []

    def _add_buffer(arr, component_type, acc_type, target=None, count=None):
        raw_data = arr.tobytes()
        data = _pad_to_4(raw_data)
        bv_idx = len(buffer_views)
        acc_idx = len(accessors)
        offset = sum(len(p) for p in bin_parts)
        bv = {'buffer': 0, 'byteOffset': offset, 'byteLength': len(raw_data)}
        if target:
            bv['target'] = target
        buffer_views.append(bv)
        acc = {
            'bufferView': bv_idx,
            'componentType': component_type,
            'count': count if count is not None else len(arr),
            'type': acc_type,
        }
        if acc_type == 'VEC3' and component_type == FLOAT and len(arr) > 0:
            acc['min'] = arr.min(axis=0).tolist()
            acc['max'] = arr.max(axis=0).tolist()
        accessors.append(acc)
        bin_parts.append(data)
        return acc_idx

    # ---- Collect geometry per submesh ----
    submesh_data = []
    has_tangents = False
    has_bones = False

    for sm in model.submeshes:
        geom = sm.get_geometry(lod)
        if geom is None:
            continue
        vb, ib = geom
        submesh_data.append((sm, vb, ib))
        if vb.tangents is not None:
            has_tangents = True
        if vb.bone_indices is not None and vb.bone_weights is not None:
            has_bones = True

    if not submesh_data:
        return {}, b''

    # ---- Combined vertex buffer (shared across primitives) ----
    all_pos = np.concatenate([vb.positions for _, vb, _ in submesh_data]).astype(np.float32)
    all_nor = np.concatenate([vb.normals for _, vb, _ in submesh_data]).astype(np.float32)
    # glTF uses top-left UV origin (same as game) — no V-flip
    all_uv = np.concatenate([vb.uvs for _, vb, _ in submesh_data]).astype(np.float32)

    pos_acc = _add_buffer(all_pos, FLOAT, 'VEC3', ARRAY_BUFFER)
    nor_acc = _add_buffer(all_nor, FLOAT, 'VEC3', ARRAY_BUFFER)
    uv_acc = _add_buffer(all_uv, FLOAT, 'VEC2', ARRAY_BUFFER)

    tan_acc = None
    if has_tangents:
        all_tan = np.concatenate([
            vb.tangents if vb.tangents is not None
            else np.zeros((vb.count, 4), dtype=np.float32)
            for _, vb, _ in submesh_data
        ]).astype(np.float32)
        tan_acc = _add_buffer(all_tan, FLOAT, 'VEC4', ARRAY_BUFFER)

    joints_acc = None
    weights_acc = None
    if has_bones:
        all_joints = np.concatenate([
            vb.bone_indices[:, :4].astype(np.uint16) if vb.bone_indices is not None
            else np.zeros((vb.count, 4), dtype=np.uint16)
            for _, vb, _ in submesh_data
        ])
        all_weights = np.concatenate([
            vb.bone_weights[:, :4].astype(np.float32) if vb.bone_weights is not None
            else np.zeros((vb.count, 4), dtype=np.float32)
            for _, vb, _ in submesh_data
        ])
        joints_acc = _add_buffer(all_joints, UNSIGNED_SHORT, 'VEC4', ARRAY_BUFFER)
        weights_acc = _add_buffer(all_weights, FLOAT, 'VEC4', ARRAY_BUFFER)

    # Shared attributes dict
    attributes = {'POSITION': pos_acc, 'NORMAL': nor_acc, 'TEXCOORD_0': uv_acc}
    if tan_acc is not None:
        attributes['TANGENT'] = tan_acc
    if joints_acc is not None:
        attributes['JOINTS_0'] = joints_acc
        attributes['WEIGHTS_0'] = weights_acc

    # ---- Per-submesh index accessors ----
    vertex_offset = 0
    index_accs = []
    for sm, vb, ib in submesh_data:
        idx = (ib.indices.astype(np.uint32) + vertex_offset)
        idx_acc = _add_buffer(idx, UNSIGNED_INT, 'SCALAR', ELEMENT_ARRAY_BUFFER)
        index_accs.append(idx_acc)
        vertex_offset += vb.count

    # ---- Materials + textures ----
    gltf_materials = []
    gltf_textures = []
    gltf_images = []
    gltf_samplers = [{'magFilter': 9729, 'minFilter': 9987, 'wrapS': 10497, 'wrapT': 10497}]
    material_map = {}  # material_name → material index

    def _get_or_create_material(mat_name):
        if mat_name in material_map:
            return material_map[mat_name]
        if mat_name == "(null)":
            return None

        dds_base = material_to_dds_basename(mat_name)
        tex_dir = texture_rel_dir.replace('\\', '/') if texture_rel_dir else ""
        mat_idx = len(gltf_materials)
        material_map[mat_name] = mat_idx

        material = {'name': mat_name, 'pbrMetallicRoughness': {
            'baseColorFactor': [0.8, 0.8, 0.8, 1.0],
            'metallicFactor': 0.0,
            'roughnessFactor': 0.5,
        }}
        pbr = material['pbrMetallicRoughness']

        def _add_image(filename):
            """Add image + texture entry, return texture index."""
            img_idx = len(gltf_images)
            uri = f"{tex_dir}/{filename}" if tex_dir else filename
            gltf_images.append({'uri': uri})
            tex_idx = len(gltf_textures)
            gltf_textures.append({'source': img_idx, 'sampler': 0})
            return tex_idx

        # Diffuse: dye override > base diffuse > _ma fallback
        override = (diffuse_overrides or {}).get(mat_name)
        if override:
            tex_idx = _add_image(override)
            pbr['baseColorTexture'] = {'index': tex_idx}
        elif _tex_exists(dds_base, "", available_textures):
            tex_idx = _add_image(f"{dds_base}.dds")
            pbr['baseColorTexture'] = {'index': tex_idx}
        elif _tex_exists(dds_base, "_ma", available_textures):
            tex_idx = _add_image(f"{dds_base}_ma.dds")
            pbr['baseColorTexture'] = {'index': tex_idx}

        # Normal map
        if _tex_exists(dds_base, "_n", available_textures):
            tex_idx = _add_image(f"{dds_base}_n.dds")
            material['normalTexture'] = {'index': tex_idx}

        # Specular/metallic-roughness: _sp or _mg
        if _tex_exists(dds_base, "_sp", available_textures):
            tex_idx = _add_image(f"{dds_base}_sp.dds")
            pbr['metallicRoughnessTexture'] = {'index': tex_idx}
        elif _tex_exists(dds_base, "_mg", available_textures):
            tex_idx = _add_image(f"{dds_base}_mg.dds")
            pbr['metallicRoughnessTexture'] = {'index': tex_idx}

        # Occlusion: _o
        if _tex_exists(dds_base, "_o", available_textures):
            tex_idx = _add_image(f"{dds_base}_o.dds")
            material['occlusionTexture'] = {'index': tex_idx}

        gltf_materials.append(material)
        return mat_idx

    # ---- Build primitives with materials ----
    primitives = []
    for i, (sm, vb, ib) in enumerate(submesh_data):
        prim = {'attributes': attributes, 'indices': index_accs[i]}
        mat_idx = _get_or_create_material(sm.material_name)
        if mat_idx is not None:
            prim['material'] = mat_idx
        primitives.append(prim)

    mesh = {
        'name': model.submeshes[0].name if model.submeshes else 'mesh',
        'primitives': primitives,
    }

    # ---- Nodes + skeleton ----
    nodes = [{'mesh': 0, 'name': 'root'}]
    skin = None
    has_skeleton = model.bones is not None and len(model.bones) > 0

    if has_skeleton and has_bones:
        bone_start = len(nodes)
        joint_indices = []

        for i, bone in enumerate(model.bones):
            node = {'name': bone.name}
            pos = bone.position.tolist()
            rot = bone.rotation.tolist()
            scl = bone.scale.tolist()
            if any(abs(p) > 1e-6 for p in pos):
                node['translation'] = pos
            if any(abs(r - d) > 1e-6 for r, d in zip(rot, [0, 0, 0, 1])):
                node['rotation'] = rot
            if any(abs(s - 1) > 1e-6 for s in scl):
                node['scale'] = scl
            nodes.append(node)
            joint_indices.append(bone_start + i)

        for i, bone in enumerate(model.bones):
            node_idx = bone_start + i
            if 0 <= bone.parent_index < len(model.bones):
                parent_node = bone_start + bone.parent_index
                nodes[parent_node].setdefault('children', []).append(node_idx)
            else:
                nodes[0].setdefault('children', []).append(node_idx)

        ibm_data = np.zeros((len(model.bones), 4, 4), dtype=np.float32)
        for i, bone in enumerate(model.bones):
            ibm_data[i] = bone.inverse_bind_matrix
        ibm_acc = _add_buffer(ibm_data.ravel(), FLOAT, 'MAT4',
                              count=len(model.bones))

        skin = {'inverseBindMatrices': ibm_acc, 'joints': joint_indices}
        nodes[0]['skin'] = 0

    # ---- Assemble glTF ----
    gltf = {
        'asset': {'version': '2.0', 'generator': 'Crimson Desert PAC Exporter'},
        'scene': 0,
        'scenes': [{'nodes': [0]}],
        'nodes': nodes,
        'meshes': [mesh],
        'accessors': accessors,
        'bufferViews': buffer_views,
        'buffers': [{'byteLength': sum(len(p) for p in bin_parts)}],
    }
    if gltf_materials:
        gltf['materials'] = gltf_materials
    if gltf_textures:
        gltf['textures'] = gltf_textures
        gltf['images'] = gltf_images
        gltf['samplers'] = gltf_samplers
    if skin:
        gltf['skins'] = [skin]

    return gltf, b''.join(bin_parts)


class GltfExporter(MeshExporter):
    format_id = "gltf"
    format_name = "glTF 2.0 Binary"
    file_extension = ".glb"

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
        glb_filename = base_name + '.glb'
        os.makedirs(output_dir, exist_ok=True)
        glb_path = os.path.join(output_dir, glb_filename)

        gltf_json, bin_data = _build_glb(
            model, lod=opts.lod,
            texture_rel_dir=opts.texture_rel_dir,
            available_textures=opts.available_textures,
            diffuse_overrides=opts.diffuse_overrides,
        )
        _write_glb(gltf_json, bin_data, glb_path)

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
            output_files=[glb_path],
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
