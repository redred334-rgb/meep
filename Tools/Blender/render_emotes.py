"""Render the emote library (Art/Characters/Base/Emotes.json) on a base body as a contact sheet.

    python3 Tools/Blender/render_emotes.py -- Female              # Previews/Female_Emotes.png
    python3 Tools/Blender/render_emotes.py -- Male --emote Rage   # one emote, large, for checking
    blender -b -P Tools/Blender/render_emotes.py -- Female        # same thing from Blender itself

Options after the body name:
    --emote NAME      render only this emote at --big size to Previews/<Body>_Emote_<NAME>.png
    --out PATH        write the image somewhere else
    --samples N       Cycles samples per frame (default 32, denoised)
    --frame WxH       size of one sheet cell (default 300x360)
    --big WxH         size of a single-emote render (default 900x1080)
    --per-row N       cells per row on the sheet (default 6)
    --plain           don't apply the borderlands_default_face sliders underneath the emotes

What it does for every emote: resets the face, applies the Exp_* morph weights, rotates the
`jaw` bone about its local X by jaw_deg, rotates `eye_l` / `eye_r` (yaw about local Z, pitch
about local X, or aims each eye at a point `converge` metres ahead of the face), renders with
Cycles on the CPU using whatever M_Skin / M_Eyes / M_Mouth the .blend currently has, and
resets again. The camera and lights it adds live only in memory: the .blend is never saved.
"""
import json
import math
import os
import sys

import bpy
from mathutils import Euler, Vector

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BASE_DIR = os.path.join(REPO, "Art", "Characters", "Base")
EMOTES_JSON = os.path.join(BASE_DIR, "Emotes.json")
PREVIEW_DIR = os.path.join(BASE_DIR, "Previews")
FONT_DIR = "/usr/share/fonts/truetype/dejavu"

FACE_BONES = ("jaw", "eye_l", "eye_r")
PER_ROW = 6
FRAME = (300, 360)
BIG = (900, 1080)
SAMPLES = 32
BACKGROUND = (0.62, 0.63, 0.66)


# ----------------------------------------------------------------------------- scene helpers

def parse_args(argv):
    args = {"body": "Female", "emote": None, "out": None, "samples": SAMPLES, "frame": FRAME,
            "big": BIG, "per_row": PER_ROW, "plain": False}
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    it = iter(argv)
    for a in it:
        if a in ("Male", "Female"):
            args["body"] = a
        elif a == "--emote":
            args["emote"] = next(it)
        elif a == "--out":
            args["out"] = next(it)
        elif a == "--samples":
            args["samples"] = int(next(it))
        elif a == "--frame":
            args["frame"] = tuple(int(v) for v in next(it).lower().split("x"))
        elif a == "--big":
            args["big"] = tuple(int(v) for v in next(it).lower().split("x"))
        elif a == "--per-row":
            args["per_row"] = int(next(it))
        elif a == "--plain":
            args["plain"] = True
        else:
            raise SystemExit("unknown argument %r" % a)
    return args


def find_body():
    mesh = next(o for o in bpy.data.objects if o.type == 'MESH' and o.name.startswith("SK_BaseBody"))
    arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
    return mesh, arm


def face_frame(mesh, arm):
    """Where to point the camera: centre between the eyes and the chin, in world space."""
    eye_l = arm.matrix_world @ arm.data.bones["eye_l"].head_local
    eye_r = arm.matrix_world @ arm.data.bones["eye_r"].head_local
    chin = arm.matrix_world @ arm.data.bones["jaw"].tail_local
    top = max((mesh.matrix_world @ v.co).z for v in mesh.data.vertices)
    eyes = (eye_l + eye_r) / 2
    centre = Vector((0.0, eyes.y, (top + chin.z) / 2 - 0.015))
    return eyes, centre, top - chin.z


def setup_scene(mesh, arm, size, samples):
    scene = bpy.context.scene
    eyes, centre, head_h = face_frame(mesh, arm)

    cam_data = bpy.data.cameras.new("EmoteCam")
    cam_data.lens = 110.0
    cam_data.sensor_fit = 'VERTICAL'
    cam_data.sensor_height = 24.0
    cam = bpy.data.objects.new("EmoteCam", cam_data)
    scene.collection.objects.link(cam)
    # Frame: head height plus margins, portrait. Face is at -Y, camera looks along +Y.
    want_h = head_h * 1.55
    dist = want_h / (cam_data.sensor_height / cam_data.lens)
    cam.location = Vector((0.0, centre.y - dist, centre.z))
    cam.rotation_euler = Euler((math.pi / 2, 0.0, 0.0), 'XYZ')
    scene.camera = cam

    lights = []

    def add_light(name, kind, energy, loc, size=0.5, colour=(1.0, 1.0, 1.0)):
        data = bpy.data.lights.new(name, kind)
        data.energy = energy
        data.color = colour
        if kind == 'AREA':
            data.size = size
        obj = bpy.data.objects.new(name, data)
        obj.location = Vector(loc)
        direction = centre - obj.location
        obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
        scene.collection.objects.link(obj)
        lights.append(obj)
        return obj

    add_light("EmoteKey", 'AREA', 14.0, (0.55, centre.y - 0.9, centre.z + 0.45), size=0.6, colour=(1.0, 0.96, 0.9))
    add_light("EmoteFill", 'AREA', 5.0, (-0.8, centre.y - 0.8, centre.z + 0.05), size=1.0, colour=(0.9, 0.95, 1.0))
    add_light("EmoteRim", 'AREA', 10.0, (-0.4, centre.y + 0.5, centre.z + 0.5), size=0.4)

    if scene.world is None:
        scene.world = bpy.data.worlds.new("EmoteWorld")
    world = scene.world
    if not world.use_nodes:
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (*BACKGROUND, 1.0)
        bg.inputs[1].default_value = 0.35

    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    try:
        scene.cycles.denoiser = 'OPENIMAGEDENOISE'
        scene.cycles.denoising_use_gpu = False
    except Exception:
        pass
    scene.cycles.max_bounces = 4
    scene.render.resolution_x, scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    return cam, lights


def teardown_scene(cam, lights):
    for obj in [cam] + lights:
        data = obj.data
        bpy.data.objects.remove(obj)
        if isinstance(data, bpy.types.Camera):
            bpy.data.cameras.remove(data)
        else:
            bpy.data.lights.remove(data)


def ensure_iris(mesh, arm):
    """If M_Eyes is still the untextured placeholder, give it a procedural iris in memory so eye
    direction can be checked. Centred on each eyeball's forward-most vertex, sized from the
    eyeball's angular radius in UV space. Nothing is saved, so the .blend keeps its material."""
    mat = mesh.data.materials.get("M_Eyes") if mesh.data.materials else None
    if mat is None or mat.node_tree is None:
        return
    if any(n.type == 'TEX_IMAGE' and n.image for n in mat.node_tree.nodes):
        return  # real eye texture already hooked up: use it
    uv = mesh.data.uv_layers.active.data
    verts = mesh.data.vertices
    per_vert_uv = {}
    eye_vids = set()
    for p in mesh.data.polygons:
        if p.material_index != 1:
            continue
        for vi, li in zip(p.vertices, p.loop_indices):
            per_vert_uv[vi] = uv[li].uv.copy()
            eye_vids.add(vi)
    if not eye_vids:
        return
    centres = []
    for bone_name, sign in (("eye_l", 1), ("eye_r", -1)):
        side = [vi for vi in eye_vids if (verts[vi].co.x > 0) == (sign > 0)]
        if not side:
            continue
        head = arm.data.bones[bone_name].head_local
        forward = (arm.data.bones[bone_name].tail_local - head).normalized()
        front = max(side, key=lambda vi: (verts[vi].co - head).normalized().dot(forward))
        c = per_vert_uv[front]
        ring = []
        for vi in side:
            d = (verts[vi].co - head).normalized().dot(forward)
            if 0.87 < d < 0.95:  # 18..30 degrees off the axis: the iris edge
                ring.append((per_vert_uv[vi] - c).length)
        ring.sort()
        radius = ring[len(ring) // 2] if ring else 0.08
        centres.append((c, radius))
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        return
    uvn = nodes.new("ShaderNodeUVMap")
    dist_outputs = []
    for c, radius in centres:
        sub = nodes.new("ShaderNodeVectorMath")
        sub.operation = 'SUBTRACT'
        sub.inputs[1].default_value = (c.x, c.y, 0.0)
        links.new(uvn.outputs["UV"], sub.inputs[0])
        length = nodes.new("ShaderNodeVectorMath")
        length.operation = 'LENGTH'
        links.new(sub.outputs["Vector"], length.inputs[0])
        scale = nodes.new("ShaderNodeMath")
        scale.operation = 'DIVIDE'
        scale.inputs[1].default_value = radius
        links.new(length.outputs["Value"], scale.inputs[0])
        dist_outputs.append(scale.outputs["Value"])
    if not dist_outputs:
        return
    d = dist_outputs[0]
    for other in dist_outputs[1:]:
        m = nodes.new("ShaderNodeMath")
        m.operation = 'MINIMUM'
        links.new(d, m.inputs[0])
        links.new(other, m.inputs[1])
        d = m.outputs["Value"]
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = 'CONSTANT'
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (0.01, 0.01, 0.01, 1.0)   # pupil
    ramp.color_ramp.elements[1].position = 0.36
    ramp.color_ramp.elements[1].color = (0.12, 0.38, 0.62, 1.0)   # iris
    e = ramp.color_ramp.elements.new(0.9)
    e.color = (0.04, 0.1, 0.18, 1.0)                              # limbal ring
    e = ramp.color_ramp.elements.new(1.0)
    e.color = (0.92, 0.9, 0.88, 1.0)                               # sclera
    links.new(d, ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.25
    print("M_Eyes: procedural iris added in memory (not saved)")


# ----------------------------------------------------------------------------- face control

def reset_face(mesh, arm, keep=()):
    """Zero every Exp_* morph and the face bones. Shape keys in `keep` are left alone."""
    for kb in mesh.data.shape_keys.key_blocks:
        if kb.name.startswith("Exp_") and kb.name not in keep:
            kb.value = 0.0
    for name in FACE_BONES:
        pb = arm.pose.bones[name]
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.location = (0.0, 0.0, 0.0)


def apply_sliders(mesh, sliders):
    """Character-creator sliders: a shape key name -> value (0..1). Negative values would use
    the paired morph in the game; here each entry names the morph directly."""
    kbs = mesh.data.shape_keys.key_blocks
    for name, value in sliders.items():
        if name in kbs:
            kbs[name].value = float(value)
        else:
            print("  slider %s not in mesh, skipped" % name)


def eye_look(arm, bone_name, yaw_deg, pitch_deg):
    """Yaw about the eye bone's local Z (+ = character's left), pitch about local X (+ = up)."""
    pb = arm.pose.bones[bone_name]
    pb.rotation_mode = 'XYZ'  # X first, then Z: Rz(yaw) @ Rx(pitch)
    pb.rotation_euler = (math.radians(pitch_deg), 0.0, math.radians(yaw_deg))


def eye_converge(arm, bone_name, target_world):
    """Aim the eye bone's local Y axis at a world-space point."""
    bone = arm.data.bones[bone_name]
    local = (arm.matrix_world @ bone.matrix_local).inverted() @ target_world
    yaw = math.degrees(math.atan2(-local.x, local.y))
    pitch = math.degrees(math.atan2(local.z, math.hypot(local.x, local.y)))
    eye_look(arm, bone_name, yaw, pitch)
    return yaw, pitch


def apply_emote(mesh, arm, emote, keep=()):
    reset_face(mesh, arm, keep)
    kbs = mesh.data.shape_keys.key_blocks
    for name, weight in emote.get("morphs", {}).items():
        if name not in kbs:
            raise KeyError("%s: morph %s not in mesh" % (emote["name"], name))
        kbs[name].value = max(0.0, min(1.0, float(weight)))

    jaw = arm.pose.bones["jaw"]
    jaw.rotation_mode = 'XYZ'
    jaw.rotation_euler = (math.radians(float(emote.get("jaw_deg", 0.0))), 0.0, 0.0)

    eyes = emote.get("eyes")
    if eyes:
        if isinstance(eyes, str):
            eyes = {"converge": 0.6} if eyes == "converge" else {}
        if "converge" in eyes:
            dist = float(eyes["converge"]) if eyes["converge"] is not None else 0.6
            eye_l = arm.matrix_world @ arm.data.bones["eye_l"].head_local
            eye_r = arm.matrix_world @ arm.data.bones["eye_r"].head_local
            mid = (eye_l + eye_r) / 2
            target = mid + Vector((0.0, -dist, 0.0))  # the face looks down -Y
            # optional offsets so a converged gaze can still glance off-centre
            yaw_off = math.radians(float(eyes.get("yaw", 0.0)))
            pitch_off = math.radians(float(eyes.get("pitch", 0.0)))
            if yaw_off or pitch_off:
                off = Vector((-math.sin(yaw_off) * math.cos(pitch_off), -math.cos(yaw_off) * math.cos(pitch_off), math.sin(pitch_off)))
                target = mid + off * dist
            for name in ("eye_l", "eye_r"):
                eye_converge(arm, name, target)
        else:
            for name in ("eye_l", "eye_r"):
                eye_look(arm, name, float(eyes.get("yaw", 0.0)), float(eyes.get("pitch", 0.0)))


# ----------------------------------------------------------------------------- rendering

def render_to(path):
    scene = bpy.context.scene
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    except OSError:
        return ImageFont.load_default()


def wrap(draw, text, fnt, width):
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = (line + " " + w).strip()
        if draw.textlength(trial, font=fnt) <= width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def cell_label(emote):
    bits = []
    if emote.get("jaw_deg"):
        bits.append("jaw %+g" % emote["jaw_deg"])
    eyes = emote.get("eyes")
    if isinstance(eyes, dict):
        if "converge" in eyes:
            bits.append("eyes converge %.1f m" % float(eyes["converge"] or 0.6))
        elif eyes.get("yaw") or eyes.get("pitch"):
            bits.append("eyes yaw %+g pitch %+g" % (eyes.get("yaw", 0), eyes.get("pitch", 0)))
    return ", ".join(bits)


def build_sheet(frames, out_path, body, frame_size, per_row, subtitle):
    fw, fh = frame_size
    label_h = 46
    gap = 6
    title_h = 40
    rows = (len(frames) + per_row - 1) // per_row
    W = per_row * fw + (per_row + 1) * gap
    H = title_h + rows * (fh + label_h + gap) + gap
    sheet = Image.new("RGB", (W, H), (28, 28, 30))
    draw = ImageDraw.Draw(sheet)
    f_title, f_name, f_small = font(20, True), font(15, True), font(11)
    draw.text((gap, 10), "%s emotes" % body, font=f_title, fill=(240, 240, 240))
    draw.text((gap + draw.textlength("%s emotes" % body, font=f_title) + 14, 16), subtitle, font=f_small, fill=(170, 170, 175))
    for i, (emote, path) in enumerate(frames):
        r, c = divmod(i, per_row)
        x = gap + c * (fw + gap)
        y = title_h + r * (fh + label_h + gap)
        draw.text((x + 4, y + 4), emote["name"], font=f_name, fill=(255, 255, 255))
        sub = cell_label(emote)
        if sub:
            draw.text((x + 4, y + 23), sub, font=f_small, fill=(170, 200, 255))
        morphs = ", ".join("%s %.2g" % (k.replace("Exp_", ""), v) for k, v in emote.get("morphs", {}).items())
        if morphs:
            lines = wrap(draw, morphs, f_small, fw - 8)
            draw.text((x + 4, y + 34), lines[0] + (" ..." if len(lines) > 1 else ""), font=f_small, fill=(150, 150, 155))
        img = Image.open(path).convert("RGB")
        if img.size != (fw, fh):
            img = img.resize((fw, fh), Image.LANCZOS)
        sheet.paste(img, (x, y + label_h))
    sheet.save(out_path)


def main():
    args = parse_args(sys.argv)
    body = args["body"]
    blend_path = os.path.join(BASE_DIR, "BaseBody_%s.blend" % body)
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    mesh, arm = find_body()

    with open(EMOTES_JSON) as f:
        library = json.load(f)
    emotes = library["emotes"]
    sliders = {} if args["plain"] else library.get("borderlands_default_face", {}).get("sliders", {})

    tmp_dir = os.path.join(os.environ.get("TMPDIR", "/tmp"), "render_emotes_%s" % body)
    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(PREVIEW_DIR, exist_ok=True)

    ensure_iris(mesh, arm)
    reset_face(mesh, arm)
    apply_sliders(mesh, sliders)
    keep = tuple(sliders)

    try:
        if args["emote"]:
            wanted = [e for e in emotes if e["name"].lower() == args["emote"].lower()]
            if not wanted:
                raise SystemExit("no emote called %r; have: %s" % (args["emote"], ", ".join(e["name"] for e in emotes)))
            cam, lights = setup_scene(mesh, arm, args["big"], args["samples"])
            emote = wanted[0]
            apply_emote(mesh, arm, emote, keep)
            out = args["out"] or os.path.join(PREVIEW_DIR, "%s_Emote_%s.png" % (body, emote["name"]))
            render_to(out)
            print("wrote", out)
        else:
            cam, lights = setup_scene(mesh, arm, args["frame"], args["samples"])
            frames = []
            for emote in emotes:
                apply_emote(mesh, arm, emote, keep)
                path = os.path.join(tmp_dir, "%s.png" % emote["name"])
                render_to(path)
                frames.append((emote, path))
                print("rendered", emote["name"])
            out = args["out"] or os.path.join(PREVIEW_DIR, "%s_Emotes.png" % body)
            if Image is None:
                raise SystemExit("Pillow is needed to assemble the sheet; frames are in " + tmp_dir)
            subtitle = ("base look: " + ", ".join("%s %g" % kv for kv in sliders.items())) if sliders else "plain mesh"
            subtitle += "  |  Cycles CPU %d spp, M_Skin as in the .blend" % args["samples"]
            build_sheet(frames, out, body, args["frame"], args["per_row"], subtitle)
            print("wrote", out)
    finally:
        reset_face(mesh, arm)
        for name in sliders:
            if name in mesh.data.shape_keys.key_blocks:
                mesh.data.shape_keys.key_blocks[name].value = 0.0
        try:
            teardown_scene(cam, lights)
        except NameError:
            pass
        # Nothing is saved: the .blend on disk stays as it was.


if __name__ == "__main__":
    main()
