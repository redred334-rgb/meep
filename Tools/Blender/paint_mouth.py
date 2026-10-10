"""
Paints the comic-style mouth interior (teeth, gums, tongue) for the player base bodies.

Run after build_base_body.py (which gives the mouth faces their own UV square):
    python3 Tools/Blender/paint_mouth.py -- Female              # bake, paint, hook up, preview
    python3 Tools/Blender/paint_mouth.py -- Male --no-preview
    blender -b -P Tools/Blender/paint_mouth.py -- Female        # same thing from Blender itself

Like paint_base_body.py: Blender bakes position, normal and occlusion of the M_Mouth faces
(material index 2) into the mouth UV square, and the painting is done with numpy in 3D space:
teeth are segmented by arc length along the dental arch (14 per row), the gum line is a scalloped
ink line, the tongue gets a centre groove, a throat shadow and a highlight stripe near the tip.

Output in Art/Characters/Base/Textures/ (both bodies have the same mouth UV layout, so one set
is shared; the painter derives every position from the body it is run on, so either body works):
    T_Mouth_BaseColor.png   1024, colour
    T_Mouth_Masks.png       1024, R = teeth (ink excluded, tint), G = tongue (ink excluded, tint)
and Previews/<Body>_Mouth.png: front and 3/4 close-ups with the jaw at +18 degrees and
Exp_MouthOpen 0.3, plus the Rage emote from Emotes.json. The .blend gets T_Mouth_* hooked
into M_Mouth (colour, and roughness 0.35 on the teeth / 0.6 elsewhere from the mask).

Default colours (sRGB): TEETH #E8E2D6, GUM #9A4D47, TONGUE #B8646A, DARK (inside) #3A1A18,
ink is comic_paint.INK.
"""

import json
import math
import os
import sys

import bpy  # noqa: E402  (bpy must load before bmesh)
import bmesh
import numpy as np
from mathutils import Euler, Vector
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from comic_paint import (  # noqa: E402
    INK, hex_col, smoothstep, noise, blend, line_band)
from paint_base_body import (  # noqa: E402
    BASE_DIR, TEX_DIR, REPO_ROOT, bake, geom_socket, ao_socket, color_attribute_socket, to_image, save_png)
import render_emotes  # noqa: E402

MOUTH_RES = int(os.environ.get("MOUTH_RES", 1024))
PREVIEW_DIR = os.path.join(BASE_DIR, "Previews")

TEETH = hex_col("#E8E2D6")
GUM = hex_col("#9A4D47")
TONGUE = hex_col("#B8646A")
DARK = hex_col("#3A1A18")
# Shadows shift warm, never grey (style guide): the x0.8 / x0.75 bands as RGB multipliers.
BACK_MUL = np.array([0.83, 0.79, 0.76], np.float32)        # x0.8 toward the back teeth
THROAT_MUL = np.array([0.78, 0.73, 0.72], np.float32)      # x0.75 toward the throat
HALF_MUL = np.array([0.92, 0.90, 0.89], np.float32)        # soft half-tone under the lip
HIGHLIGHT_MUL = np.array([1.22, 1.20, 1.10], np.float32)   # lips-side stripe on the tongue

# Human tooth widths per side from the midline, mm: central, lateral, canine, 2 premolars, 2 molars.
# They are scaled to each arch's half length, so every row shows 14 teeth.
TOOTH_WIDTHS = np.array([8.5, 6.5, 7.5, 7.0, 7.0, 10.0, 10.0], np.float32)

PREVIEW_PANEL = 640
PREVIEW_SAMPLES = 32


# ---------------------------------------------------------------------------------------------
# Baking


def mouth_copy(source):
    """Unrigged copy of the body with only the M_Mouth faces, plus a 'MouthPart' colour
    attribute: R = upper teeth, G = lower teeth, B = tongue."""
    obj = source.copy()
    obj.data = source.data.copy()
    obj.parent = None
    obj.matrix_world = source.matrix_world.copy()
    obj.modifiers.clear()
    obj.shape_key_clear()
    bpy.context.scene.collection.objects.link(obj)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index != 2], context='FACES')
    bm.verts.ensure_lookup_table()
    deform = bm.verts.layers.deform.verify()
    head_group = obj.vertex_groups["head"].index
    pending = set(bm.faces)
    islands = []
    while pending:
        island, stack = set(), [min(pending, key=lambda f: f.index)]
        while stack:
            f = stack.pop()
            if f in island:
                continue
            island.add(f)
            pending.discard(f)
            stack.extend(n for v in f.verts for n in v.link_faces if n in pending)
        on_head = any(head_group in v[deform] for f in island for v in f.verts)
        islands.append((on_head, len(island), island))
    if len(islands) != 3:
        raise RuntimeError(f"expected 3 mouth islands (upper teeth, lower teeth, tongue), found {len(islands)}")
    upper = next(i for i in islands if i[0])[2]
    lower, tongue = [i[2] for i in sorted((i for i in islands if not i[0]), key=lambda i: i[1])]
    part_of = {}
    for channel, island in enumerate((upper, lower, tongue)):
        for f in island:
            for v in f.verts:
                part_of[v.index] = channel
    bm.to_mesh(obj.data)
    bm.free()
    attr = obj.data.color_attributes.new("MouthPart", 'FLOAT_COLOR', 'POINT')
    for v in obj.data.vertices:
        rgba = [0.0, 0.0, 0.0, 1.0]
        rgba[part_of[v.index]] = 1.0
        attr.data[v.index].color = rgba
    return obj


def bake_mouth_maps(obj, res):
    maps = {
        "pos": bake(obj, res, geom_socket("Position")),
        "nrm": bake(obj, res, geom_socket("Normal")),
        "ao_s": bake(obj, res, ao_socket(0.008), samples=24),
        "ao_m": bake(obj, res, ao_socket(0.03), samples=24),
        "part": bake(obj, res, color_attribute_socket("MouthPart")),
    }
    # The bake leaves alpha at 1 everywhere and the bake margin holds junk positions, so only
    # texels where the part attribute is intact count; to_image dilates past the island edges.
    valid = maps["part"][:, :, :3].sum(axis=2).reshape(-1) > 0.97
    flat = {key: img.reshape(-1, 4)[valid][:, :3].copy() for key, img in maps.items()}
    # Belt and braces: a mouth texel sits within 5 cm of its part's median position.
    which = flat["part"].argmax(axis=1)
    near = np.ones(len(which), bool)
    for channel in range(3):
        sel = which == channel
        if sel.any():
            near[sel] = np.abs(flat["pos"][sel] - np.median(flat["pos"][sel], axis=0)).max(axis=1) < 0.05
    if not near.all():
        print(f"  dropped {int((~near).sum())} margin texels with positions outside the mouth")
        valid[np.flatnonzero(valid)[~near]] = False
        flat = {key: arr[near] for key, arr in flat.items()}
    flat["ao_s"] = flat["ao_s"][:, 0]
    flat["ao_m"] = flat["ao_m"][:, 0]
    return flat, valid


# ---------------------------------------------------------------------------------------------
# Painting


class Arch:
    """Arc-length coordinates along one tooth row, fitted from the baked texels.

    coords() gives per texel: s, the signed arc length from the midline (m); out, 1 where the
    texel faces out of the arch; depth, 0 at the outer wall and 1 at the inner ridge (where the
    slab meets the palate / floor of the mouth); thick, the wall-to-ridge distance (m).
    """

    def __init__(self, P, N, sel):
        p = P[sel]
        self.z_top, self.z_bottom = float(p[:, 2].max()), float(p[:, 2].min())
        # Centre of the arch: between the two back ends, a little forward of them.
        self.centre = np.array([0.0, float(p[:, 1].max()) - 0.004], np.float32)
        radial = p[:, :2] - self.centre
        r = np.linalg.norm(radial, axis=1)
        radial /= np.maximum(r[:, None], 1e-6)
        out = (N[sel][:, :2] * radial).sum(axis=1)
        front = (out > 0.3) & (np.abs(N[sel][:, 2]) < 0.7)
        theta = np.arctan2(p[:, 0], -(p[:, 1] - self.centre[1]))
        # Per angle bin: median radius of the outer wall and the innermost radius -> polylines.
        bins = np.linspace(-math.pi / 2, math.pi / 2, 49)
        centres = 0.5 * (bins[1:] + bins[:-1])
        idx = np.clip(np.digitize(theta, bins) - 1, 0, 47)
        outer = np.array([np.median(r[front & (idx == b)]) if np.any(front & (idx == b)) else np.nan for b in range(48)])
        inner = np.array([np.percentile(r[idx == b], 2) if np.any(idx == b) else np.nan for b in range(48)])
        ok = ~np.isnan(outer)
        self._outer = np.interp(centres, centres[ok], outer[ok]).astype(np.float32)
        ok = ~np.isnan(inner)
        self._inner = np.interp(centres, centres[ok], inner[ok]).astype(np.float32)
        pts = np.stack([self._outer * np.sin(centres), -self._outer * np.cos(centres)], axis=1)
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        self._theta, self._cum = centres, cum - np.interp(0.0, centres, cum)
        self.s_half = float(max(-self._cum[0], self._cum[-1]))

    def coords(self, P, N):
        radial = P[:, :2] - self.centre
        r = np.linalg.norm(radial, axis=1)
        radial /= np.maximum(r[:, None], 1e-6)
        out = (N[:, :2] * radial).sum(axis=1)
        theta = np.arctan2(P[:, 0], -(P[:, 1] - self.centre[1]))
        s = np.interp(theta, self._theta, self._cum).astype(np.float32)
        r_out = np.interp(theta, self._theta, self._outer)
        thick = np.maximum(r_out - np.interp(theta, self._theta, self._inner), 0.002).astype(np.float32)
        depth = np.clip((r_out - r) / thick, 0.0, 1.0).astype(np.float32)
        return s, out, depth, thick


def tooth_coords(s, s_half):
    """Per texel: distance (m) to the nearest tooth boundary and the 0..1 position inside the tooth."""
    widths = TOOTH_WIDTHS / TOOTH_WIDTHS.sum() * s_half
    edges = np.concatenate([[0.0], np.cumsum(widths)])
    a = np.abs(s)
    k = np.clip(np.searchsorted(edges, a, side='right') - 1, 0, len(widths) - 1)
    start, width = edges[k], widths[k]
    u = np.clip((a - start) / width, 0.0, 1.0)
    d_sep = np.minimum(a - start, start + width - a)
    return d_sep, u, k


def paint_teeth(P, N, ao_s, ao_m, sel, upper, colour, teeth_mask):
    """Paint one tooth row in place. upper: the row hangs from the gum (gum at the top).

    The slab has an outer wall (gum above/below the gum line, crown toward the biting edge) and
    an inner surface that slopes from the biting edge back to the ridge where it meets the mouth
    cavity: tooth colour near the edge, gum toward the ridge, dark at the ridge itself.
    """
    arch = Arch(P, N, sel)
    Ps, Ns = P[sel], N[sel]
    s, out, depth, thick = arch.coords(Ps, Ns)
    z = Ps[:, 2]
    height = arch.z_top - arch.z_bottom
    d_sep, u, k = tooth_coords(s, arch.s_half)
    wall = smoothstep(-0.05, 0.3, out)             # outward wall; the rest is the inner surface
    inner = 1.0 - wall

    # Scalloped gum line on the wall: arches over each tooth centre, dips between the teeth.
    scallop = 0.06 * height * np.cos(2.0 * math.pi * u)
    if upper:
        z_gum = arch.z_top - 0.36 * height - scallop
        d_gum_wall = z - z_gum                      # > 0 on the gum side
        to_edge = (z - arch.z_bottom) / (0.64 * height)
    else:
        z_gum = arch.z_bottom + 0.36 * height + scallop
        d_gum_wall = z_gum - z
        to_edge = (arch.z_top - z) / (0.64 * height)
    # On the inner surface the gum starts 62% of the way to the ridge.
    d_gum_inner = (depth - 0.62) * thick
    d_gum = np.where(wall > 0.5, d_gum_wall, d_gum_inner)
    crown = smoothstep(0.0004, -0.0004, d_gum)
    to_edge = np.where(wall > 0.5, to_edge, 1.0 - depth / 0.62)

    # Flat values: crown off-white, gum pink-red, the ridge dark.
    col = (TEETH * crown[:, None] + GUM * (1.0 - crown)[:, None]).astype(np.float32)
    back = smoothstep(0.4, 1.0, np.abs(s) / max(arch.s_half, 1e-6))
    col = col * (1.0 + (BACK_MUL - 1.0) * back[:, None])                            # x0.8 toward the back teeth
    col = blend(col, DARK, inner * smoothstep(0.75, 1.0, depth) * 0.7)             # ridge: inside the mouth
    col = col * (1.0 + (HALF_MUL - 1.0) * (smoothstep(0.0035, 0.0008, np.abs(d_gum)) * crown)[:, None])  # shadow at the gum
    rounding = smoothstep(0.0018, 0.0003, d_sep) * crown                             # each tooth reads as a cylinder
    col = col * (1.0 - 0.08 * rounding[:, None])
    if upper:
        col = col * (1.0 + (HALF_MUL - 1.0) * (inner * crown)[:, None])             # underside is the shadow side
    else:
        col = col * (1.0 - 0.12 * (inner * crown * smoothstep(0.85, 0.5, ao_m[sel]))[:, None])
    col = col * (1.0 - 0.15 * ((1.0 - crown) * smoothstep(0.9, 0.55, ao_m[sel]))[:, None])

    # Ink: medium gum line (continuous), thin separations from the gum through the biting edge,
    # fading out over the inner surface.
    ink = line_band(d_gum, 0.0011, soft=0.0003) * 0.9
    sep_w = 0.0006 + 0.0002 * smoothstep(2.5, 6.0, k)                               # molars a touch heavier
    sep_w = sep_w + 0.0003 * smoothstep(0.25, 0.0, to_edge)                         # notch at the edge
    sep = line_band(d_sep, sep_w, soft=0.00025) * crown
    sep *= smoothstep(-0.0003, 0.0012, -d_gum) * (wall + inner * smoothstep(0.5, 0.05, depth))
    sep *= 0.55 + 0.2 * smoothstep(0.004, 0.0, np.abs(s))                            # thin lines read brown; midline a little stronger
    ink = np.clip(ink + sep, 0.0, 1.0)
    col = blend(col, INK, ink)

    colour[sel] = np.clip(col, 0.0, 1.0)
    teeth_mask[sel] = crown * (1.0 - ink)


def paint_tongue(P, N, ao_s, ao_m, sel, colour, tongue_mask):
    Ps, Ns = P[sel], N[sel]
    y_tip, y_back = float(Ps[:, 1].min()), float(Ps[:, 1].max())
    v = (Ps[:, 1] - y_tip) / max(y_back - y_tip, 1e-6)
    x = Ps[:, 0]
    top = smoothstep(0.1, 0.45, Ns[:, 2])
    under = smoothstep(-0.1, -0.45, Ns[:, 2])
    side = (1.0 - top) * (1.0 - under)

    col = np.tile(TONGUE, (len(Ps), 1)).astype(np.float32)
    col = col * (1.0 - 0.10 * side[:, None])                                        # sides: half-tone
    col = blend(col, DARK, under * 0.55)                                             # underside: inside the mouth
    throat = smoothstep(0.5, 0.95, v)
    col = col * (1.0 + (THROAT_MUL - 1.0) * throat[:, None])                         # x0.75 toward the throat
    col = blend(col, DARK, smoothstep(0.82, 1.0, v) * 0.65)                          # and dark at the back
    col = col * (1.0 - 0.12 * (smoothstep(0.75, 0.45, ao_m[sel]) * (1.0 - top))[:, None])   # floor of the mouth

    # Lips-side highlight: a flat lighter crescent on the top, following the tip's curve.
    d_front = np.sqrt((Ps[:, 1] - y_tip) ** 2 + (0.5 * x) ** 2)
    stripe = line_band(d_front - 0.013, 0.0055, soft=0.0008) * top
    stripe *= smoothstep(0.0175, 0.012, np.abs(x)) * smoothstep(0.25, 0.65, noise(Ps, 90.0, 3) + 0.25)
    col = col * (1.0 + (HIGHLIGHT_MUL - 1.0) * stripe[:, None])

    # Centre groove: a darker line down the middle of the top, with a soft valley around it.
    wob = (noise(Ps, 140.0, 11) - 0.5) * 0.0012
    along = smoothstep(0.10, 0.2, v) * smoothstep(0.86, 0.72, v)
    valley = smoothstep(0.0032, 0.0006, np.abs(x + wob)) * top * along
    col = col * (1.0 - 0.10 * valley[:, None])
    groove = line_band(x + wob, 0.001, soft=0.0003) * top * along * 0.55
    col = blend(col, INK, groove)

    colour[sel] = np.clip(col, 0.0, 1.0)
    tongue_mask[sel] = (1.0 - under * 0.5) * (1.0 - groove)


def paint_mouth(maps):
    P, N, part = maps["pos"], maps["nrm"], maps["part"]
    n = len(P)
    colour = np.tile(DARK, (n, 1)).astype(np.float32)
    teeth_mask = np.zeros(n, np.float32)
    tongue_mask = np.zeros(n, np.float32)
    which = part.argmax(axis=1)
    upper, lower, tongue = which == 0, which == 1, which == 2
    paint_teeth(P, N, maps["ao_s"], maps["ao_m"], upper, True, colour, teeth_mask)
    paint_teeth(P, N, maps["ao_s"], maps["ao_m"], lower, False, colour, teeth_mask)
    paint_tongue(P, N, maps["ao_s"], maps["ao_m"], tongue, colour, tongue_mask)
    return colour, np.stack([teeth_mask, tongue_mask, np.zeros(n, np.float32)], axis=1)


# ---------------------------------------------------------------------------------------------
# Material hook-up and preview


def hook_up_mouth(blend_path):
    """T_Mouth_BaseColor -> Base Color; roughness 0.35 on the teeth, 0.6 elsewhere via the mask."""
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    mat = bpy.data.materials["M_Mouth"]
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes["Principled BSDF"]
    for node in [n for n in nodes if n.type in ('TEX_IMAGE', 'MIX', 'SEPARATE_COLOR', 'MATH')]:
        nodes.remove(node)

    def image_node(file_name, non_color=False):
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(os.path.join(TEX_DIR, file_name), check_existing=True)
        tex.image.filepath = "//Textures/" + file_name
        if non_color:
            tex.image.colorspace_settings.name = 'Non-Color'
        return tex

    colour = image_node("T_Mouth_BaseColor.png")
    links.new(colour.outputs["Color"], bsdf.inputs["Base Color"])
    masks = image_node("T_Mouth_Masks.png", non_color=True)
    split = nodes.new("ShaderNodeSeparateColor")
    links.new(masks.outputs["Color"], split.inputs["Color"])
    rough = nodes.new("ShaderNodeMath")
    rough.operation = 'MULTIPLY_ADD'
    rough.inputs[1].default_value = -0.25   # teeth mask 1 -> 0.35, 0 -> 0.6
    rough.inputs[2].default_value = 0.6
    links.new(split.outputs["Red"], rough.inputs[0])
    links.new(rough.outputs["Value"], bsdf.inputs["Roughness"])
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)


def mouth_centre(mesh, arm):
    """World-space centre of the lips (mask_lips vertex group)."""
    idx = mesh.vertex_groups["mask_lips"].index
    co = [mesh.matrix_world @ v.co for v in mesh.data.vertices
          if any(g.group == idx and g.weight >= 0.5 for g in v.groups)]
    return sum(co, Vector()) / len(co)


def aim_camera(cam, centre, dist, yaw_deg):
    """Put the camera dist metres from centre, yaw degrees around Z off the face's -Y axis."""
    yaw = math.radians(yaw_deg)
    cam.location = centre + Vector((-math.sin(yaw) * dist, -math.cos(yaw) * dist, 0.0))
    cam.rotation_euler = (centre - cam.location).to_track_quat('-Z', 'Y').to_euler()


def render_preview(body_name, blend_path, out_path):
    """Front and 3/4 close-ups (jaw +18 deg, Exp_MouthOpen 0.3) and the Rage emote, side by side.
    Nothing is saved: camera and lights live in memory only."""
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    mesh, arm = render_emotes.find_body()
    with open(render_emotes.EMOTES_JSON) as f:
        library = json.load(f)
    sliders = library.get("borderlands_default_face", {}).get("sliders", {})
    rage = next((e for e in library["emotes"] if e["name"] == "Rage"), None)

    render_emotes.ensure_iris(mesh, arm)
    render_emotes.reset_face(mesh, arm)
    render_emotes.apply_sliders(mesh, sliders)
    keep = tuple(sliders)
    cam, lights = render_emotes.setup_scene(mesh, arm, (PREVIEW_PANEL, PREVIEW_PANEL), PREVIEW_SAMPLES)
    centre = mouth_centre(mesh, arm) + Vector((0.0, 0.0, -0.012))
    dist = 0.085 / (cam.data.sensor_height / cam.data.lens)   # 8.5 cm tall frame

    open_mouth = {"name": "MouthOpen", "morphs": {"Exp_MouthOpen": 0.3}, "jaw_deg": 18}
    tmp_dir = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"paint_mouth_{body_name}")
    os.makedirs(tmp_dir, exist_ok=True)
    panels = []
    try:
        for label, emote, yaw in (("front, jaw +18, MouthOpen 0.3", open_mouth, 0.0),
                                  ("3/4, jaw +18, MouthOpen 0.3", open_mouth, 38.0),
                                  ("Rage (Emotes.json)", rage, 0.0)):
            if emote is None:
                continue
            render_emotes.apply_emote(mesh, arm, emote, keep)
            aim_camera(cam, centre, dist, yaw)
            path = os.path.join(tmp_dir, label.split(",")[0].replace("/", "") + ".png")
            render_emotes.render_to(path)
            panels.append((label, path))
    finally:
        render_emotes.reset_face(mesh, arm)
        render_emotes.teardown_scene(cam, lights)

    gap, label_h = 6, 24
    sheet = Image.new("RGB", (len(panels) * (PREVIEW_PANEL + gap) + gap, PREVIEW_PANEL + label_h + 2 * gap), (28, 28, 30))
    draw = ImageDraw.Draw(sheet)
    fnt = render_emotes.font(14, True)
    for i, (label, path) in enumerate(panels):
        x = gap + i * (PREVIEW_PANEL + gap)
        draw.text((x + 4, gap + 2), f"{body_name} mouth: {label}", font=fnt, fill=(240, 240, 240))
        sheet.paste(Image.open(path).convert("RGB"), (x, gap + label_h))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    sheet.save(out_path)
    print("  wrote", os.path.relpath(out_path, REPO_ROOT))


# ---------------------------------------------------------------------------------------------


def paint(body_name, preview=True):
    print(f"Painting the mouth on {body_name}")
    blend_path = os.path.join(BASE_DIR, f"BaseBody_{body_name}.blend")
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    mesh_obj = bpy.data.objects[f"SK_BaseBody_{body_name}"]
    os.makedirs(TEX_DIR, exist_ok=True)

    obj = mouth_copy(mesh_obj)
    maps, valid = bake_mouth_maps(obj, MOUTH_RES)
    print(f"  {int(valid.sum())} mouth texels at {MOUTH_RES} ({100.0 * valid.mean():.1f}% of the square)")
    colour, masks = paint_mouth(maps)
    write_textures(colour, masks, valid)

    hook_up_mouth(blend_path)
    if preview:
        render_preview(body_name, blend_path, os.path.join(PREVIEW_DIR, f"{body_name}_Mouth.png"))


def write_textures(colour, masks, valid):
    """Islands dilated 8 texels (to_image), the rest of the square filled with the dark inside colour."""
    img = to_image(colour, valid, MOUTH_RES, 3)
    filled = to_image(np.ones(int(valid.sum()), np.float32), valid, MOUTH_RES, 1)[:, :, 0] > 0.5
    img[~filled] = DARK
    mask_img = to_image(masks, valid, MOUTH_RES, 3)
    # The bake margin (16 texels, which keeps the part value) plus the dilation can wrap around
    # the square's edge (np.roll); the islands never reach it, so the border is plain.
    border = max(1, MOUTH_RES // 128)
    for arr, fill in ((img, DARK), (mask_img, 0.0)):
        arr[:border], arr[-border:], arr[:, :border], arr[:, -border:] = fill, fill, fill, fill
    save_png(img, os.path.join(TEX_DIR, "T_Mouth_BaseColor.png"))
    save_png(mask_img, os.path.join(TEX_DIR, "T_Mouth_Masks.png"))


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    preview = "--no-preview" not in argv
    wanted = [a for a in argv if a in ("Male", "Female")] or ["Female"]
    for body_name in wanted:
        paint(body_name, preview)


if __name__ == "__main__":
    main()
