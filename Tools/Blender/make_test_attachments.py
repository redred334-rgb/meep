"""
Makes procedural stand-ins for Tripo clothes and hair from the Female base body, so the
fitting tools can be tested before any Tripo asset exists. Then (with --sheet) renders the
end-to-end check sheet from the fitted FBX files.

    python3 Tools/Blender/make_test_attachments.py            # writes Art/Characters/Test/*.obj
    python3 Tools/Blender/make_test_attachments.py --sheet    # Art/Characters/Test/Previews/Fit_Test.png

Test assets (unrigged OBJ, like a Tripo export, deliberately mis-scaled and offset so the
fitting has real work to do):
    Test_Shirt.obj         torso + upper-arm skin pushed out 8 mm, sleeve hems and collar lip
    Test_Shirt_Solid.obj   the same shirt as a closed volume (inner shell 2 mm from the skin)
    Test_Trousers.obj      hip to ankle pushed out 6 mm, waist and ankle hems
    Test_Hair.obj          scalp pushed out 15 mm with clump bumps, closed volume (underside at 3 mm)

The Female .blend is opened read-only and never saved.
"""

import math
import os
import sys

import bpy  # noqa: E402  (bpy must load before bmesh)
import bmesh
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fit_garment as fg  # noqa: E402

TEST_DIR = fg.TEST_DIR
PREVIEW_DIR = os.path.join(TEST_DIR, "Previews")

SHIRT_BONES = ("spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r", "upperarm_l", "upperarm_r")
TROUSER_BONES = ("pelvis", "thigh_l", "thigh_r", "calf_l", "calf_r")

# name -> (misalignment scale, misalignment offset in metres)
MISALIGN = {
    "Test_Shirt": (0.97, (0.02, 0.0, 0.02)),
    "Test_Shirt_Solid": (0.97, (0.02, 0.0, 0.02)),
    "Test_Trousers": (1.04, (-0.01, 0.02, -0.02)),
    "Test_Hair": (0.90, (0.015, -0.02, 0.02)),
}


# ----------------------------------------------------------------------------- building

def select_faces(body, keep_vertex, mode=all):
    """Skin faces (material 0) whose vertices all (or any, mode=any) pass keep_vertex(index, co)."""
    mesh = body.data
    basis = mesh.shape_keys.key_blocks[0].data
    ok = [keep_vertex(v.index, basis[v.index].co) for v in mesh.vertices]
    return [p for p in mesh.polygons if p.material_index == 0 and mode(ok[i] for i in p.vertices)]


def shell_from_faces(body, faces, name, push, hem=0.0):
    """New object made of copies of `faces`, each vertex pushed out along the body normal by
    push(index, co) metres; boundary edges get a hem lip folded back toward the skin."""
    mesh = body.data
    basis = mesh.shape_keys.key_blocks[0].data
    bm = bmesh.new()
    verts = {}
    for p in faces:
        for i in p.vertices:
            if i not in verts:
                co = basis[i].co
                verts[i] = bm.verts.new(co + mesh.vertices[i].normal * push(i, co))
    for p in faces:
        try:
            bm.faces.new([verts[i] for i in p.vertices])
        except ValueError:
            pass
    bm.normal_update()
    if hem > 0:
        edges = [e for e in bm.edges if e.is_boundary]
        res = bmesh.ops.extrude_edge_only(bm, edges=edges)
        new_verts = [g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)]
        for v in new_verts:
            _, normal, _, _ = SURFACE.nearest(v.co)
            v.co -= normal * hem
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    data = bpy.data.meshes.new(name)
    bm.to_mesh(data)
    bm.free()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def clumps(i, co):
    """15 mm cap plus +-6 mm bumps so the hair looks like sculpted clumps."""
    return 0.015 + 0.006 * math.sin(co.x * 95.0) * math.sin(co.z * 80.0 + co.y * 40.0) + 0.003 * math.sin(co.y * 120.0)


def make_solid(shell, name, thickness):
    """Closed-volume copy: Solidify adds an inner shell `thickness` inward."""
    solid = shell.copy()
    solid.data = shell.data.copy()
    solid.name = solid.data.name = name
    bpy.context.scene.collection.objects.link(solid)
    mod = solid.modifiers.new("solid", 'SOLIDIFY')
    mod.thickness = thickness
    mod.offset = -1.0
    mod.use_rim = True
    fg.set_active(solid)
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return solid


def export_obj(obj, scale, offset):
    """Applies the deliberate misalignment and writes an unrigged, triangulated OBJ."""
    obj.data.transform(Matrix.Translation(Vector(offset)) @ Matrix.Scale(scale, 4))
    obj.data.update()
    path = os.path.join(TEST_DIR, obj.name + ".obj")
    fg.set_active(obj)
    bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True, export_materials=False,
                          export_triangulated_mesh=True, export_normals=False, export_uv=False)
    tris = sum(len(p.vertices) - 2 for p in obj.data.polygons)
    print(f"Wrote {path}: {len(obj.data.vertices)} verts, {tris} tris, scaled x{scale}, offset {offset}")


def make_assets():
    global SURFACE
    os.makedirs(TEST_DIR, exist_ok=True)
    body, armature = fg.open_body("Female")
    SURFACE = fg.BodySurface(body)
    bones = fg.bone_names(armature)
    dom = fg.dominant_bone(body, bones)
    upper = armature.data.bones["upperarm_l"]
    sleeve_x = upper.head_local.x + 0.55 * (upper.tail_local.x - upper.head_local.x)
    scalp = body.vertex_groups["mask_scalp"].index

    def shirt_vert(i, co):
        return dom[i] in SHIRT_BONES and abs(co.x) < sleeve_x

    def trouser_vert(i, co):
        return dom[i] in TROUSER_BONES

    def scalp_vert(i, co):
        return any(g.group == scalp and g.weight > 0.5 for g in body.data.vertices[i].groups)

    shirt = shell_from_faces(body, select_faces(body, shirt_vert), "Test_Shirt", lambda i, co: 0.008, hem=0.005)
    solid = make_solid(shirt, "Test_Shirt_Solid", 0.006)  # inner shell 2 mm from the skin
    trousers = shell_from_faces(body, select_faces(body, trouser_vert, any), "Test_Trousers", lambda i, co: 0.006, hem=0.004)
    cap = shell_from_faces(body, select_faces(body, scalp_vert), "Test_Hair_Shell", clumps, hem=0.0)
    hair = make_solid(cap, "Test_Hair", 0.012)  # closed clump volume, underside 3 mm from the scalp
    for obj in (shirt, solid, trousers, hair):
        export_obj(obj, *MISALIGN[obj.name])


# ----------------------------------------------------------------------------- check sheet

PARTS = {  # fbx name -> render colour
    "SK_Shirt_Test": (0.75, 0.18, 0.16, 1.0),
    "SK_Trousers_Test": (0.2, 0.3, 0.6, 1.0),
    "SK_Hair_Test": (0.35, 0.2, 0.1, 1.0),
}


def import_fbx(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    return next(o for o in new if o.type == 'ARMATURE'), next(o for o in new if o.type == 'MESH')


def flat_material(name, colour):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = colour
    bsdf.inputs["Roughness"].default_value = 0.9
    bsdf.inputs["Specular IOR Level"].default_value = 0.1
    return mat


def skin_material_with_hide_mask(colour, hide):
    """What the Unreal skin material instance does: skin vertices whose HideMask channel is
    set for a hidden slot are not drawn. `hide` is a set of channel indices (0 torso, 1 arms,
    2 legs, 3 feet)."""
    mat = flat_material("skin", colour)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf, out = nodes["Principled BSDF"], nodes["Material Output"]
    vcol = nodes.new("ShaderNodeVertexColor")
    vcol.layer_name = "HideMask"
    split = nodes.new("ShaderNodeSeparateColor")
    links.new(vcol.outputs["Color"], split.inputs["Color"])
    channels = [split.outputs["Red"], split.outputs["Green"], split.outputs["Blue"], vcol.outputs["Alpha"]]
    fac = None
    for c in sorted(hide):
        if fac is None:
            fac = channels[c]
            continue
        add = nodes.new("ShaderNodeMath")
        add.operation = 'MAXIMUM'
        links.new(fac, add.inputs[0])
        links.new(channels[c], add.inputs[1])
        fac = add.outputs[0]
    mix = nodes.new("ShaderNodeMixShader")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    if fac is not None:
        links.new(fac, mix.inputs["Fac"])
    links.new(bsdf.outputs[0], mix.inputs[1])
    links.new(transparent.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def rotate_bone_world(armature, name, axis, degrees):
    """Rotates a pose bone about a world axis through its head (works whatever the roll)."""
    pb = armature.pose.bones[name]
    bpy.context.view_layer.update()
    m = pb.matrix.copy()
    pivot = Matrix.Translation(m.translation)
    rot = Matrix.Rotation(math.radians(degrees), 4, axis)
    pb.matrix = pivot @ rot @ pivot.inverted() @ m
    bpy.context.view_layer.update()


def set_keys(meshes, values):
    for obj in meshes:
        if not obj.data.shape_keys:
            continue
        for k in obj.data.shape_keys.key_blocks[1:]:
            k.value = values.get(k.name, 0.0)


def setup_render(scene):
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 48
    scene.cycles.use_denoising = True
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = 'AgX'
    world = bpy.data.worlds.new("w")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.22, 0.24, 0.27, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.7
    for rot, energy in (((math.radians(50), 0, math.radians(30)), 2.5), ((math.radians(70), 0, math.radians(-140)), 1.2)):
        light = bpy.data.lights.new("sun", 'SUN')
        light.energy = energy
        lo = bpy.data.objects.new("sun", light)
        lo.rotation_euler = rot
        scene.collection.objects.link(lo)
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


def shot(scene, cam, path, loc, target, lens, size):
    cam.location = Vector(loc)
    cam.rotation_euler = (Vector(target) - cam.location).to_track_quat('-Z', 'Y').to_euler()
    cam.data.lens = lens
    scene.render.resolution_x, scene.render.resolution_y = size
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def render_sheet():
    """Re-imports the body and fitted part FBX files (what Unreal would get), drives the parts
    from the body's armature like a Leader Pose Component and renders the check sheet."""
    from PIL import Image, ImageDraw

    os.makedirs(PREVIEW_DIR, exist_ok=True)
    tmp = os.path.join(PREVIEW_DIR, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    body_arm, body = import_fbx(os.path.join(fg.BASE_DIR, "SK_BaseBody_Female.fbx"))
    # The shirt reports HideTorso, the trousers HideLegs: hide those skin regions like Unreal does.
    body.data.materials[0] = skin_material_with_hide_mask((0.8, 0.62, 0.52, 1.0), {0, 2})
    parts = []
    for name, colour in PARTS.items():
        path = os.path.join(TEST_DIR, name + ".fbx")
        if not os.path.exists(path):
            print(f"Missing {path}, run the fit scripts first")
            continue
        arm, mesh = import_fbx(path)
        for mod in mesh.modifiers:  # Leader Pose: every part follows the body's skeleton
            if mod.type == 'ARMATURE':
                mod.object = body_arm
        mesh.parent = body_arm
        bpy.data.objects.remove(arm)
        mesh.data.materials.clear()
        mesh.data.materials.append(flat_material(name, colour))
        parts.append(mesh)
    cam = setup_render(scene)
    meshes = [body] + parts

    # 1. T-pose, body + all parts
    shot(scene, cam, os.path.join(tmp, "tpose.png"), (0, -4.0, 0.95), (0, 0, 0.85), 60, (700, 1000))
    # 2. bent pose: thigh and arm 45 degrees, elbow and knee too
    rotate_bone_world(body_arm, "thigh_l", 'X', -45)
    rotate_bone_world(body_arm, "calf_l", 'X', 45)
    rotate_bone_world(body_arm, "upperarm_l", 'Y', 45)
    rotate_bone_world(body_arm, "lowerarm_l", 'Z', 45)
    rotate_bone_world(body_arm, "upperarm_r", 'Y', -45)
    shot(scene, cam, os.path.join(tmp, "bent.png"), (2.4, -3.2, 1.1), (0, 0, 0.85), 60, (700, 1000))
    for pb in body_arm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    # 3. head: neutral, then Jaw_Wide=1 + Head_Square=1 (hair must follow the head sliders)
    head_cam, head_at = (0.55, -0.8, 1.6), (0, 0, 1.5)
    shot(scene, cam, os.path.join(tmp, "head0.png"), head_cam, head_at, 70, (700, 500))
    set_keys(meshes, {"Jaw_Wide": 1.0, "Head_Square": 1.0})
    shot(scene, cam, os.path.join(tmp, "head1.png"), head_cam, head_at, 70, (700, 500))
    set_keys(meshes, {})

    sheet = Image.new("RGB", (2100, 1100), (30, 32, 36))
    draw = ImageDraw.Draw(sheet)
    sheet.paste(Image.open(os.path.join(tmp, "tpose.png")), (0, 60))
    sheet.paste(Image.open(os.path.join(tmp, "bent.png")), (700, 60))
    sheet.paste(Image.open(os.path.join(tmp, "head0.png")), (1400, 60))
    sheet.paste(Image.open(os.path.join(tmp, "head1.png")), (1400, 600))
    for text, xy in (("T-pose: body + shirt + trousers + hair (re-imported FBX, skin hidden by HideMask R+B)", (10, 20)),
                     ("Bent pose: thigh/arm 45 deg, parts driven by the body skeleton", (710, 20)),
                     ("Head neutral", (1410, 20)), ("Jaw_Wide=1, Head_Square=1 (hair follows)", (1410, 572))):
        draw.text(xy, text, fill=(235, 235, 235))
    out = os.path.join(PREVIEW_DIR, "Fit_Test.png")
    sheet.save(out)
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    print(f"Wrote {out}")


def main():
    args = fg.script_args()
    if "--sheet" in args:
        render_sheet()
    else:
        make_assets()


if __name__ == "__main__":
    main()
