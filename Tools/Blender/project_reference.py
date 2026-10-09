"""
Projects a hand-drawn (or AI-drawn) comic reference of the character onto the base body texture.

The drawing must be a full-body T-pose on a plain black background, like
Art/Characters/Base/Reference/Male_Front.png. The silhouette of the drawing is matched to the
silhouette of the 3D body (head top, neck, hands, armpits, crotch, feet), the drawing is warped
to fit, and every texel that faces the camera is coloured from it. A back-view drawing is used
the same way for the back; without one, the back is filled with a softened copy of the front
(ink lines dissolved) so the colour is continuous.

The head is NOT taken from the full-body drawing (the face is far too small in it). It comes from
close-up face drawings (<Body>_FaceFront.png, <Body>_FaceSide.png) matched by facial landmarks
(FACE_LANDMARKS: pixel positions measured on the drawings), and the back of the head from the
back-view drawing. Every drawing is colour-graded to the skin palette first.

Run after paint_base_body.py:
    blender -b -P Tools/Blender/project_reference.py -- Male

Outputs (Art/Characters/Base/Textures/):
    T_Body_<Body>_BaseColor_Drawn.png   base texture with the drawing projected onto the body
    T_Body_<Body>_DrawnCoverage.png     white where the drawing was used (debug)
"""

import collections
import os
import sys

import bpy  # noqa: E402  (bpy must load before bmesh)
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paint_base_body as pb  # noqa: E402
from comic_paint import CORE_MUL, HALF_MUL, SKIN, luminance  # noqa: E402

REPO_ROOT = pb.REPO_ROOT
BASE_DIR = pb.BASE_DIR
TEX_DIR = pb.TEX_DIR
REF_DIR = os.path.join(BASE_DIR, "Reference")

RES = int(os.environ.get("PAINT_RES", 4096))
PX_PER_M = 520.0  # scale of the body silhouette image used for matching
BG_LEVEL = 0.02  # pixels darker than this are background in the drawing


# ---------------------------------------------------------------------------------------------
# Silhouette matching


def fill_from_border(mask):
    """Pixels of `mask` reachable from the image border (4-connected)."""
    h, w = mask.shape
    out = np.zeros_like(mask)
    q = collections.deque()
    border = [(y, x) for x in range(w) for y in (0, h - 1)] + [(y, x) for y in range(h) for x in (0, w - 1)]
    for y, x in border:
        if mask[y, x] and not out[y, x]:
            out[y, x] = True
            q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not out[ny, nx]:
                out[ny, nx] = True
                q.append((ny, nx))
    return out


def largest_blob(mask):
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    seed = (int(np.median(ys)), int(np.median(xs)))
    if not mask[seed]:
        d = (ys - seed[0]) ** 2 + (xs - seed[1]) ** 2
        seed = (int(ys[np.argmin(d)]), int(xs[np.argmin(d)]))
    out = np.zeros_like(mask)
    out[seed] = True
    q = collections.deque([seed])
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not out[ny, nx]:
                out[ny, nx] = True
                q.append((ny, nx))
    return out


def morph(mask, r, grow):
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                shifted = np.roll(mask, (dy, dx), axis=(0, 1))
                out = (out | shifted) if grow else (out & shifted)
    return out


def drawing_mask(img):
    dark = img.max(axis=2) < BG_LEVEL
    body = morph(morph(~dark, 5, True), 5, False)  # close thin ink gaps so background can't leak in
    mask = ~fill_from_border(~body)
    return largest_blob(mask)


def trace_contour(mask):
    """Clockwise boundary of the blob, as (N, 2) array of (x, y)."""
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    start = (ys.min(), xs[ys == ys.min()].min())
    dirs = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
    pts = [start]
    cur, prev_dir = start, 2
    for _ in range(400000):
        found = False
        for k in range(8):
            d = (prev_dir + 5 + k) % 8
            ny, nx = cur[0] + dirs[d][0], cur[1] + dirs[d][1]
            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx]:
                cur, prev_dir = (ny, nx), d
                found = True
                break
        if not found or (cur == start and len(pts) > 2):
            break
        pts.append(cur)
    return np.array([(p[1], p[0]) for p in pts], np.float32)


def deepest(contour, i0, i1):
    """Index between i0 and i1 (walking forward) furthest from the straight line between them."""
    n = len(contour)
    idx = np.arange(i0, i1 + (n if i1 < i0 else 0)) % n
    a, b = contour[i0], contour[i1]
    ab = b - a
    seg = contour[idx] - a
    dist = np.abs(ab[0] * seg[:, 1] - ab[1] * seg[:, 0]) / (np.linalg.norm(ab) + 1e-6)
    return int(idx[np.argmax(dist)])


def anchors(contour):
    """Ten landmark indices on a T-pose silhouette, in clockwise order from the top of the head."""
    x, y = contour[:, 0], contour[:, 1]
    cx = (x.min() + x.max()) / 2
    head = int(np.argmin(y))
    hand_l = int(np.argmin(x))
    hand_r = int(np.argmax(x))
    left = x < cx
    foot_l = int(np.where(left)[0][np.argmax(y[left])])
    foot_r = int(np.where(~left)[0][np.argmax(y[~left])])
    height = y[foot_l] - y[head]

    def neck(i0, i1):
        n = len(contour)
        idx = np.arange(i0, i1 + (n if i1 < i0 else 0)) % n
        band = idx[(y[idx] > y[head] + 0.09 * height) & (y[idx] < y[head] + 0.2 * height)]
        return int(band[np.argmin(np.abs(x[band] - cx))])

    return [head, neck(head, hand_r), hand_r, deepest(contour, hand_r, foot_r), foot_r,
            deepest(contour, foot_r, foot_l), foot_l, deepest(contour, foot_l, hand_l), hand_l,
            neck(hand_l, head)]


def resample(contour, idx, per_segment=16):
    n = len(contour)
    out = []
    for a, b in zip(idx, idx[1:] + idx[:1]):
        seg = contour[np.arange(a, b + (n if b < a else 0)) % n]
        d = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(seg, axis=0), axis=1))])
        t = np.linspace(0, d[-1], per_segment, endpoint=False)
        out.append(np.stack([np.interp(t, d, seg[:, 0]), np.interp(t, d, seg[:, 1])], 1))
    return np.concatenate(out)


def tps_fit(src, dst, reg=1.0):
    n = len(src)
    r2 = np.sum((src[:, None] - src[None]) ** 2, -1)
    K = np.where(r2 > 0, r2 * np.log(r2 + 1e-12), 0) + reg * np.eye(n)
    Pm = np.hstack([np.ones((n, 1)), src])
    L = np.zeros((n + 3, n + 3))
    L[:n, :n], L[:n, n:], L[n:, :n] = K, Pm, Pm.T
    return np.linalg.solve(L, np.vstack([dst, np.zeros((3, 2))]))


def tps_eval(coef, src, pts, chunk=200000):
    out = np.zeros((len(pts), 2))
    for s in range(0, len(pts), chunk):
        q = pts[s:s + chunk]
        r2 = np.sum((q[:, None] - src[None]) ** 2, -1)
        U = np.where(r2 > 0, r2 * np.log(r2 + 1e-12), 0)
        out[s:s + chunk] = U @ coef[:-3] + coef[-3] + q @ coef[-2:]
    return out


def sample(img, x, y):
    h, w = img.shape[:2]
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x0 + 1] * fx * (1 - fy)
            + img[y0 + 1, x0] * (1 - fx) * fy + img[y0 + 1, x0 + 1] * fx * fy)


# ---------------------------------------------------------------------------------------------
# Body side


def body_silhouette(mesh_obj, mirror_x):
    verts = np.array([v.co[:] for v in mesh_obj.data.vertices], np.float32)
    if mirror_x:
        verts[:, 0] *= -1
    W, H = 1100, 1000
    ox, oz = W / 2, 60 + verts[:, 2].max() * PX_PER_M
    img = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(img)
    for p in mesh_obj.data.polygons:
        draw.polygon([(ox + verts[i, 0] * PX_PER_M, oz - verts[i, 2] * PX_PER_M) for i in p.vertices], fill=255)
    return np.asarray(img) > 0, (ox, oz, W, H)


def project_view(mesh_obj, P, N, ref_img, mirror_x):
    """Colour every texel from one drawing. Returns (colour, weight) with weight 0 where unseen."""
    ref_mask = drawing_mask(ref_img)
    ref_src = pb.dilate(ref_img, ref_mask, 60)  # extend colours past the silhouette for safety

    body_mask, (ox, oz, W, H) = body_silhouette(mesh_obj, mirror_x)
    body_pts = resample(trace_contour(body_mask), anchors(trace_contour(body_mask)))
    ref_pts = resample(trace_contour(ref_mask), anchors(trace_contour(ref_mask)))
    coef = tps_fit(body_pts.astype(np.float64), ref_pts.astype(np.float64))

    sx = -P[:, 0] if mirror_x else P[:, 0]
    bx = ox + sx * PX_PER_M
    by = oz - P[:, 2] * PX_PER_M
    uv = tps_eval(coef, body_pts.astype(np.float64), np.stack([bx, by], 1))
    colour = sample(ref_src, uv[:, 0], uv[:, 1])

    # Visibility: nearest texel wins in each pixel cell of the view.
    depth_sign = 1.0 if mirror_x else -1.0  # back view looks along -Y, front along +Y
    depth = P[:, 1] * depth_sign
    cell = np.clip(by.astype(int), 0, H - 1) * W + np.clip(bx.astype(int), 0, W - 1)
    nearest = np.full(W * H, -np.inf, np.float32)
    np.maximum.at(nearest, cell, depth)
    visible = depth >= nearest[cell] - 0.012
    facing = pb.smoothstep(0.1, 0.45, N[:, 1] * depth_sign)
    return colour, facing * visible


def soft_copy(img, mask):
    """The drawing with its ink lines dissolved: for filling parts no drawing covers."""
    src = pb.dilate(img, mask, 60)
    pil = Image.fromarray((np.clip(src, 0, 1) * 255).astype(np.uint8))
    return np.asarray(pil.filter(ImageFilter.GaussianBlur(14))).astype(np.float32) / 255


# ---------------------------------------------------------------------------------------------
# Head: landmark-matched projection of the face drawings

# Pixel positions of facial landmarks in the reference drawings (measured on the saved crops).
FACE_LANDMARKS = {
    "Male": {
        # front view: name -> (px, py); model side: (x, z) taken from Landmarks
        "front": {"eye_l": (305, 318), "eye_r": (150, 318), "nose_tip": (228, 397), "mouth": (228, 478),
                  "corner_l": (282, 481), "corner_r": (175, 481), "chin_bottom": (228, 695),
                  "ear_l": (430, 355), "ear_r": (28, 355), "hairline": (228, 195), "head_top": (228, 60),
                  "temple_l": (375, 235), "temple_r": (80, 235), "neck_l": (330, 640), "neck_r": (125, 640)},
        # side view (profile facing left = the character's left side): name -> (px, py); model (y, z)
        "side": {"nose_tip": (12, 345), "chin": (55, 475), "chin_bottom": (118, 540), "eye": (105, 283),
                 "brow": (75, 238), "ear": (288, 318), "ear_top": (292, 258), "ear_bottom": (300, 372),
                 "head_back": (440, 230), "head_top": (240, 62), "mouth": (32, 415), "hairline": (62, 178),
                 "neck_front": (165, 600), "neck_back": (400, 600)},
    },
}


def model_face_points(lm, head_pts, view):
    """Model-space (x,z) or (y,z) positions matching FACE_LANDMARKS."""
    top = head_pts[np.argmax(head_pts[:, 2])]
    back = head_pts[np.argmax(head_pts[:, 1])]
    r = lm.eye_radius
    chin_bottom_z = lm.chin[2] - 0.022
    if view == "front":
        return {"eye_l": (lm.eye_l[0], lm.eye_l[2]), "eye_r": (lm.eye_r[0], lm.eye_r[2]),
                "nose_tip": (0.0, lm.nose_tip[2]), "mouth": (0.0, 0.5 * (lm.lips_top + lm.lips_bottom)),
                "corner_l": (lm.mouth_half_width, 0.5 * (lm.lips_top + lm.lips_bottom)),
                "corner_r": (-lm.mouth_half_width, 0.5 * (lm.lips_top + lm.lips_bottom)),
                "chin_bottom": (0.0, chin_bottom_z), "ear_l": (lm.ear_x + 0.011, lm.ear_z),
                "ear_r": (-lm.ear_x - 0.011, lm.ear_z), "hairline": (0.0, lm.eye_l[2] + 0.034),
                "head_top": (0.0, top[2]), "temple_l": (0.062, lm.eye_l[2] + 0.03), "temple_r": (-0.062, lm.eye_l[2] + 0.03),
                "neck_l": (0.045, lm.chin[2] - 0.05), "neck_r": (-0.045, lm.chin[2] - 0.05)}
    return {"nose_tip": (lm.nose_tip[1], lm.nose_tip[2]), "chin": (lm.chin[1], lm.chin[2]),
            "chin_bottom": (lm.chin[1] + 0.03, chin_bottom_z), "eye": (lm.eye_l[1] + 0.006, lm.eye_l[2]),
            "brow": (lm.eye_l[1] - 0.004, lm.eye_l[2] + 1.6 * r), "ear": (lm.ear_y, lm.ear_z),
            "ear_top": (lm.ear_y - 0.003, lm.ear_z + 0.028), "ear_bottom": (lm.ear_y + 0.004, lm.ear_z - 0.03),
            "head_back": (back[1], back[2]), "head_top": (top[1], top[2]),
            "mouth": (lm.mouth[1] + 0.004, 0.5 * (lm.lips_top + lm.lips_bottom)),
            "hairline": (lm.nose_tip[1] + 0.02, lm.eye_l[2] + 0.034),
            "neck_front": (lm.chin[1] + 0.045, lm.chin[2] - 0.055), "neck_back": (lm.chin[1] + 0.18, lm.chin[2] - 0.055)}


def project_head_view(P, N, img, lm, head_pts, body_name, view):
    """Project a face drawing onto the head using landmark correspondences. Returns (colour, weight)."""
    px = FACE_LANDMARKS[body_name][view]
    mp = model_face_points(lm, head_pts, view)
    names = [n for n in px if n in mp]
    src = np.array([mp[n] for n in names], np.float64)
    dst = np.array([px[n] for n in names], np.float64)
    scale = 2500.0  # model metres -> roughly image pixels, keeps the TPS well conditioned
    coef = tps_fit(src * scale, dst, reg=5.0)
    ref_src = pb.dilate(img, drawing_mask(img), 80)

    if view == "front":
        q = np.stack([P[:, 0], P[:, 2]], 1)
        depth = -P[:, 1]
        facing = pb.smoothstep(0.15, 0.55, -N[:, 1])
    else:
        q = np.stack([P[:, 1], P[:, 2]], 1)
        depth = np.abs(P[:, 0])
        facing = pb.smoothstep(0.3, 0.75, np.abs(N[:, 0]))
    uv = tps_eval(coef, src * scale, q * scale)
    colour = sample(ref_src, uv[:, 0], uv[:, 1])

    # Visibility along the view direction, on a 1 mm grid of the projection plane.
    cell_q = np.floor((q - q.min(axis=0)) / 0.0015).astype(np.int64)
    W = int(cell_q[:, 0].max()) + 1
    cell = cell_q[:, 1] * W + cell_q[:, 0]
    nearest = np.full(cell.max() + 1, -np.inf, np.float32)
    np.maximum.at(nearest, cell, depth)
    visible = depth >= nearest[cell] - 0.01
    inside = (uv[:, 0] > 0) & (uv[:, 0] < img.shape[1] - 1) & (uv[:, 1] > 0) & (uv[:, 1] < img.shape[0] - 1)
    return colour, facing * visible * inside


def grade_image(img):
    """Scale a drawing so its skin median matches the palette and pull saturation down."""
    flat = img.reshape(-1, 3)
    lum = luminance(flat)
    sat = flat.max(axis=1) - flat.min(axis=1)
    skin_px = (lum > 0.3) & (sat > 0.12) & (flat[:, 0] > flat[:, 2])
    median = np.median(flat[skin_px], axis=0) if skin_px.sum() > 100 else SKIN
    graded = flat * (SKIN / np.maximum(median, 1e-3))
    g_lum = luminance(graded)[:, None]
    graded = g_lum + (graded - g_lum) * 0.8
    return np.clip(graded, 0, 1).reshape(img.shape)


def shadow_bands(colour, shadow):
    half = pb.smoothstep(0.14, 0.22, shadow)[:, None]
    core = pb.smoothstep(0.32, 0.40, shadow)[:, None]
    mul = (1.0 + (HALF_MUL - 1.0) * half) * (1.0 + (CORE_MUL / HALF_MUL - 1.0) * core)
    return np.clip(colour * mul, 0.0, 1.0)


def strip_hair(img):
    """Replace hair, beard and brows in a face drawing with nearby skin, so the base head is plain.

    Hair/beard/brow pixels are the large very dark masses (ink lines are dark too, but thin: a
    5 px opening removes them from the mask). Returns (skin-only image, hair+beard mask 0..1).
    """
    lum = img @ np.array([0.3, 0.59, 0.11], np.float32)
    body = drawing_mask(img)
    dark = (lum < 0.16) & body
    mass = morph(morph(dark, 4, False), 7, True)            # open: drop thin ink, keep masses
    mass = morph(morph(mass, 3, True), 3, False)            # close small holes
    skin = body & ~mass
    filled = pb.dilate(img, skin, 120)
    blur = np.asarray(Image.fromarray((np.clip(filled, 0, 1) * 255).astype(np.uint8))
                      .filter(ImageFilter.GaussianBlur(9))).astype(np.float32) / 255
    out = np.where(mass[:, :, None], blur, img)
    return out, mass.astype(np.float32)


def load_ref(name, plain_head=False):
    path = os.path.join(REF_DIR, name + ".png")
    if not os.path.exists(path):
        return None
    img = np.asarray(Image.open(path).convert("RGB")).astype(np.float32) / 255
    if plain_head:
        img, mass = strip_hair(img)
        Image.fromarray((mass * 255).astype(np.uint8)).save(os.path.join(REF_DIR, name + "_HairMask.png"))
    return grade_image(img)


def main():
    body_name = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "Male"
    blend_path = os.path.join(BASE_DIR, f"BaseBody_{body_name}.blend")
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    mesh_obj = bpy.data.objects[f"SK_BaseBody_{body_name}"]
    armature = bpy.data.objects["Armature"]
    lm = pb.Landmarks(mesh_obj, armature)
    head_idx = mesh_obj.vertex_groups["head"].index
    head_pts = np.array([v.co[:] for v in mesh_obj.data.vertices
                         if any(g.group == head_idx and g.weight > 0.5 for g in v.groups)], np.float32)

    front = load_ref(f"{body_name}_Front")
    back = load_ref(f"{body_name}_Back")
    face_front = load_ref(f"{body_name}_FaceFront", plain_head=True)
    face_side = load_ref(f"{body_name}_FaceSide", plain_head=True)
    back_head = load_ref(f"{body_name}_Back", plain_head=True)

    skin_obj = pb.make_copy(mesh_obj, keep_eyes=False)
    pos = pb.bake(skin_obj, RES, pb.geom_socket("Position"))
    nrm = pb.bake(skin_obj, RES, pb.geom_socket("Normal"))
    head = pb.bake(skin_obj, RES, pb.attributes_socket(["head", "neck_01", None]))
    ao_l = pb.bake(skin_obj, RES, pb.ao_socket(0.15), samples=24)
    valid = pos[:, :, 3].reshape(-1) > 0.5
    P = pos.reshape(-1, 4)[valid][:, :3].copy()
    N = nrm.reshape(-1, 4)[valid][:, :3].copy()
    head_w = pb.smoothstep(0.35, 0.65, head.reshape(-1, 4)[valid][:, 0])
    shadow = 1.0 - ao_l.reshape(-1, 4)[valid][:, 0]
    del pos, nrm, head, ao_l

    # --- Body: front and back T-pose drawings matched by silhouette.
    colour_f, w_f = project_view(mesh_obj, P, N, front, mirror_x=False)
    if back is not None:
        colour_b, w_b = project_view(mesh_obj, P, N, back, mirror_x=True)
    else:
        colour_b, w_b = project_view(mesh_obj, P, N, soft_copy(front, drawing_mask(front)), mirror_x=True)
        w_b *= 0.999
    colour_fill, _ = project_view(mesh_obj, P, N, soft_copy(front, drawing_mask(front)), mirror_x=False)
    total = np.clip(w_f + w_b, 0, 1)
    body = (colour_f * w_f[:, None] + colour_b * w_b[:, None]) / np.maximum(w_f + w_b, 1e-6)[:, None]
    body = body * total[:, None] + colour_fill * (1 - total)[:, None]

    # --- Head: face drawings matched by landmarks (front + profile), back of the head from the back view.
    drawn = body
    coverage = total
    if face_front is not None and body_name in FACE_LANDMARKS:
        views = [project_head_view(P, N, face_front, lm, head_pts, body_name, "front")]
        if face_side is not None:
            views.append(project_head_view(P, N, face_side, lm, head_pts, body_name, "side"))
        if back_head is not None:
            colour_bh, w_bh = project_view(mesh_obj, P, N, back_head, mirror_x=True)
            views.append((colour_bh, w_bh * pb.smoothstep(0.2, 0.6, N[:, 1])))
        wsum = sum(w for _, w in views)
        head_col = sum(c * w[:, None] for c, w in views) / np.maximum(wsum, 1e-6)[:, None]
        head_cov = np.clip(wsum, 0, 1)
        head_col = head_col * head_cov[:, None] + body * (1 - head_cov)[:, None]
        drawn = head_col * head_w[:, None] + body * (1 - head_w)[:, None]
        coverage = np.maximum(total, head_cov * head_w)

    out = shadow_bands(drawn, shadow)
    pb.save_png(pb.to_image(out, valid, RES, 3), os.path.join(TEX_DIR, f"T_Body_{body_name}_BaseColor_Drawn.png"))
    pb.save_png(pb.to_image(coverage, valid, RES, 1),
                os.path.join(TEX_DIR, f"T_Body_{body_name}_DrawnCoverage.png"), size=1024)
    pb.hook_up_materials(body_name, blend_path, colour_file=f"T_Body_{body_name}_BaseColor_Drawn.png")


if __name__ == "__main__":
    main()
