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
    T_Body_Male_Beard.png                      optional facial hair layer (white = beard)
    T_Eyes_BaseColor.png, T_Eyes_Masks.png     eyes; Masks R = iris (tint)

Default colours (Unreal divides by these when tinting, see Tools/Unreal/setup_comic_look.py):
    skin (0.77, 0.58, 0.47), hair (0.09, 0.07, 0.06), underwear (0.20, 0.20, 0.22),
    iris (0.30, 0.17, 0.09), all sRGB.
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

SKIN = np.array([0.77, 0.58, 0.47], np.float32)
HAIR = np.array([0.09, 0.07, 0.06], np.float32)
UNDERWEAR = np.array([0.20, 0.20, 0.22], np.float32)
IRIS = np.array([0.30, 0.17, 0.09], np.float32)
INK = np.array([0.07, 0.045, 0.04], np.float32)
BLUSH = np.array([0.86, 0.42, 0.38], np.float32)
LIPS = np.array([0.66, 0.40, 0.36], np.float32)

STYLES = {
    # ink: crease line strength, hatch: hatching amount, bands: hard painted shadow bands,
    # mottle: brush noise strength, light: painted top light
    "BL": {"ink": 1.0, "hatch": 1.0, "bands": 1.0, "mottle": 1.0, "light": 0.05},
    "TT": {"ink": 0.6, "hatch": 0.0, "bands": 0.55, "mottle": 0.6, "light": 0.09},
}

# Underwear, matching find_underwear_verts in build_base_body.py.
BRIEFS_TOP_ABOVE_PELVIS = 0.035
BRIEFS_LEG_LENGTH = 0.16


# ---------------------------------------------------------------------------------------------
# Small numpy helpers


def smoothstep(e0, e1, x):
    span = np.asarray(e1, np.float32) - e0
    span = np.where(np.abs(span) < 1e-8, 1e-8, span)
    t = np.clip((x - e0) / span, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _hash(ix, iy, iz, seed):
    h = (ix * 73856093) ^ (iy * 19349663) ^ (iz * 83492791) ^ (seed * 2654435761)
    h = (h ^ (h >> 13)) * 1274126177
    return ((h ^ (h >> 16)) & 0xFFFF).astype(np.float32) / 65535.0


def noise(p, scale, seed=0):
    """Smooth 3D value noise in 0..1. p is (N, 3); scale is a float or a 3-vector."""
    q = p * np.asarray(scale, np.float32)
    i = np.floor(q).astype(np.int64)
    f = q - i
    f = f * f * (3.0 - 2.0 * f)
    ix, iy, iz = i[:, 0], i[:, 1], i[:, 2]
    fx, fy, fz = f[:, 0], f[:, 1], f[:, 2]
    out = 0.0
    for dx in (0, 1):
        wx = fx if dx else 1.0 - fx
        for dy in (0, 1):
            wy = fy if dy else 1.0 - fy
            for dz in (0, 1):
                wz = fz if dz else 1.0 - fz
                out = out + wx * wy * wz * _hash(ix + dx, iy + dy, iz + dz, seed)
    return out


def fbm(p, scale, octaves=3, seed=0):
    total, amp, norm = 0.0, 1.0, 0.0
    for o in range(octaves):
        total = total + amp * noise(p, np.asarray(scale, np.float32) * (2.0 ** o), seed + o)
        norm += amp
        amp *= 0.5
    return total / norm


def blend(base, colour, alpha):
    a = np.clip(alpha, 0.0, 1.0)[:, None]
    return base * (1.0 - a) + np.asarray(colour, np.float32) * a


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
    remove = [f for f in bm.faces if (f.material_index == 1) != keep_eyes]
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
            idx = groups[name]
            return np.array([v.co[:] for v in verts if any(g.group == idx and g.weight >= minimum for g in v.groups)],
                            np.float32)

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

        head = group_verts("head")
        centre = head[np.abs(head[:, 0]) < 0.006]
        below_eyes = centre[(centre[:, 2] < self.eye_l[2]) & (centre[:, 2] > self.mouth[2])]
        self.nose_tip = below_eyes[np.argmin(below_eyes[:, 1])]
        # Front-most point of the chin, well below the lower lip (which sticks out further).
        below_mouth = centre[(centre[:, 2] < self.lips_bottom - 0.015) & (centre[:, 2] > self.mouth[2] - 0.09)]
        self.chin = below_mouth[np.argmin(below_mouth[:, 1])]

        self.nipple = None
        if "mask_nipple" in groups:
            nip = group_verts("mask_nipple", 0.1)
            if len(nip):
                self.nipple = float(nip[:, 2].mean())

        bones = armature.data.bones
        self.bone_head = {b.name: np.array(b.head_local[:], np.float32) for b in bones}
        self.bone_tail = {b.name: np.array(b.tail_local[:], np.float32) for b in bones}


# ---------------------------------------------------------------------------------------------
# Painting


def eye_frame(P, lm):
    """Per-pixel coordinates around the nearest eye: outward, up, distance from eye centre."""
    left = P[:, 0] > 0
    centre = np.where(left[:, None], lm.eye_l, lm.eye_r)
    side = np.where(left, 1.0, -1.0).astype(np.float32)
    d = P - centre
    return d[:, 0] * side, d[:, 2], d[:, 1], np.linalg.norm(d, axis=1)


def paint_brows(P, N, lm, male):
    ox, up, fwd, _ = eye_frame(P, lm)
    r = lm.eye_radius
    start, end = -0.016, 0.028
    t = np.clip((ox - start) / (end - start), 0.0, 1.0)
    # Male: low, thick and nearly straight. Female: thinner with a soft arch.
    if male:
        centre_line = r * 1.3 + 0.005 + 0.004 * t - 0.007 * t * t
        thickness = 0.0078
    else:
        centre_line = r * 1.4 + 0.005 + 0.011 * t - 0.015 * t * t
        thickness = 0.0052
    # Full at the inner end, tapering to a point at the outer end.
    thickness = thickness * (0.8 + 0.2 * smoothstep(0.0, 0.25, t)) * (1.0 - 0.75 * smoothstep(0.45, 1.0, t))
    across = np.abs(up - centre_line) / np.maximum(thickness * 0.5, 1e-4)
    edge = (noise(P, (300.0, 300.0, 900.0), seed=12) - 0.5) * 0.35
    shape = 1.0 - smoothstep(0.8, 1.1, across + edge)
    shape *= smoothstep(start - 0.003, start + 0.003, ox) * (1.0 - smoothstep(end - 0.004, end + 0.001, ox))
    shape *= smoothstep(-0.2, -0.5, N[:, 1]) * (fwd < 0.01)
    # Hairs run along the brow, so the noise is stretched along x.
    strokes = noise(P, (140.0, 500.0, 1100.0), seed=11)
    return np.clip(shape * (0.75 + 0.35 * strokes), 0.0, 1.0)


def paint_beard(P, N, lm, lips_f):
    """Short full beard with moustache and soul patch, shaped on the default male face."""
    ax = np.abs(P[:, 0])
    z = P[:, 2]
    edge_noise = (fbm(P, 220.0, 2, seed=21) - 0.5) * 0.007
    zn = z + edge_noise
    corner_x = lm.mouth_half_width + 0.003
    nose_base = lm.nose_tip[2] - 0.011

    # Cheek line: from the sideburn in front of the ear, curving down to the mouth corner.
    side_t = np.clip((ax - corner_x) / max(lm.ear_x - 0.012 - corner_x, 1e-3), 0.0, 1.0)
    cheek_top = (lm.lips_top + 0.004) + side_t ** 1.4 * ((lm.eye_l[2] - 0.012) - (lm.lips_top + 0.004))
    cheeks = smoothstep(0.002, -0.002, zn - cheek_top) * smoothstep(corner_x - 0.008, corner_x, ax)

    # Lower edge: a few cm under the chin, rising towards the jaw angle below the ear.
    jaw_t = np.clip(ax / lm.ear_x, 0.0, 1.0)
    low = (lm.chin[2] - 0.045) + jaw_t ** 2 * ((lm.mouth[2] - 0.02) - (lm.chin[2] - 0.045))
    lower = smoothstep(-0.004, 0.004, zn - low)

    # Moustache: over the upper lip, top edge curving down at the sides.
    m_top = nose_base + 0.002 - 0.008 * (ax / corner_x) ** 2
    moustache = (smoothstep(-0.003, 0.003, zn - (lm.lips_top - 0.004)) * smoothstep(0.002, -0.002, zn - m_top)
                 * smoothstep(0.002, -0.004, ax - (corner_x + 0.006)))
    # Chin: everything below the lower lip, apart from clean patches either side of the soul patch.
    chin = smoothstep(0.002, -0.002, zn - (lm.lips_bottom - 0.002)) * smoothstep(0.004, -0.002, ax - (corner_x + 0.004))
    beside_soul = (smoothstep(-0.013, -0.009, zn - lm.lips_bottom) * smoothstep(0.002, -0.002, zn - lm.lips_bottom)
                   * smoothstep(0.007, 0.010, ax) * smoothstep(corner_x - 0.002, corner_x - 0.008, ax))
    chin *= 1.0 - beside_soul

    beard = np.maximum(np.maximum(cheeks, moustache), chin) * lower
    beard *= smoothstep(lm.ear_y + 0.006, lm.ear_y - 0.008, P[:, 1])  # nothing behind the ears
    beard *= 1.0 - smoothstep(0.25, 0.5, lips_f)
    # Hairs grow downwards, so the noise is stretched along z.
    strokes = noise(P, (900.0, 900.0, 260.0), seed=23)
    return np.clip(beard * (0.84 + 0.22 * strokes), 0.0, 1.0)


def paint_scalp(P, scalp_fac):
    hairline = scalp_fac + (fbm(P, 300.0, 2, seed=31) - 0.5) * 0.5
    density = smoothstep(0.35, 0.6, hairline)
    strokes = noise(P, (600.0, 220.0, 600.0), seed=33)
    return np.clip(density * (0.68 + 0.35 * strokes), 0.0, 1.0)


def underwear_mask(P, lm, female):
    """Briefs (and a sports bra on the female) with smooth edges, plus their outline."""
    top = lm.bone_head["pelvis"][2] + BRIEFS_TOP_ABOVE_PELVIS
    left = P[:, 0] > 0
    thigh_head = np.where(left[:, None], lm.bone_head["thigh_l"], lm.bone_head["thigh_r"])
    thigh_tail = np.where(left[:, None], lm.bone_tail["thigh_l"], lm.bone_tail["thigh_r"])
    axis = thigh_tail - thigh_head
    axis /= np.linalg.norm(axis, axis=1, keepdims=True)
    along = np.einsum("ij,ij->i", P - thigh_head, axis)
    d_top = top - P[:, 2]  # positive inside
    d_leg = BRIEFS_LEG_LENGTH - along
    near_hips = P[:, 2] > lm.bone_head["thigh_l"][2] - 0.35
    inside = np.minimum(d_top, d_leg)
    inside = np.where(near_hips, inside, -1.0)
    edge_dist = np.abs(inside)
    waistband = smoothstep(0.03, 0.02, d_top) * (inside > 0)

    if female and lm.nipple is not None:
        radial = np.linalg.norm(P[:, :2] - np.array([0.0, lm.bone_head["spine_03"][1]], np.float32), axis=1)
        d_low = P[:, 2] - (lm.nipple - 0.075)
        d_high = (lm.nipple + 0.07) - P[:, 2]
        bra = np.minimum(np.minimum(d_low, d_high), 0.2 - radial)
        bra_band = smoothstep(0.022, 0.012, d_low) * (bra > 0)
        edge_dist = np.where(bra > inside, np.abs(bra), edge_dist)
        inside = np.maximum(inside, bra)
        waistband = np.maximum(waistband, bra_band)

    mask = smoothstep(-0.0008, 0.0008, inside)
    return mask, edge_dist, waistband


def paint_body(body_name, maps, lm, style):
    """Returns (base colour (N,3), skin mask, hair mask, underwear mask, beard mask)."""
    male = body_name == "Male"
    st = STYLES[style]
    P, N = maps["pos"], maps["nrm"]
    ao_s, ao_l, point = maps["ao_s"], maps["ao_l"], maps["point"]
    lips_f, nails_f, ears_f = maps["m1"][:, 0], maps["m1"][:, 1], maps["m1"][:, 2]
    scalp_f, nipple_f = maps["m2"][:, 0], maps["m2"][:, 1]

    colour = np.repeat(SKIN[None, :], len(P), axis=0)

    # Painted form shading: soft bands from large-scale occlusion, shifted warm in shadow.
    shadow = 1.0 - ao_l
    banded = 0.55 * smoothstep(0.16, 0.22, shadow) + 0.45 * smoothstep(0.34, 0.40, shadow)
    soft = smoothstep(0.1, 0.5, shadow)
    shade = banded * st["bands"] + soft * (1.0 - st["bands"])
    colour = colour * (1.0 - 0.32 * shade[:, None] * np.array([0.75, 1.0, 1.05], np.float32))

    # Painted top light on upward-facing planes.
    light = smoothstep(0.35, 0.85, N[:, 2]) * st["light"]
    colour = colour + light[:, None] * np.array([1.0, 0.95, 0.85], np.float32)

    # Brush mottling and long strokes.
    mottle = (fbm(P, 45.0, 3, seed=1) - 0.5) * 0.12 + (noise(P, (90.0, 90.0, 18.0), seed=2) - 0.5) * 0.06
    colour = colour * (1.0 + mottle[:, None] * st["mottle"])
    cool = (fbm(P, 12.0, 2, seed=3) - 0.5) * 0.08 * st["mottle"]
    colour = colour * (1.0 + cool[:, None] * np.array([-0.5, 0.2, 0.8], np.float32))

    # Warm zones: cheeks, nose, ears, elbows, knees, hands, feet.
    ox, up, fwd, eye_d = eye_frame(P, lm)
    cheek = np.exp(-(((ox - 0.012) / 0.022) ** 2 + ((up + 0.035) / 0.022) ** 2)) * (fwd < 0.0)
    nose = np.exp(-np.sum((P - lm.nose_tip) ** 2, axis=1) / (0.014 ** 2))
    warm = 0.28 * cheek + 0.35 * nose + 0.35 * smoothstep(0.2, 0.8, ears_f)
    for joint, radius, amount in (("lowerarm", 0.045, 0.25), ("calf", 0.06, 0.3), ("hand", 0.07, 0.25)):
        for side in ("l", "r"):
            warm += amount * np.exp(-np.sum((P - lm.bone_head[f"{joint}_{side}"]) ** 2, axis=1) / radius ** 2)
    warm += 0.35 * smoothstep(0.1, 0.6, nails_f)
    for side in ("l", "r"):
        warm += 0.25 * np.exp(-np.sum((P - lm.bone_head[f"ball_{side}"]) ** 2, axis=1) / 0.06 ** 2)
    colour = blend(colour, BLUSH, np.clip(warm, 0.0, 0.55) * 0.55)

    # Under-eye shadow.
    under = np.exp(-((ox / 0.018) ** 2 + ((up + 0.016) / 0.008) ** 2)) * (fwd < 0.005)
    colour = colour * (1.0 - 0.07 * under[:, None] * np.array([0.9, 1.1, 0.7], np.float32))

    # Lips: darker upper lip, light catch on the lower lip.
    lips = smoothstep(0.35, 0.6, lips_f)
    lip_col = np.where((P[:, 2] > lm.mouth[2])[:, None], LIPS * 0.86, LIPS)
    colour = blend(colour, lip_col, lips * 0.85)
    catch = lips * np.exp(-((P[:, 0] / 0.012) ** 2 + ((P[:, 2] - (lm.lips_bottom + 0.004)) / 0.003) ** 2))
    colour = colour + 0.12 * catch[:, None]

    # Nails and nipples.
    nails = smoothstep(0.4, 0.7, nails_f)
    colour = blend(colour, SKIN * np.array([1.12, 1.0, 1.0], np.float32) + 0.08, nails * 0.8)
    if male:
        colour = blend(colour, np.array([0.55, 0.34, 0.30], np.float32), smoothstep(0.15, 0.5, nipple_f) * 0.6)

    skin_colour = colour.copy()

    # Hair: brows and short scalp hair.
    hair = np.maximum(paint_brows(P, N, lm, male), paint_scalp(P, scalp_f))
    colour = blend(colour, HAIR, hair)

    # Underwear (painted cloth with its own shading and stitching).
    under_m, edge_dist, waistband = underwear_mask(P, lm, female=not male)
    cloth = np.repeat(UNDERWEAR[None, :], len(P), axis=0)
    cloth = cloth * (1.0 - 0.45 * smoothstep(0.08, 0.35, shadow)[:, None])
    cloth = cloth * (1.0 + (noise(P, (60.0, 60.0, 220.0), seed=41)[:, None] - 0.5) * 0.25)
    cloth = cloth + 0.07 * waistband[:, None]
    stitch_line = smoothstep(0.0012, 0.0004, np.abs(edge_dist - 0.006))
    dashes = (np.sin(np.einsum("ij,j->i", P, np.array([700.0, 300.0, 120.0], np.float32))) > 0.2)
    cloth = cloth + 0.22 * (stitch_line * dashes)[:, None]
    colour = blend(colour, cloth, under_m)

    # Ink.
    ink = smoothstep(0.84, 0.6, ao_s) * (0.7 + 0.6 * noise(P, 160.0, seed=51))
    ink = np.maximum(ink, smoothstep(0.47, 0.42, point) * 0.8)
    ink *= st["ink"]
    # The eye socket is deep, which would fill it with ink; only the lid lines are drawn there.
    ink *= smoothstep(lm.eye_radius * 1.6, lm.eye_radius * 2.4, eye_d)
    # Eyelid lines: heavy upper lash line, light lower one.
    lid_width = np.where(up > -0.001, 1.09, 1.045) * lm.eye_radius
    lid = smoothstep(lid_width, lid_width - 0.0012, eye_d) * (fwd < lm.eye_radius * 0.2)
    ink = np.maximum(ink, lid * np.where(up > -0.001, 1.0, 0.5))
    # Outlines around lips, nails and underwear.
    ink = np.maximum(ink, smoothstep(0.18, 0.0, np.abs(lips_f - 0.5)) * 0.75 * st["ink"])
    ink = np.maximum(ink, smoothstep(0.15, 0.0, np.abs(nails_f - 0.5)) * 0.6)
    ink = np.maximum(ink, smoothstep(0.0015, 0.0005, edge_dist) * (P[:, 2] > 0.6))
    if st["hatch"] > 0:
        d1 = np.einsum("ij,j->i", P, np.array([0.7, 0.2, 0.7], np.float32)) * 240.0
        d2 = np.einsum("ij,j->i", P, np.array([-0.7, 0.2, 0.7], np.float32)) * 240.0
        width1 = 0.45 * smoothstep(0.22, 0.42, shadow) * st["hatch"]
        width2 = 0.4 * smoothstep(0.40, 0.60, shadow) * st["hatch"]
        hatch = np.maximum(smoothstep(width1, width1 * 0.6, np.abs(d1 - np.round(d1)) * 2) * (width1 > 0.02),
                           smoothstep(width2, width2 * 0.6, np.abs(d2 - np.round(d2)) * 2) * (width2 > 0.02))
        hatch *= 0.55 + 0.45 * noise(P, 70.0, seed=53)
        ink = np.maximum(ink, hatch * 0.85)
    colour = blend(colour, INK, np.clip(ink, 0.0, 1.0))

    beard = paint_beard(P, N, lm, lips_f) if male else None

    skin_mask = np.clip((1.0 - hair) * (1.0 - under_m) * (1.0 - np.clip(ink, 0, 1)), 0.0, 1.0)
    return np.clip(colour, 0.0, 1.0), skin_mask, hair, under_m, beard


def paint_eyes(maps, lm):
    P = maps["pos"]
    left = P[:, 0] > 0
    centre = np.where(left[:, None], lm.eye_l, lm.eye_r)
    d = P - centre
    d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-6)
    angle = np.degrees(np.arccos(np.clip(-d[:, 1], -1.0, 1.0)))  # 0 = looking straight ahead

    sclera = np.array([0.93, 0.90, 0.86], np.float32)
    colour = np.repeat(sclera[None, :], len(P), axis=0)
    colour = colour * (1.0 - 0.25 * smoothstep(0.1, 0.6, d[:, 2])[:, None])  # lid shadow
    colour = blend(colour, np.array([0.9, 0.62, 0.6], np.float32), smoothstep(40, 60, angle) * 0.4)

    iris_r, pupil_r = 25.0, 10.0
    iris = smoothstep(iris_r + 0.8, iris_r - 0.8, angle)
    swirl = np.arctan2(d[:, 2], d[:, 0])
    rays = noise(np.stack([swirl * 8.0, angle * 0.3, np.zeros_like(angle)], 1), 1.0, seed=61)
    iris_col = IRIS[None, :] * (0.75 + 0.6 * rays[:, None])
    iris_col = iris_col * (1.0 + 0.35 * smoothstep(iris_r * 0.75, pupil_r, angle)[:, None])  # lighter centre
    iris_col = iris_col * (1.0 - 0.35 * smoothstep(0.1, 0.9, d[:, 2])[:, None])  # lid shadow
    colour = blend(colour, iris_col, iris)
    colour = blend(colour, INK, smoothstep(2.0, 0.0, np.abs(angle - iris_r)) * 0.9)  # limbal ring
    colour = blend(colour, np.array([0.03, 0.02, 0.02], np.float32), smoothstep(pupil_r + 0.6, pupil_r - 0.6, angle))
    hl_dir = np.array([-0.25, -0.93, 0.27], np.float32)
    hl_dir /= np.linalg.norm(hl_dir)
    highlight = smoothstep(0.9975, 0.9990, np.einsum("ij,j->i", d, hl_dir))
    colour = blend(colour, np.array([1.0, 1.0, 1.0], np.float32), highlight)
    return np.clip(colour, 0.0, 1.0), iris * (1.0 - highlight)


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
        maps["ao_l"] = bake(obj, res, ao_socket(0.15), samples=24)
        maps["point"] = bake(obj, res, geom_socket("Pointiness"))
        maps["m1"] = bake(obj, res, attributes_socket(["mask_lips", None, "mask_ears"]))
        maps["nails"] = bake(obj, res, sum_groups_socket(["mask_fingernails", "mask_toenails"]))
        maps["m2"] = bake(obj, res, attributes_socket(["mask_scalp", "mask_nipple", "head"]))
    valid = maps["pos"][:, :, 3].reshape(-1) > 0.5
    flat = {}
    for key, img in maps.items():
        flat[key] = img.reshape(-1, 4)[valid][:, :3].copy()
    if with_masks:
        flat["ao_s"], flat["ao_l"], flat["point"] = flat["ao_s"][:, 0], flat["ao_l"][:, 0], flat["point"][:, 0]
        flat["m1"][:, 1] = flat.pop("nails")[:, 0]
    return flat, valid


def paint(body_name):
    print(f"Painting {body_name}")
    blend_path = os.path.join(BASE_DIR, f"BaseBody_{body_name}.blend")
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    mesh_obj = bpy.data.objects[f"SK_BaseBody_{body_name}"]
    armature = bpy.data.objects["Armature"]
    lm = Landmarks(mesh_obj, armature)
    os.makedirs(TEX_DIR, exist_ok=True)

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
        if beard is not None and style == "BL":
            save_png(to_image(beard, valid, BODY_RES, 1),
                     os.path.join(TEX_DIR, f"T_Body_{body_name}_Beard.png"), size=MASK_RES)
    del maps

    if body_name == "Male":  # Both bodies share the same eye layout, so one eye texture is enough.
        eye_obj = make_copy(mesh_obj, keep_eyes=True)
        eye_maps, eye_valid = bake_maps(eye_obj, EYE_RES, with_masks=False)
        eye_colour, iris = paint_eyes(eye_maps, lm)
        save_png(to_image(eye_colour, eye_valid, EYE_RES, 3), os.path.join(TEX_DIR, "T_Eyes_BaseColor.png"))
        save_png(to_image(iris, eye_valid, EYE_RES, 1), os.path.join(TEX_DIR, "T_Eyes_Masks.png"))

    hook_up_materials(body_name, blend_path)


def hook_up_materials(body_name, blend_path):
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

    textured("M_Skin", f"T_Body_{body_name}_BaseColor_BL.png",
             f"T_Body_{body_name}_Beard.png" if body_name == "Male" else None)
    textured("M_Eyes", "T_Eyes_BaseColor.png")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)


def main():
    wanted = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["Male", "Female"]
    for body_name in wanted:
        paint(body_name)


if __name__ == "__main__":
    main()
