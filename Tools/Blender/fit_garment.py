"""
Fits a Tripo-generated garment (unrigged OBJ / GLB / FBX) onto a player base body and
exports it as a skeletal mesh that shares the body's skeleton, skin weights and body sliders.

    python3 Tools/Blender/fit_garment.py <garment.obj> <Female|Male> [--slot torso|legs|feet]
                                         [--name SK_Shirt_Test] [options]
    (also works as  blender -b -P Tools/Blender/fit_garment.py -- <garment.obj> <Body> ...)

Steps (see Docs/Art/Attachment_Pipeline.md and Docs/Art/Next_Steps_Research.md section 2):
    1. Align: scale and move the garment by the body's bone landmarks for the slot
       (shoulders / hips / feet), so Tripo's arbitrary scale and origin don't matter.
    2. Strip the inner shell: Tripo meshes are closed volumes; faces whose normal points into
       the body and that have a facing outer face within --shell-gap are deleted.
    3. Shrinkwrap "Above Surface" (Nearest Surface Point) with the slot's offset, on the
       contact vertices only (close to the skin, never hems / cuffs / collar). A second pass
       pushes any remaining intersecting vertex (hems included) out of the body.
    4. Data Transfer skin weights from the body (Nearest Face Interpolated, all layers),
       limit 4 influences, normalise.
    5. Transfer the body's slider shape keys (not the Exp_* expressions) by nearest-face
       barycentric sampling of the body's deltas, so the garment follows the sliders.
    6. HideMask vertex colour of zeros, placeholder material, parent to the body armature.
    7. Export FBX (deform bones only, no leaf bones, FBX unit scale, shape keys on) and a
       .blend for inspection, then re-import the FBX and verify it.

Prints the HideMask channels (torso / arms / legs / feet) the garment covers, which is what
the Unreal character blueprint sets on the skin material instance.

The base-body .blend is opened read-only: this script never saves it.
"""

import argparse
import math
import os
import sys

import bpy  # noqa: E402  (bpy must load before bmesh)
import bmesh
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BASE_DIR = os.path.join(REPO_ROOT, "Art", "Characters", "Base")
TEST_DIR = os.path.join(REPO_ROOT, "Art", "Characters", "Test")

# Same channels as MASK_BONES in build_base_body.py: HideMask R = torso, G = arms, B = legs, A = feet.
HIDE_CHANNELS = ("torso", "arms", "legs", "feet")
HIDE_PARAMS = ("HideTorso", "HideArms", "HideLegs", "HideFeet")

# Per slot: the bones whose skin region the garment hangs on, which bbox axis gives the scale,
# where it is anchored (clothes hang from the shoulders / waist, shoes stand on the floor) and
# the default Shrinkwrap offset (research doc: 3-6 mm tight clothes, 8-15 mm jackets).
SLOTS = {
    "torso": dict(bones=("spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r"),
                  scale_axis="Z", anchor="top", offset=0.006),
    "legs": dict(bones=("pelvis", "thigh_l", "thigh_r", "calf_l", "calf_r"),
                 scale_axis="X", anchor="top", offset=0.005),
    "feet": dict(bones=("foot_l", "foot_r", "ball_l", "ball_r"),
                 scale_axis="X", anchor="bottom", offset=0.004),
}


# ----------------------------------------------------------------------------- scene helpers

def script_args(argv=None):
    """Arguments after '--' when run inside Blender, else everything after the script name."""
    argv = sys.argv if argv is None else argv
    if "--" in argv:
        return argv[argv.index("--") + 1:]
    return argv[1:]


def open_body(body_name):
    """Opens the base body .blend (never saved) and returns (body mesh object, armature)."""
    blend = os.path.join(BASE_DIR, f"BaseBody_{body_name}.blend")
    if not os.path.exists(blend):
        raise SystemExit(f"No base body: {blend}")
    bpy.ops.wm.open_mainfile(filepath=blend, load_ui=False)
    body = bpy.data.objects.get(f"SK_BaseBody_{body_name}")
    if body is None:
        body = next(o for o in bpy.data.objects if o.type == 'MESH' and o.data.shape_keys)
    armature = body.parent if body.parent and body.parent.type == 'ARMATURE' else None
    if armature is None:
        armature = next(m.object for m in body.modifiers if m.type == 'ARMATURE')
    armature.data.pose_position = 'REST'
    for pb in armature.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    return body, armature


def bone_names(armature):
    return {b.name for b in armature.data.bones if b.use_deform}


def set_active(obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def import_mesh(path, name):
    """Imports an OBJ / GLB / FBX and returns one mesh object with identity transform."""
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        raise SystemExit(f"Unsupported garment format: {path}")
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == 'MESH']
    if not meshes:
        raise SystemExit(f"No mesh found in {path}")
    for o in meshes:
        o.parent = None
    bpy.ops.object.select_all(action='DESELECT')
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    for o in new:
        if o is not obj and o.name in bpy.data.objects:
            bpy.data.objects.remove(o)
    obj.name = name
    obj.data.name = name
    obj.modifiers.clear()
    obj.vertex_groups.clear()
    if obj.data.shape_keys:
        obj.shape_key_clear()
    obj.data.materials.clear()
    tris = sum(len(p.vertices) - 2 for p in obj.data.polygons)
    print(f"Imported {os.path.basename(path)}: {len(obj.data.vertices)} verts, {tris} tris, "
          f"size {tuple(round(d, 3) for d in obj.dimensions)} m")
    return obj


# ----------------------------------------------------------------------------- body sampling

class BodySurface:
    """Nearest-point queries on the body's skin (Basis shape, material 0 only) plus the data
    needed to interpolate anything stored per body vertex at a point on the surface."""

    def __init__(self, body):
        mesh = body.data
        self.body = body
        basis = mesh.shape_keys.key_blocks[0].data if mesh.shape_keys else mesh.vertices
        self.coords = np.array([v.co[:] for v in basis], dtype=np.float64)
        self.polys = [tuple(p.vertices) for p in mesh.polygons if p.material_index == 0]
        self.bvh = BVHTree.FromPolygons([Vector(c) for c in self.coords], self.polys, all_triangles=False)

    def nearest(self, point):
        """(location, normal, polygon index, distance) of the closest skin point."""
        return self.bvh.find_nearest(Vector(point))

    def signed_distance(self, point):
        loc, normal, _, dist = self.nearest(point)
        return dist if (Vector(point) - loc).dot(normal) >= 0 else -dist

    def sample_weights(self, point):
        """(vertex indices, barycentric weights) of the skin polygon nearest to point."""
        loc, _, index, _ = self.nearest(point)
        poly = self.polys[index]
        weights = poly_3d_calc([Vector(self.coords[i]) for i in poly], loc)
        return poly, weights


def dominant_bone(obj, bone_set):
    """Per-vertex name of the bone with the highest weight (None if unweighted)."""
    names = {g.index: g.name for g in obj.vertex_groups if g.name in bone_set}
    result = []
    for v in obj.data.vertices:
        best, best_w = None, 0.0
        for g in v.groups:
            if g.group in names and g.weight > best_w:
                best, best_w = names[g.group], g.weight
        result.append(best)
    return result


def region_bbox(body, surface, bones, all_bones):
    """Bounding box of the body's skin vertices whose dominant bone is in `bones`."""
    dom = dominant_bone(body, all_bones)
    pts = surface.coords[[i for i, b in enumerate(dom) if b in bones]]
    return pts.min(axis=0), pts.max(axis=0)


# ----------------------------------------------------------------------------- fitting steps

def transform_mesh(obj, matrix):
    obj.data.transform(matrix)
    obj.data.update()


def align_to_region(obj, lo, hi, scale_axis, anchor, pad, scale=None):
    """Uniform scale from one bbox axis, centre X/Y, anchor top or bottom to the region."""
    pts = np.array([v.co[:] for v in obj.data.vertices])
    glo, ghi = pts.min(axis=0), pts.max(axis=0)
    axis = "XYZ".index(scale_axis)
    if scale is None:
        scale = (hi[axis] - lo[axis] + 2 * pad) / max(ghi[axis] - glo[axis], 1e-6)
    transform_mesh(obj, Matrix.Scale(scale, 4))
    pts = np.array([v.co[:] for v in obj.data.vertices])
    glo, ghi = pts.min(axis=0), pts.max(axis=0)
    move = Vector((0.0, 0.0, 0.0))
    move.x = (lo[0] + hi[0]) / 2 - (glo[0] + ghi[0]) / 2
    move.y = (lo[1] + hi[1]) / 2 - (glo[1] + ghi[1]) / 2
    if anchor == "top":
        move.z = (hi[2] + pad) - ghi[2]
    elif anchor == "bottom":
        move.z = lo[2] - glo[2]
    else:
        move.z = (lo[2] + hi[2]) / 2 - (glo[2] + ghi[2]) / 2
    transform_mesh(obj, Matrix.Translation(move))
    print(f"Aligned: scale x{scale:.3f}, moved {tuple(round(m, 3) for m in move)} m")
    return scale


def fix_normals(obj, surface):
    """Makes normals consistent and pointing away from the body (Tripo output is random)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.faces.ensure_lookup_table()
    # Vote with the faces farthest from the skin: those are outer-shell faces for sure.
    scored = []
    for f in bm.faces:
        c = f.calc_center_median()
        loc, _, _, dist = surface.nearest(c)
        scored.append((dist, f.normal.dot(c - loc)))
    scored.sort(reverse=True)
    top = scored[:max(1, len(scored) // 4)]
    if sum(1 for _, d in top if d < 0) > len(top) / 2:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
        print("Normals flipped (they pointed into the body)")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def strip_inner_shell(obj, surface, gap):
    """Deletes faces whose normal points into the body and that have a facing (outer) face
    within `gap` along that normal: the inner wall of a closed Tripo volume."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    tree = BVHTree.FromBMesh(bm, epsilon=0.0)
    inner = []
    for f in bm.faces:
        c = f.calc_center_median()
        n = f.normal
        loc, _, _, skin_dist = surface.nearest(c)
        if n.dot(loc - c) <= 0:
            continue  # normal points away from the skin: outer face
        # The outer wall is behind an inner face: look against the normal for a face that
        # faces back at it and is further from the skin.
        hit = tree.ray_cast(c - n * 1e-4, -n, gap)
        if hit[0] is None or hit[2] == f.index or hit[1].dot(n) >= -0.7:
            continue  # no facing wall within the gap: a real outer face
        if surface.nearest(hit[0])[3] < skin_dist + 0.001:
            continue  # the facing face is not further from the skin: two outer faces (armpit)
        inner.append(f)
    if inner:
        bmesh.ops.delete(bm, geom=inner, context='FACES')
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context='VERTS')
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    print(f"Inner shell: removed {len(inner)} faces, {len(obj.data.polygons)} faces left")
    return len(inner)


def boundary_vertices(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    result = {v.index for e in bm.edges if e.is_boundary for v in e.verts}
    bm.free()
    return result


def make_group(obj, name, indices, weight=1.0):
    group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
    if indices:
        group.add(list(indices), weight, 'REPLACE')
    return group


def shrinkwrap(obj, body, group, offset, mode):
    mod = obj.modifiers.new("fit", 'SHRINKWRAP')
    mod.target = body
    mod.wrap_method = 'NEAREST_SURFACEPOINT'
    mod.wrap_mode = mode
    mod.offset = offset
    mod.vertex_group = group
    set_active(obj)
    bpy.ops.object.modifier_apply(modifier=mod.name)


def wrap_to_body(obj, body, surface, offset, contact_dist, exclude_boundary=True):
    """Contact verts (within contact_dist of the skin, or inside it, not on a hem) are snapped
    `offset` above the skin; then every vertex still inside the skin is pushed out."""
    boundary = boundary_vertices(obj) if exclude_boundary else set()
    contact = [v.index for v in obj.data.vertices
               if v.index not in boundary and surface.signed_distance(v.co) < contact_dist]
    make_group(obj, "contact", contact)
    shrinkwrap(obj, body, "contact", offset, 'ABOVE_SURFACE')
    obj.vertex_groups.remove(obj.vertex_groups["contact"])
    pushed = push_out_of_body(obj, body, surface, offset)
    print(f"Shrinkwrap: {len(contact)} contact verts at {offset * 1000:.0f} mm "
          f"({len(boundary)} hem verts left alone), {pushed} extra verts pushed out")


def push_out_of_body(obj, body, surface, offset, clearance=0.001, passes=4):
    """Any vertex (hems included) or face centre still inside the skin, or closer than
    `clearance`, gets its vertices snapped `offset` above the skin; creases get a larger
    offset each pass so face centres clear the concave skin too."""
    pushed = set()
    for i in range(passes):
        verts = {v.index for v in obj.data.vertices if surface.signed_distance(v.co) < clearance}
        for p in obj.data.polygons:
            if surface.signed_distance(p.center) < clearance:
                verts.update(p.vertices)
        if not verts:
            break
        pushed |= verts
        make_group(obj, "push_out", verts)
        shrinkwrap(obj, body, "push_out", offset * (1 + 0.5 * i), 'ABOVE_SURFACE')
        obj.vertex_groups.remove(obj.vertex_groups["push_out"])
    return len(pushed)


def transfer_weights(obj, body, bone_set):
    """Data Transfer modifier: vertex groups, Nearest Face Interpolated, all layers; then
    limit 4 influences and normalise, as Unreal expects."""
    mod = obj.modifiers.new("weights", 'DATA_TRANSFER')
    mod.object = body
    mod.use_vert_data = True
    mod.data_types_verts = {'VGROUP_WEIGHTS'}
    mod.vert_mapping = 'POLYINTERP_NEAREST'
    mod.layers_vgroup_select_src = 'ALL'
    mod.layers_vgroup_select_dst = 'NAME'
    set_active(obj)
    bpy.ops.object.datalayout_transfer(modifier=mod.name)
    bpy.ops.object.modifier_apply(modifier=mod.name)
    for g in list(obj.vertex_groups):  # mask_* painter groups are not bones
        if g.name not in bone_set:
            obj.vertex_groups.remove(g)
    bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=4)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=False)
    unweighted = count_unweighted(obj)
    if unweighted:
        # Should not happen with nearest-face mapping; fall back to the nearest body vertex.
        fix_unweighted(obj, body, bone_set)
    print(f"Weights: {len(obj.vertex_groups)} bone groups, {count_unweighted(obj)} unweighted verts")


def count_unweighted(obj, eps=1e-4):
    return sum(1 for v in obj.data.vertices if sum(g.weight for g in v.groups) < eps)


def fix_unweighted(obj, body, bone_set):
    surface = BodySurface(body)
    names = {g.index: g.name for g in body.vertex_groups if g.name in bone_set}
    for v in obj.data.vertices:
        if sum(g.weight for g in v.groups) >= 1e-4:
            continue
        poly, weights = surface.sample_weights(v.co)
        acc = {}
        for vi, w in zip(poly, weights):
            for g in body.data.vertices[vi].groups:
                if g.group in names:
                    acc[names[g.group]] = acc.get(names[g.group], 0.0) + g.weight * w
        for name, w in sorted(acc.items(), key=lambda kv: -kv[1])[:4]:
            make_group(obj, name, [v.index], w)


def slider_key_names(body, prefixes=None):
    keys = body.data.shape_keys.key_blocks
    names = [k.name for k in keys[1:] if not k.name.startswith("Exp_")]
    if prefixes:
        names = [n for n in names if n.startswith(tuple(prefixes))]
    return names


def transfer_shape_keys(obj, body, surface, key_names):
    """For every body shape key, the garment vertex gets the body delta interpolated over
    the nearest skin polygon (barycentric). Equivalent to the Shape Transfer add-on's
    surface-nearest mode, without the add-on."""
    body_keys = body.data.shape_keys.key_blocks
    basis = np.array([d.co[:] for d in body_keys[0].data])
    n_body = len(basis)
    samples = [surface.sample_weights(v.co) for v in obj.data.vertices]
    rows, cols, vals = [], [], []
    for gi, (poly, weights) in enumerate(samples):
        for vi, w in zip(poly, weights):
            rows.append(gi)
            cols.append(vi)
            vals.append(w)
    rows, cols, vals = np.array(rows), np.array(cols), np.array(vals)
    garment_basis = np.array([v.co[:] for v in obj.data.vertices])
    if obj.data.shape_keys is None:
        obj.shape_key_add(name="Basis", from_mix=False)
    moved = 0
    for name in key_names:
        delta = np.array([d.co[:] for d in body_keys[name].data]) - basis
        out = np.zeros_like(garment_basis)
        np.add.at(out, rows, delta[cols] * vals[:, None])
        key = obj.shape_key_add(name=name, from_mix=False)
        key.value = 0.0  # Blender 5 creates keys at 1.0 and FBX stores the value (DeformPercent)
        key.data.foreach_set("co", (garment_basis + out).ravel())
        if np.abs(out).max() > 1e-5:
            moved += 1
    assert n_body == len(body_keys[0].data)
    print(f"Shape keys: {len(key_names)} transferred, {moved} of them move this mesh")


def add_hide_mask(obj):
    attr = obj.data.color_attributes.get("HideMask") or obj.data.color_attributes.new("HideMask", 'BYTE_COLOR', 'POINT')
    attr.data.foreach_set("color", [0.0] * (4 * len(obj.data.vertices)))
    obj.data.color_attributes.active_color = attr
    obj.data.color_attributes.render_color_index = obj.data.color_attributes.find("HideMask")


def placeholder_material(obj, name, colour):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = colour
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = colour
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    for p in obj.data.polygons:
        p.material_index = 0
    for p in obj.data.polygons:
        p.use_smooth = True


def parent_to_armature(obj, armature):
    obj.parent = armature
    obj.matrix_parent_inverse = armature.matrix_world.inverted()
    for m in list(obj.modifiers):
        if m.type == 'ARMATURE':
            obj.modifiers.remove(m)
    mod = obj.modifiers.new("Armature", 'ARMATURE')
    mod.object = armature


def coverage(obj, body, surface, cover_dist=0.03):
    """Fraction of each HideMask channel's skin vertices that sit under the garment."""
    attr = body.data.color_attributes.get("HideMask")
    if attr is None:
        return {}
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    tree = BVHTree.FromBMesh(bm)
    totals = [0] * 4
    covered = [0] * 4
    for v in body.data.vertices:
        rgba = attr.data[v.index].color
        channels = [c for c in range(4) if rgba[c] > 0.5]
        if not channels:
            continue
        loc, normal, _, dist = tree.find_nearest(Vector(surface.coords[v.index]), cover_dist)
        under = loc is not None and normal.dot(Vector(surface.coords[v.index]) - loc) < 0
        for c in channels:
            totals[c] += 1
            covered[c] += int(under)
    bm.free()
    return {HIDE_CHANNELS[c]: (covered[c] / totals[c] if totals[c] else 0.0) for c in range(4)}


def report_coverage(obj, body, surface):
    cov = coverage(obj, body, surface)
    if not cov:
        return []
    hidden = [name for name, frac in cov.items() if frac >= 0.5]
    print("HideMask coverage: " + ", ".join(f"{n} {f * 100:.0f}%" for n, f in cov.items()))
    params = ", ".join(f"{HIDE_PARAMS[HIDE_CHANNELS.index(n)]}=1" for n in hidden) or "nothing to hide"
    print(f"Unreal skin material instance for this garment: {params}")
    return hidden


# ----------------------------------------------------------------------------- export / verify

def export_fbx(obj, armature, fbx_path):
    os.makedirs(os.path.dirname(fbx_path), exist_ok=True)
    bpy.ops.object.select_all(action='DESELECT')
    armature.select_set(True)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.export_scene.fbx(
        filepath=fbx_path,
        use_selection=True,
        object_types={'ARMATURE', 'MESH'},
        apply_unit_scale=True,
        apply_scale_options='FBX_SCALE_UNITS',
        add_leaf_bones=False,
        use_armature_deform_only=True,
        mesh_smooth_type='FACE',
        use_mesh_modifiers=False,  # keeps the shape keys (morph targets)
        colors_type='SRGB',
        bake_anim=False,
    )
    print(f"Exported {fbx_path}")


def save_blend(blend_path):
    """Saves the working scene (body + fitted part) as a NEW file for inspection."""
    os.makedirs(os.path.dirname(blend_path), exist_ok=True)
    if os.path.abspath(blend_path).startswith(os.path.abspath(BASE_DIR)):
        raise SystemExit("Refusing to write into Art/Characters/Base")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path, copy=True)
    print(f"Saved {blend_path}")


def verify_fbx(fbx_path, body, surface, expected_bones, expected_keys, min_clearance=0.0005):
    """Re-imports the FBX and checks skeleton names, weights, morphs and body intersection."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=fbx_path, use_custom_normals=False)
    new = [o for o in bpy.data.objects if o not in before]
    arm = next(o for o in new if o.type == 'ARMATURE')
    mesh = next(o for o in new if o.type == 'MESH')
    bpy.context.view_layer.update()
    problems = []

    bones = {b.name for b in arm.data.bones}
    if bones != expected_bones:
        problems.append(f"bone names differ: missing {sorted(expected_bones - bones)}, extra {sorted(bones - expected_bones)}")
    groups = {g.name for g in mesh.vertex_groups}
    if not groups <= expected_bones:
        problems.append(f"vertex groups that are not bones: {sorted(groups - expected_bones)}")
    unweighted = count_unweighted(mesh)
    if unweighted:
        problems.append(f"{unweighted} unweighted vertices")
    key_blocks = mesh.data.shape_keys.key_blocks[1:] if mesh.data.shape_keys else []
    keys = [k.name for k in key_blocks]
    if sorted(keys) != sorted(expected_keys):
        problems.append(f"morph targets: got {len(keys)}, expected {len(expected_keys)}")
    live = [k.name for k in key_blocks if abs(k.value) > 1e-6]
    if live:
        problems.append(f"morph targets stored with a non-zero value: {live[:5]}")

    mw = mesh.matrix_world
    inside = 0
    worst = float("inf")
    for v in mesh.data.vertices:
        d = surface.signed_distance(mw @ v.co)
        worst = min(worst, d)
        inside += d < -min_clearance
    for p in mesh.data.polygons:
        d = surface.signed_distance(mw @ p.center)
        worst = min(worst, d)
        inside += d < -min_clearance
    if inside:
        problems.append(f"{inside} vertex/face-centre samples inside the body (worst {worst * 1000:.1f} mm)")

    tris = sum(len(p.vertices) - 2 for p in mesh.data.polygons)
    print(f"Verify {os.path.basename(fbx_path)}: {len(bones)} bones (names match: {bones == expected_bones}), "
          f"{len(mesh.data.vertices)} verts / {tris} tris, {unweighted} unweighted, "
          f"{len(keys)} morph targets, {inside} samples inside the body, "
          f"closest sample {worst * 1000:.1f} mm from the skin")
    for p in problems:
        print("  PROBLEM: " + p)
    for o in new:
        bpy.data.objects.remove(o)
    return not problems


# ----------------------------------------------------------------------------- main

def parse(argv):
    ap = argparse.ArgumentParser(description="Fit a garment onto a base body")
    ap.add_argument("garment", help="OBJ / GLB / FBX from Tripo (unrigged)")
    ap.add_argument("body", help="Female or Male")
    ap.add_argument("--slot", choices=sorted(SLOTS), default="torso")
    ap.add_argument("--name", default=None, help="Mesh / file name, e.g. SK_Shirt_Test")
    ap.add_argument("--out", default=TEST_DIR, help="Output folder (default Art/Characters/Test)")
    ap.add_argument("--offset", type=float, default=None, help="Shrinkwrap offset in metres (slot default)")
    ap.add_argument("--contact", type=float, default=0.02, help="Verts closer than this to the skin are contact verts")
    ap.add_argument("--shell-gap", type=float, default=0.015, help="Max thickness of an inner shell to strip")
    ap.add_argument("--scale", type=float, default=None, help="Force this uniform scale instead of the landmark fit")
    ap.add_argument("--scale-axis", choices=("X", "Y", "Z"), default=None, help="Bbox axis used for the scale")
    ap.add_argument("--no-align", action="store_true", help="Garment is already placed on the body")
    ap.add_argument("--keep-shell", action="store_true", help="Skip the inner-shell strip")
    ap.add_argument("--colour", default="0.35,0.42,0.6", help="Placeholder material colour r,g,b")
    ap.add_argument("--no-verify", action="store_true")
    return ap.parse_args(argv)


def fit_garment(args):
    slot = SLOTS[args.slot]
    name = args.name or f"SK_{os.path.splitext(os.path.basename(args.garment))[0]}"
    body, armature = open_body(args.body)
    bones = bone_names(armature)
    surface = BodySurface(body)
    garment = import_mesh(os.path.abspath(args.garment), name)
    offset = slot["offset"] if args.offset is None else args.offset

    if not args.no_align:
        lo, hi = region_bbox(body, surface, slot["bones"], bones)
        align_to_region(garment, lo, hi, args.scale_axis or slot["scale_axis"], slot["anchor"],
                        pad=offset, scale=args.scale)
    fix_normals(garment, surface)
    if not args.keep_shell:
        strip_inner_shell(garment, surface, args.shell_gap)
    wrap_to_body(garment, body, surface, offset, args.contact)
    transfer_weights(garment, body, bones)
    keys = slider_key_names(body)
    transfer_shape_keys(garment, body, surface, keys)
    add_hide_mask(garment)
    colour = tuple(float(c) for c in args.colour.split(",")) + (1.0,)
    placeholder_material(garment, f"M_{name[3:] if name.startswith('SK_') else name}", colour)
    parent_to_armature(garment, armature)
    hidden = report_coverage(garment, body, surface)

    fbx_path = os.path.join(args.out, name + ".fbx")
    blend_path = os.path.join(args.out, name + ".blend")
    export_fbx(garment, armature, fbx_path)
    save_blend(blend_path)
    ok = True
    if not args.no_verify:
        ok = verify_fbx(fbx_path, body, surface, bones, keys)
    tris = sum(len(p.vertices) - 2 for p in garment.data.polygons)
    print(f"Done: {name} ({tris} tris), slot {args.slot}, hides {hidden or 'nothing'}, verified: {ok}")
    return ok


def main():
    args = parse(script_args())
    ok = fit_garment(args)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
