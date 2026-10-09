"""
Builds the ZombieMMO player base bodies (male and female) with MPFB and exports them for Unreal.

Output (relative to the repo root), for each of Male and Female:
    Art/Characters/Base/SK_BaseBody_<Body>.fbx   skeletal mesh + face morph targets
    Art/Characters/Base/BaseBody_<Body>.blend    editable Blender scene

What you get per body:
    - One mesh, T-pose, feet at 0 (male ~1.79 m, female ~1.66 m)
    - Both bodies use the same skeleton, so animations and clothes setups are shared
    - Unreal-style skeleton (root, pelvis, spine_01..03, thigh_l, ...)
    - Eyes kept as part of the mesh so face morphs move them with the sockets
    - Material slots: M_Skin, M_Eyes, M_Underwear (plain briefs, plus a sports bra on the
      female, painted onto the body)
    - Face customisation morph targets (FaceSlider names below)
    - Vertex colours used as a "hide under clothes" mask:
          R = torso, G = arms, B = legs, A = feet (underwear areas are never hidden)

Requirements: Blender 4.2+ with the MPFB extension installed and enabled.

Run:
    blender -b -P Tools/Blender/build_base_body.py
"""

import os
import sys

import bpy  # noqa: E402  (bpy must load before bmesh)
import bmesh

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(REPO_ROOT, "Art", "Characters", "Base")

# Body shapes. 0..1, 0.5 is average. Age 0.5 is ~25 years; proportions above 0.5 are
# slightly idealised, which suits a stylised comic look.
BODIES = {
    "Male": {
        "gender": 1.0, "age": 0.5, "muscle": 0.6, "weight": 0.5, "proportions": 0.7,
        "height": 0.55, "cupsize": 0.5, "firmness": 0.5,
        "race": {"asian": 0.33, "caucasian": 0.34, "african": 0.33},
    },
    "Female": {
        "gender": 0.0, "age": 0.5, "muscle": 0.55, "weight": 0.5, "proportions": 0.7,
        "height": 0.5, "cupsize": 0.5, "firmness": 0.6,
        "race": {"asian": 0.33, "caucasian": 0.34, "african": 0.33},
    },
}

# Morph target name -> list of MPFB target files that are blended at 1.0 to make it.
# Pairs named _Decr/_Incr (or _Down/_Up etc.) drive one slider from -1 to +1 in Unreal.
FACE_MORPHS = {
    "Nose_Narrow": ["nose/nose-scale-horiz-decr"],
    "Nose_Wide": ["nose/nose-scale-horiz-incr"],
    "Nose_Short": ["nose/nose-scale-vert-decr"],
    "Nose_Long": ["nose/nose-scale-vert-incr"],
    "Nose_HumpLess": ["nose/nose-hump-decr"],
    "Nose_HumpMore": ["nose/nose-hump-incr"],
    "Nose_TipDown": ["nose/nose-point-down"],
    "Nose_TipUp": ["nose/nose-point-up"],
    "Nose_NostrilsNarrow": ["nose/nose-flaring-decr"],
    "Nose_NostrilsWide": ["nose/nose-flaring-incr"],
    "Jaw_Narrow": ["chin/chin-width-decr"],
    "Jaw_Wide": ["chin/chin-width-incr"],
    "Jaw_AngleSoft": ["chin/chin-bones-decr"],
    "Jaw_AngleStrong": ["chin/chin-bones-incr"],
    "Chin_Back": ["chin/chin-prominent-decr"],
    "Chin_Forward": ["chin/chin-prominent-incr"],
    "Chin_Short": ["chin/chin-height-decr"],
    "Chin_Long": ["chin/chin-height-incr"],
    "Cheekbones_Low": ["cheek/l-cheek-bones-decr", "cheek/r-cheek-bones-decr"],
    "Cheekbones_High": ["cheek/l-cheek-bones-incr", "cheek/r-cheek-bones-incr"],
    "Cheeks_Thin": ["cheek/l-cheek-volume-decr", "cheek/r-cheek-volume-decr"],
    "Cheeks_Full": ["cheek/l-cheek-volume-incr", "cheek/r-cheek-volume-incr"],
    "Eyes_Small": ["eyes/l-eye-scale-decr", "eyes/r-eye-scale-decr"],
    "Eyes_Big": ["eyes/l-eye-scale-incr", "eyes/r-eye-scale-incr"],
    "Eyes_Down": ["eyes/l-eye-trans-down", "eyes/r-eye-trans-down"],
    "Eyes_Up": ["eyes/l-eye-trans-up", "eyes/r-eye-trans-up"],
    "Eyes_Close": ["eyes/l-eye-trans-in", "eyes/r-eye-trans-in"],
    "Eyes_Apart": ["eyes/l-eye-trans-out", "eyes/r-eye-trans-out"],
    "Brows_Down": ["eyebrows/eyebrows-trans-down"],
    "Brows_Up": ["eyebrows/eyebrows-trans-up"],
    "Brows_AngleDown": ["eyebrows/eyebrows-angle-down"],
    "Brows_AngleUp": ["eyebrows/eyebrows-angle-up"],
    "Mouth_Narrow": ["mouth/mouth-scale-horiz-decr"],
    "Mouth_Wide": ["mouth/mouth-scale-horiz-incr"],
    "Mouth_CornersDown": ["mouth/mouth-angles-down"],
    "Mouth_CornersUp": ["mouth/mouth-angles-up"],
    "UpperLip_Thin": ["mouth/mouth-upperlip-volume-decr"],
    "UpperLip_Full": ["mouth/mouth-upperlip-volume-incr"],
    "LowerLip_Thin": ["mouth/mouth-lowerlip-volume-decr"],
    "LowerLip_Full": ["mouth/mouth-lowerlip-volume-incr"],
    "Ears_Small": ["ears/l-ear-scale-decr", "ears/r-ear-scale-decr"],
    "Ears_Big": ["ears/l-ear-scale-incr", "ears/r-ear-scale-incr"],
    "Ears_Pointed": ["ears/l-ear-shape-pointed", "ears/r-ear-shape-pointed"],
    "Head_Narrow": ["head/head-scale-horiz-decr"],
    "Head_Wide": ["head/head-scale-horiz-incr"],
    "Head_Round": ["head/head-round"],
    "Head_Square": ["head/head-square"],
    "Forehead_Back": ["forehead/forehead-trans-backward"],
    "Forehead_Forward": ["forehead/forehead-trans-forward"],
    "Neck_Thin": ["neck/neck-scale-horiz-decr"],
    "Neck_Thick": ["neck/neck-scale-horiz-incr"],
}

# Vertex colour mask channels, chosen by the bone with the highest weight on each vertex.
MASK_BONES = {
    0: ("pelvis", "spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r"),  # R torso
    1: ("upperarm_l", "upperarm_r", "lowerarm_l", "lowerarm_r"),  # G arms
    2: ("thigh_l", "thigh_r", "calf_l", "calf_r"),  # B legs
    3: ("foot_l", "foot_r", "ball_l", "ball_r"),  # A feet
}

UNDERWEAR_BONES = ("pelvis", "spine_01", "thigh_l", "thigh_r")
BRA_BONES = ("spine_02", "spine_03", "clavicle_l", "clavicle_r")

EYE_GROUPS = ("helper-l-eye", "helper-r-eye")


def mpfb_module(name):
    for mod_name, mod in sys.modules.items():
        if mod_name.endswith("mpfb.services." + name):
            return mod
    raise RuntimeError("MPFB is not enabled. Install the MPFB extension in Blender first.")


def enable_mpfb():
    import addon_utils
    for module in ("bl_ext.user_default.mpfb", "bl_ext.blender_org.mpfb", "mpfb"):
        try:
            if addon_utils.enable(module, default_set=True):
                return
        except Exception:
            pass


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def build_human(macros):
    HumanService = mpfb_module("humanservice").HumanService
    TargetService = mpfb_module("targetservice").TargetService

    basemesh = HumanService.create_human(macro_detail_dict=macros)
    # Bake the body shape so face morphs are deltas on top of this exact body.
    TargetService.bake_targets(basemesh)
    return basemesh


def add_face_morphs(basemesh):
    TargetService = mpfb_module("targetservice").TargetService
    LocationService = mpfb_module("locationservice").LocationService
    targets_dir = LocationService.get_mpfb_data("targets")

    basemesh.shape_key_add(name="Basis", from_mix=False)
    for morph_name, parts in FACE_MORPHS.items():
        loaded = []
        for part in parts:
            path = os.path.join(targets_dir, part + ".target.gz")
            loaded.append(TargetService.load_target(basemesh, path, weight=1.0, name="tmp_" + os.path.basename(part)))
        if len(loaded) == 1:
            loaded[0].name = morph_name
            loaded[0].value = 0.0
            continue
        # Combine left/right halves into one symmetrical morph.
        for key in basemesh.data.shape_keys.key_blocks:
            key.value = 1.0 if key in loaded else 0.0
        combined = basemesh.shape_key_add(name=morph_name, from_mix=True)
        combined.value = 0.0
        for key in loaded:
            basemesh.shape_key_remove(key)


def add_rig(basemesh):
    HumanService = mpfb_module("humanservice").HumanService
    armature = HumanService.add_builtin_rig(basemesh, "game_engine")
    # Unreal skips the armature object as a bone when it is called "Armature".
    armature.name = "Armature"
    armature.data.name = "Armature"
    root = armature.data.bones.get("Root")
    if root:
        root.name = "root"
    if "Root" in basemesh.vertex_groups:
        basemesh.vertex_groups["Root"].name = "root"
    return armature


def pose_to_t_pose(basemesh, armature):
    """Raise the arms from MPFB's A-pose to a T-pose and make that the rest pose.

    Shape keys are re-captured in the new pose so every face morph still lines up.
    """
    from mathutils import Matrix, Vector

    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='POSE')
    for side, sign in (("l", 1.0), ("r", -1.0)):
        for name in (f"upperarm_{side}", f"lowerarm_{side}", f"hand_{side}"):
            pb = armature.pose.bones[name]
            bpy.context.view_layer.update()
            head = pb.head.copy()
            rot = (pb.tail - pb.head).rotation_difference(Vector((sign, 0.0, 0.0)))
            pb.matrix = Matrix.Translation(head) @ rot.to_matrix().to_4x4() @ Matrix.Translation(-head) @ pb.matrix
    bpy.context.view_layer.update()

    # Capture the posed basis and every posed shape key.
    keys = basemesh.data.shape_keys.key_blocks
    basemesh.show_only_shape_key = True
    captured = []
    for i in range(len(keys)):
        basemesh.active_shape_key_index = i
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = basemesh.evaluated_get(depsgraph)
        captured.append([v.co.copy() for v in evaluated.to_mesh().vertices])
        evaluated.to_mesh_clear()
    basemesh.show_only_shape_key = False
    basemesh.active_shape_key_index = 0

    for key, coords in zip(keys, captured):
        for point, co in zip(key.data, coords):
            point.co = co
    for vert, co in zip(basemesh.data.vertices, captured[0]):
        vert.co = co

    bpy.ops.pose.armature_apply(selected=False)
    bpy.ops.object.mode_set(mode='OBJECT')


def bust_height(basemesh):
    """Average height of the nipple area, used to place the sports bra."""
    idx = basemesh.vertex_groups["nipple"].index
    zs = [v.co.z for v in basemesh.data.vertices if any(g.group == idx for g in v.groups)]
    return sum(zs) / len(zs)


def strip_helpers_keep_eyes(basemesh, bone_names):
    """Delete helper/joint geometry except the eyes, and drop non-bone vertex groups."""
    groups = basemesh.vertex_groups
    body_idx = groups["body"].index
    eye_idx = {groups[g].index for g in EYE_GROUPS}

    keep = set()
    eye_verts = set()
    for v in basemesh.data.vertices:
        gids = {g.group for g in v.groups if g.weight > 0.0}
        if body_idx in gids:
            keep.add(v.index)
        elif gids & eye_idx:
            keep.add(v.index)
            eye_verts.add(v.index)

    for mod in list(basemesh.modifiers):
        if mod.type == 'MASK':
            basemesh.modifiers.remove(mod)

    # Eyes follow the head bone only.
    head = groups.get("head")
    for gname in [g.name for g in groups]:
        if gname not in bone_names:
            continue
        groups[gname].remove(list(eye_verts))
    head.add(list(eye_verts), 1.0, 'REPLACE')

    # bmesh keeps shape keys when deleting vertices.
    bm = bmesh.new()
    bm.from_mesh(basemesh.data)
    bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.index not in keep], context='VERTS')
    bm.to_mesh(basemesh.data)
    bm.free()
    basemesh.data.update()

    for g in list(basemesh.vertex_groups):
        if g.name not in bone_names:
            basemesh.vertex_groups.remove(g)

    return eye_verts


def dominant_bone(vertex, name_by_index):
    best = max(vertex.groups, key=lambda g: g.weight, default=None)
    return name_by_index[best.group] if best is not None else None


def find_underwear_verts(basemesh, armature, bust_z=None):
    """Plain briefs: waist down to the upper thighs, with the leg edge following each thigh.

    With bust_z set, also a sports bra band around the chest.
    """
    bones = armature.data.bones
    top = bones["pelvis"].head_local.z + 0.035
    leg_length = 0.16  # metres down the thigh bone
    name_by_index = {g.index: g.name for g in basemesh.vertex_groups}
    result = set()
    for v in basemesh.data.vertices:
        bone = dominant_bone(v, name_by_index)
        if bust_z is not None and bone in BRA_BONES and bust_z - 0.075 <= v.co.z <= bust_z + 0.07:
            result.add(v.index)
            continue
        if bone not in UNDERWEAR_BONES or v.co.z > top:
            continue
        thigh = bones["thigh_l" if v.co.x > 0 else "thigh_r"]
        axis = (thigh.tail_local - thigh.head_local).normalized()
        if (v.co - thigh.head_local).dot(axis) > leg_length:
            continue
        result.add(v.index)
    return result


def assign_materials(basemesh, underwear_verts):
    materials = []
    for name, colour in (("M_Skin", (0.80, 0.62, 0.52, 1.0)),
                         ("M_Eyes", (0.95, 0.95, 0.95, 1.0)),
                         ("M_Underwear", (0.18, 0.18, 0.20, 1.0))):
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = colour
        mat.use_nodes = True
        mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = colour
        materials.append(mat)
    basemesh.data.materials.clear()
    for mat in materials:
        basemesh.data.materials.append(mat)

    bm = bmesh.new()
    bm.from_mesh(basemesh.data)
    bm.faces.ensure_lookup_table()
    # The body is one big connected island; each eye is a small separate one.
    seen = set()
    for f in bm.faces:
        if f.index in seen:
            continue
        island, stack = [], [f]
        seen.add(f.index)
        while stack:
            cur = stack.pop()
            island.append(cur)
            for e in cur.edges:
                for nf in e.link_faces:
                    if nf.index not in seen:
                        seen.add(nf.index)
                        stack.append(nf)
        if len(island) < 2000:
            for face in island:
                face.material_index = 1
    for f in bm.faces:
        if f.material_index == 0 and all(v.index in underwear_verts for v in f.verts):
            f.material_index = 2
    bm.to_mesh(basemesh.data)
    bm.free()


def paint_hide_mask(basemesh, underwear_verts):
    """Underwear is never hidden, so a shirt with no trousers never leaves a hole."""
    mesh = basemesh.data
    attr = mesh.color_attributes.new("HideMask", 'BYTE_COLOR', 'POINT')
    name_by_index = {g.index: g.name for g in basemesh.vertex_groups}
    channel_by_bone = {b: ch for ch, bones in MASK_BONES.items() for b in bones}
    for v in mesh.vertices:
        rgba = [0.0, 0.0, 0.0, 0.0]
        ch = channel_by_bone.get(dominant_bone(v, name_by_index))
        if ch is not None and v.index not in underwear_verts:
            rgba[ch] = 1.0
        attr.data[v.index].color = rgba
    mesh.color_attributes.active_color = attr
    mesh.color_attributes.render_color_index = mesh.color_attributes.find("HideMask")


def export(basemesh, armature, fbx_path, blend_path, mesh_name):
    os.makedirs(OUT_DIR, exist_ok=True)
    basemesh.name = mesh_name
    basemesh.data.name = mesh_name
    bpy.ops.object.select_all(action='DESELECT')
    armature.select_set(True)
    basemesh.select_set(True)
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
        use_mesh_modifiers=False,
        colors_type='SRGB',
        bake_anim=False,
    )
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)


def build_body(body_name, macros):
    mesh_name = f"SK_BaseBody_{body_name}"
    fbx_path = os.path.join(OUT_DIR, mesh_name + ".fbx")
    blend_path = os.path.join(OUT_DIR, f"BaseBody_{body_name}.blend")

    clear_scene()
    enable_mpfb()
    basemesh = build_human(macros)
    add_face_morphs(basemesh)
    armature = add_rig(basemesh)
    bone_names = {b.name for b in armature.data.bones}
    bust_z = bust_height(basemesh) if macros["gender"] < 0.5 else None
    strip_helpers_keep_eyes(basemesh, bone_names)
    pose_to_t_pose(basemesh, armature)
    underwear_verts = find_underwear_verts(basemesh, armature, bust_z)
    assign_materials(basemesh, underwear_verts)
    paint_hide_mask(basemesh, underwear_verts)
    export(basemesh, armature, fbx_path, blend_path, mesh_name)

    tris = sum(len(p.vertices) - 2 for p in basemesh.data.polygons)
    height = max(v.co.z for v in basemesh.data.vertices) - min(v.co.z for v in basemesh.data.vertices)
    print(f"Exported {fbx_path}")
    print(f"  triangles: {tris}, height: {height:.3f} m, bones: {len(bone_names)}, "
          f"morphs: {len(basemesh.data.shape_keys.key_blocks) - 1}")


def main():
    # Optional: pass body names after "--" to build only those, e.g. -- Female
    wanted = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else list(BODIES)
    for body_name in wanted:
        build_body(body_name, BODIES[body_name])


if __name__ == "__main__":
    main()
