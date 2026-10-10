"""
Paints comic-style (Borderlands / Telltale) textures for the player base bodies.

Run after build_base_body.py:
    blender -b -P Tools/Blender/paint_base_body.py            # both bodies
    blender -b -P Tools/Blender/paint_base_body.py -- Male    # just one

How it works: Blender bakes what the body looks like in 3D (position, facing direction,
creases and shadowed areas) into images on the body's UV layout. The painting itself is then
done with numpy in 3D space, so strokes and lines carry straight across UV seams. That covers
skin colour zones, brush mottling, ink lines in creases, hatching in shadow, brows, beard,
scalp hair, lips, nails, painted underwear and the eyes.

Output in Art/Characters/Base/Textures/ (per body; "BL" = Borderlands, "TT" = Telltale):
    T_Body_<Body>_BaseColor_BL.png / _TT.png   painted colour with the default skin tone
    T_Body_<Body>_Masks.png                    R = skin (tint), G = hair (tint), B = underwear (tint)
                                               (project_reference.py rewrites it to match the drawn texture)
    T_Eyes_BaseColor.png, T_Eyes_Masks.png     eyes; Masks R = iris (tint)

Default colours (Unreal divides by these when tinting, see Tools/Unreal/setup_comic_look.py):
    see SKIN / HAIR / UNDERWEAR / IRIS in comic_paint.py (sRGB).
"""

import os
import sys

import bpy  # noqa: E402  (bpy must load before bmesh)
import bmesh
import numpy as np
from PIL import Image

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE_DIR = os.path.join(REPO_ROOT, "Art", "Characters", "Base")
TEX_DIR = os.path.join(BASE_DIR, "Textures")

BODY_RES = int(os.environ.get("PAINT_RES", 4096))
MASK_RES = 2048
EYE_RES = 1024

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from comic_paint import (  # noqa: E402
    SKIN, HAIR, UNDERWEAR, IRIS, INK, STYLES, smoothstep, noise, fbm, blend, paint_body, paint_eyes)

# Underwear, matching find_underwear_verts in build_base_body.py.
BRIEFS_TOP_ABOVE_PELVIS = 0.035
BRIEFS_LEG_LENGTH = 0.16

# Vertex groups that together are "the head" for landmarks and the baked head weight: the chin and
# the lower lip are weighted to the jaw bone, not to head.
HEAD_GROUPS = ("head", "jaw")


# ---------------------------------------------------------------------------------------------
# Baking


def setup_cycles(samples):
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = samples
    scene.cycles.use_denoising = False
    scene.render.bake.margin = 16
    scene.render.bake.use_clear = True


def make_copy(source, keep_eyes):
    """Unrigged copy of the body with only the skin or only the eye faces."""
    obj = source.copy()
    obj.data = source.data.copy()
    obj.parent = None
    obj.matrix_world = source.matrix_world.copy()
    obj.modifiers.clear()
    obj.shape_key_clear()
    bpy.context.scene.collection.objects.link(obj)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    # Skin = M_Skin only: the teeth and tongue (M_Mouth) have their own material and must not be
    # painted as skin; their UV island would otherwise show up as a smear of body colour.
    remove = [f for f in bm.faces if (f.material_index == 1) != keep_eyes or (not keep_eyes and f.material_index > 1)]
    bmesh.ops.delete(bm, geom=remove, context='FACES')
    bm.to_mesh(obj.data)
    bm.free()
    return obj


def bake(obj, res, build_shader, samples=1, float_buffer=True):
    """Bake an emission shader built by build_shader(nodes, links) -> socket. Returns (H, W, 4)."""
    setup_cycles(samples)
    img = bpy.data.images.new("bake", res, res, alpha=True, float_buffer=float_buffer)
    img.generated_color = (0.0, 0.0, 0.0, 0.0)
    mat = bpy.data.materials.new("bake")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    emit = nodes.new("ShaderNodeEmission")
    links.new(build_shader(nodes, links), emit.inputs["Color"])
    links.new(emit.outputs["Emission"], out.inputs["Surface"])
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = img
    nodes.active = tex
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    for poly in obj.data.polygons:
        poly.material_index = 0

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.bake(type='EMIT')

    pixels = np.empty(res * res * 4, np.float32)
    img.pixels.foreach_get(pixels)
    bpy.data.images.remove(img)
    bpy.data.materials.remove(mat)
    return pixels.reshape(res, res, 4)


def geom_socket(name):
    def build(nodes, links):
        return nodes.new("ShaderNodeNewGeometry").outputs[name]
    return build


def ao_socket(distance):
    def build(nodes, links):
        ao = nodes.new("ShaderNodeAmbientOcclusion")
        ao.only_local = True
        ao.samples = 16
        ao.inputs["Distance"].default_value = distance
        return ao.outputs["AO"]
    return build


def attributes_socket(names):
    """Pack up to three vertex groups / attributes into RGB."""
    def build(nodes, links):
        combine = nodes.new("ShaderNodeCombineColor")
        for i, name in enumerate(names):
            if name is None:
                continue
            attr = nodes.new("ShaderNodeAttribute")
            attr.attribute_type = 'GEOMETRY'
            attr.attribute_name = name
            links.new(attr.outputs["Fac"], combine.inputs[i])
        return combine.outputs["Color"]
    return build


def color_attribute_socket(name):
    def build(nodes, links):
        attr = nodes.new("ShaderNodeAttribute")
        attr.attribute_type = 'GEOMETRY'
        attr.attribute_name = name
        return attr.outputs["Color"]
    return build


HATCH_AXIS_BONES = {
    # bone -> hatching direction follows the bone (limbs, neck) or runs vertically (torso, head)
    "neck_01": None, "head": None, "pelvis": None, "spine_01": None, "spine_02": None, "spine_03": None,
}


def add_hatch_axis(mesh_obj, armature):
    """Per-vertex hatching direction (unit vector encoded 0..1) stored as a colour attribute."""
    bones = {b.name: (np.array(b.head_local[:], np.float32), np.array(b.tail_local[:], np.float32))
             for b in armature.data.bones}
    name_by_index = {g.index: g.name for g in mesh_obj.vertex_groups}
    attr = mesh_obj.data.color_attributes.get("HatchAxis") or \
        mesh_obj.data.color_attributes.new("HatchAxis", 'FLOAT_COLOR', 'POINT')
    up = np.array([0.0, 0.0, 1.0], np.float32)
    for v in mesh_obj.data.vertices:
        best = max(v.groups, key=lambda g: g.weight, default=None)
        bone = name_by_index.get(best.group) if best is not None else None
        if bone in bones and bone not in HATCH_AXIS_BONES:
            head, tail = bones[bone]
            axis = tail - head
            axis /= max(np.linalg.norm(axis), 1e-6)
        else:
            axis = up
        attr.data[v.index].color = (*(axis * 0.5 + 0.5), 1.0)


def sum_groups_socket(names):
    def build(nodes, links):
        last = None
        for name in names:
            attr = nodes.new("ShaderNodeAttribute")
            attr.attribute_type = 'GEOMETRY'
            attr.attribute_name = name
            if last is None:
                last = attr.outputs["Fac"]
            else:
                add = nodes.new("ShaderNodeMath")
                add.operation = 'ADD'
                links.new(last, add.inputs[0])
                links.new(attr.outputs["Fac"], add.inputs[1])
                last = add.outputs["Value"]
        return last
    return build


# ---------------------------------------------------------------------------------------------
# Landmarks taken from the mesh


class Landmarks:
    def __init__(self, mesh_obj, armature):
        verts = mesh_obj.data.vertices
        groups = {g.name: g.index for g in mesh_obj.vertex_groups}

        def group_verts(name, minimum=0.5):
            """Vertices whose summed weight in the named group(s) is at least `minimum`."""
            idx = {groups[n] for n in ([name] if isinstance(name, str) else name)}
            return np.array([v.co[:] for v in verts
                             if sum(g.weight for g in v.groups if g.group in idx) >= minimum], np.float32)

        eye_vert_ids = {i for p in mesh_obj.data.polygons if p.material_index == 1 for i in p.vertices}
        eye_co = np.array([verts[i].co[:] for i in eye_vert_ids], np.float32)
        self.eye_l = eye_co[eye_co[:, 0] > 0].mean(axis=0)
        self.eye_r = eye_co[eye_co[:, 0] < 0].mean(axis=0)
        self.eye_radius = float(np.median(np.linalg.norm(eye_co[eye_co[:, 0] > 0] - self.eye_l, axis=1)))

        lips = group_verts("mask_lips")
        self.mouth = lips.mean(axis=0)
        self.lips_top = float(lips[:, 2].max())
        self.lips_bottom = float(lips[:, 2].min())
        self.mouth_half_width = float(np.abs(lips[:, 0]).max())

        ears = group_verts("mask_ears")
        self.ear_x = float(np.abs(ears[:, 0]).mean())
        self.ear_y = float(ears[:, 1].mean())
        self.ear_z = float(ears[:, 2].mean())

        # The head is the `head` AND `jaw` bones: since the jaw bone was added, the chin and the
        # lower lip are weighted to it (head 0), so the head group alone stops at the mouth line
        # and "the chin" would resolve to a throat vertex ~4 cm further back.
        head = group_verts(HEAD_GROUPS)
        centre = head[np.abs(head[:, 0]) < 0.006]
        below_eyes = centre[(centre[:, 2] < self.eye_l[2]) & (centre[:, 2] > self.mouth[2])]
        self.nose_tip = below_eyes[np.argmin(below_eyes[:, 1])]
        # Chin: the front-most point of the chin ball, i.e. below the mentolabial sulcus (the
        # furthest-back point of the profile just under the lower lip, which sticks out further).
        lower_face = centre[(centre[:, 2] < self.lips_bottom) & (centre[:, 2] > self.mouth[2] - 0.09)]
        under_lip = lower_face[lower_face[:, 2] > self.lips_bottom - 0.025]
        sulcus_z = float(under_lip[np.argmax(under_lip[:, 1])][2])
        chin_ball = lower_face[lower_face[:, 2] < sulcus_z]
        self.chin = chin_ball[np.argmin(chin_ball[:, 1])]
        # Neck silhouette 5.5 cm below the chin (profile pins for project_reference.py): the
        # front-most and back-most centre-line vertices of the whole mesh at that height.
        all_co = np.array([v.co[:] for v in verts], np.float32)
        neck_z = self.chin[2] - 0.055
        neck = all_co[(np.abs(all_co[:, 0]) < 0.006) & (np.abs(all_co[:, 2] - neck_z) < 0.006)]
        self.neck_front = float(neck[:, 1].min())
        self.neck_back = float(neck[:, 1].max())

        self.nipple = None
        if "mask_nipple" in groups:
            nip = group_verts("mask_nipple", 0.1)
            if len(nip):
                self.nipple = float(nip[:, 2].mean())

        bones = armature.data.bones
        self.bone_head = {b.name: np.array(b.head_local[:], np.float32) for b in bones}
        self.bone_tail = {b.name: np.array(b.tail_local[:], np.float32) for b in bones}


# ---------------------------------------------------------------------------------------------
# Image output


def to_image(values, valid, res, channels):
    img = np.zeros((res * res, channels), np.float32)
    img[valid] = values.reshape(len(values), -1)[:, :channels] if values.ndim > 1 else values[:, None]
    img = img.reshape(res, res, channels)[::-1]  # Blender stores images bottom row first
    img = dilate(img, valid.reshape(res, res)[::-1], 8)
    return img


def dilate(img, valid, steps):
    """Spread colours outwards past UV island edges so mip-maps don't bleed in black."""
    filled = valid.copy()
    for _ in range(steps):
        grown = img.copy()
        newly = np.zeros_like(filled)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shifted_valid = np.roll(filled, (dy, dx), axis=(0, 1))
            shifted = np.roll(img, (dy, dx), axis=(0, 1))
            take = shifted_valid & ~filled & ~newly
            grown[take] = shifted[take]
            newly |= take
        img, filled = grown, filled | newly
    return img


def save_png(img, path, size=None, mode="RGB"):
    data = (np.clip(img, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    if data.shape[2] == 1:
        data = data[:, :, 0]
    out = Image.fromarray(data, mode if data.ndim == 3 else "L")
    if size and size != out.width:
        out = out.resize((size, size), Image.LANCZOS)
    out.save(path, optimize=True)
    print("  wrote", os.path.relpath(path, REPO_ROOT))


# ---------------------------------------------------------------------------------------------


def bake_maps(obj, res, with_masks):
    maps = {}
    maps["pos"] = bake(obj, res, geom_socket("Position"))
    maps["nrm"] = bake(obj, res, geom_socket("Normal"))
    if with_masks:
        maps["ao_s"] = bake(obj, res, ao_socket(0.012), samples=24)
        maps["ao_m"] = bake(obj, res, ao_socket(0.04), samples=24)
        maps["ao_l"] = bake(obj, res, ao_socket(0.15), samples=24)
        maps["axis"] = bake(obj, res, color_attribute_socket("HatchAxis"))
        maps["point"] = bake(obj, res, geom_socket("Pointiness"))
        maps["m1"] = bake(obj, res, attributes_socket(["mask_lips", None, "mask_ears"]))
        maps["nails"] = bake(obj, res, sum_groups_socket(["mask_fingernails", "mask_toenails"]))
        maps["m2"] = bake(obj, res, attributes_socket(["mask_scalp", "mask_nipple", None]))
        maps["head"] = bake(obj, res, sum_groups_socket(HEAD_GROUPS))  # chin/lower lip are on the jaw bone
    valid = maps["pos"][:, :, 3].reshape(-1) > 0.5
    flat = {}
    for key, img in maps.items():
        flat[key] = img.reshape(-1, 4)[valid][:, :3].copy()
    if with_masks:
        for key in ("ao_s", "ao_m", "ao_l", "point"):
            flat[key] = flat[key][:, 0]
        flat["axis"] = flat["axis"] * 2.0 - 1.0
        flat["m1"][:, 1] = flat.pop("nails")[:, 0]
        flat["m2"][:, 2] = flat.pop("head")[:, 0]
    return flat, valid


def paint(body_name):
    print(f"Painting {body_name}")
    blend_path = os.path.join(BASE_DIR, f"BaseBody_{body_name}.blend")
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    mesh_obj = bpy.data.objects[f"SK_BaseBody_{body_name}"]
    armature = bpy.data.objects["Armature"]
    lm = Landmarks(mesh_obj, armature)
    os.makedirs(TEX_DIR, exist_ok=True)

    add_hatch_axis(mesh_obj, armature)
    skin_obj = make_copy(mesh_obj, keep_eyes=False)
    maps, valid = bake_maps(skin_obj, BODY_RES, with_masks=True)
    for style in STYLES:
        colour, skin_m, hair_m, under_m, beard = paint_body(body_name, maps, lm, style)
        save_png(to_image(colour, valid, BODY_RES, 3),
                 os.path.join(TEX_DIR, f"T_Body_{body_name}_BaseColor_{style}.png"))
        if style == "BL":
            masks = np.stack([skin_m, hair_m, under_m], axis=1)
            save_png(to_image(masks, valid, BODY_RES, 3),
                     os.path.join(TEX_DIR, f"T_Body_{body_name}_Masks.png"), size=MASK_RES)
        # The beard layer (T_Body_Male_Beard.png) is no longer the procedural one: project_reference.py
        # cuts it, and the brows, out of the face drawings.
    del maps

    if body_name == "Male":  # Both bodies share the same eye layout, so one eye texture is enough.
        eye_obj = make_copy(mesh_obj, keep_eyes=True)
        eye_maps, eye_valid = bake_maps(eye_obj, EYE_RES, with_masks=False)
        eye_colour, iris = paint_eyes(eye_maps, lm)
        save_png(to_image(eye_colour, eye_valid, EYE_RES, 3), os.path.join(TEX_DIR, "T_Eyes_BaseColor.png"))
        save_png(to_image(iris, eye_valid, EYE_RES, 1), os.path.join(TEX_DIR, "T_Eyes_Masks.png"))

    hook_up_materials(body_name, blend_path)


def hook_up_materials(body_name, blend_path, colour_file=None, with_beard=False):
    """Put the Borderlands textures on the .blend materials so the file previews correctly."""
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    def textured(mat_name, colour_file, beard_file=None):
        mat = bpy.data.materials[mat_name]
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        bsdf = nodes["Principled BSDF"]
        for n in [n for n in nodes if n.type == 'TEX_IMAGE' or n.type == 'MIX']:
            nodes.remove(n)
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(os.path.join(TEX_DIR, colour_file), check_existing=True)
        tex.image.filepath = "//Textures/" + colour_file
        out = tex.outputs["Color"]
        if beard_file:
            beard = nodes.new("ShaderNodeTexImage")
            beard.image = bpy.data.images.load(os.path.join(TEX_DIR, beard_file), check_existing=True)
            beard.image.filepath = "//Textures/" + beard_file
            beard.image.colorspace_settings.name = 'Non-Color'
            mix = nodes.new("ShaderNodeMix")
            mix.data_type = 'RGBA'
            mix.inputs["B"].default_value = (*HAIR, 1.0)
            links.new(beard.outputs["Color"], mix.inputs["Factor"])
            links.new(out, mix.inputs["A"])
            out = mix.outputs["Result"]
        links.new(out, bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.75

    textured("M_Skin", colour_file or f"T_Body_{body_name}_BaseColor_BL.png",
             f"T_Body_{body_name}_Beard.png" if (body_name == "Male" and with_beard) else None)
    textured("M_Eyes", "T_Eyes_BaseColor.png")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)


def main():
    wanted = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["Male", "Female"]
    for body_name in wanted:
        paint(body_name)


if __name__ == "__main__":
    main()
