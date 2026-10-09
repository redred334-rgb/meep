"""
Projects a hand-drawn (or AI-drawn) comic reference of the character onto the base body texture.

The drawing must be a full-body T-pose on a plain black background, like
Art/Characters/Base/Reference/Male_Front.png. The silhouette of the drawing is matched to the
silhouette of the 3D body (head top, neck/shoulder corners, hands, armpits, crotch, feet), the
drawing is warped to fit, and every texel that faces the camera is coloured from it. A back-view
drawing is used the same way for the back; without one, the back is filled with a softened copy of
the front (ink lines dissolved) so the colour is continuous.

The head is NOT taken from the full-body drawing (the face is far too small in it). It comes from
close-up face drawings (<Body>_FaceFront.png, <Body>_FaceSide.png) matched by facial landmarks
(FACE_LANDMARKS: pixel positions measured on the drawings) plus the eyelid openings, which are
measured on the mesh (where the eyeball shows through the skin) and traced on the drawings
(EYE_OPENINGS), so the painted eye opening coincides with the mesh one. The back of the head comes
from the back-view drawing. Every drawing is colour-graded to the skin palette first, and hair,
beard and brows are stripped (they are separate layers in the game).

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
EYE_RES = 512  # bake resolution of the eyeball position map (only used to find the lid openings)
PX_PER_M = 520.0  # scale of the body silhouette image used for matching
BG_LEVEL = 0.02  # pixels darker than this are background in the drawing


# ---------------------------------------------------------------------------------------------
# Small image helpers


def blur_f(arr, sigma):
    """Gaussian-like blur (three box blurs) of a float array, along the first two axes."""
    r = max(1, int(round(sigma)))

    def box(a, axis):
        n = a.shape[axis]
        pad = [(0, 0)] * a.ndim
        pad[axis] = (r, r)
        c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
        c = np.concatenate([np.zeros_like(np.take(c, [0], axis=axis)), c], axis=axis)
        hi = np.take(c, np.arange(2 * r + 1, n + 2 * r + 1), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        return ((hi - lo) / (2 * r + 1)).astype(np.float32)

    out = arr.astype(np.float32)
    for _ in range(3):
        out = box(box(out, 0), 1)
    return out


def polygon_mask(shape, polys):
    """Boolean mask of the union of pixel polygons [(x, y), ...]."""
    img = Image.new("L", (shape[1], shape[0]), 0)
    for poly in polys:
        ImageDraw.Draw(img).polygon(poly, fill=255)
    return np.asarray(img) > 0


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
    """Ten landmark indices on a T-pose silhouette, in clockwise order from the top of the head.

    The neck anchors are the corners where the neck meets the trapezius: the deepest point of the
    concavity between the top of the head and the hand. (The narrowest point of the silhouette is
    not usable: on the comic drawing the bearded head is narrower than the neck, which put the
    anchor on the jaw and painted the beard onto the model's neck.)
    """
    x, y = contour[:, 0], contour[:, 1]
    cx = (x.min() + x.max()) / 2
    head = int(np.argmin(y))
    hand_l = int(np.argmin(x))
    hand_r = int(np.argmax(x))
    left = x < cx
    foot_l = int(np.where(left)[0][np.argmax(y[left])])
    foot_r = int(np.where(~left)[0][np.argmax(y[~left])])
    return [head, deepest(contour, head, hand_r), hand_r, deepest(contour, hand_r, foot_r), foot_r,
            deepest(contour, foot_r, foot_l), foot_l, deepest(contour, foot_l, hand_l), hand_l,
            deepest(contour, hand_l, head)]


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


def sample1(img, x, y):
    return sample(img[:, :, None], x, y)[:, 0]


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
    soft = np.asarray(pil.filter(ImageFilter.GaussianBlur(14))).astype(np.float32) / 255
    return soft * mask[:, :, None]  # keep the exact silhouette so the anchors still match


# ---------------------------------------------------------------------------------------------
# Head: landmark-matched projection of the face drawings

# Pixel positions of facial landmarks in the reference drawings (measured on the saved crops).
# The eyes are not in here: their pins come from EYE_OPENINGS and the mesh (see eye_pins()).
FACE_LANDMARKS = {
    "Male": {
        # front view: name -> (px, py); model side: (x, z) taken from Landmarks
        "front": {"nose_tip": (228, 397), "mouth": (228, 478),
                  "lips_top": (228, 468), "lips_bottom": (228, 500),
                  "corner_l": (285, 482), "corner_r": (172, 482), "chin_bottom": (228, 695),
                  "ear_l": (430, 355), "ear_r": (28, 355), "hairline": (228, 195), "head_top": (228, 60),
                  "temple_l": (375, 235), "temple_r": (80, 235), "neck_l": (330, 640), "neck_r": (125, 640),
                  "brow_l": (305, 262), "brow_r": (150, 262)},
        # side view (profile facing left = the character's left side): name -> (px, py); model (y, z)
        "side": {"nose_tip": (12, 345), "chin": (55, 475), "chin_bottom": (118, 540),
                 "brow": (75, 238), "ear": (288, 318), "ear_top": (292, 258), "ear_bottom": (300, 372),
                 "head_back": (440, 230), "head_top": (240, 62), "mouth": (40, 416), "hairline": (62, 178),
                 "neck_front": (165, 600), "neck_back": (400, 600)},
    },
}

# Outline of the visible eye opening (sclera + iris, inside the lid ink) in each face drawing, as
# pixel polygons. Front: character's left eye (image right); the right eye is its mirror about the
# face centre line x = 228. Side: the eye seen in profile.
EYE_OPENINGS = {
    "Male": {
        "front": [(262, 327), (270, 313), (283, 304), (300, 300), (318, 302), (335, 308), (348, 316),
                  (350, 320), (340, 330), (325, 338), (305, 342), (285, 340), (270, 334)],
        "front_mirror_x": 228,
        "side": [(97, 282), (103, 275), (112, 271), (124, 273), (135, 280), (136, 285), (126, 291),
                 (112, 293), (102, 290)],
    },
}


def model_face_points(lm, head_pts, view):
    """Model-space (x,z) or (y,z) positions matching FACE_LANDMARKS."""
    top = head_pts[np.argmax(head_pts[:, 2])]
    back = head_pts[np.argmax(head_pts[:, 1])]
    r = lm.eye_radius
    chin_bottom_z = lm.chin[2] - 0.022
    if view == "front":
        return {"lips_top": (0.0, lm.lips_top), "lips_bottom": (0.0, lm.lips_bottom),
                "nose_tip": (0.0, lm.nose_tip[2]), "mouth": (0.0, 0.5 * (lm.lips_top + lm.lips_bottom)),
                "corner_l": (lm.mouth_half_width, 0.5 * (lm.lips_top + lm.lips_bottom)),
                "corner_r": (-lm.mouth_half_width, 0.5 * (lm.lips_top + lm.lips_bottom)),
                "chin_bottom": (0.0, chin_bottom_z), "ear_l": (lm.ear_x + 0.011, lm.ear_z),
                "ear_r": (-lm.ear_x - 0.011, lm.ear_z), "hairline": (0.0, lm.eye_l[2] + 0.034),
                "head_top": (0.0, top[2]), "temple_l": (0.062, lm.eye_l[2] + 0.03), "temple_r": (-0.062, lm.eye_l[2] + 0.03),
                "neck_l": (0.045, lm.chin[2] - 0.05), "neck_r": (-0.045, lm.chin[2] - 0.05),
                "brow_l": (lm.eye_l[0], lm.eye_l[2] + 1.6 * r), "brow_r": (lm.eye_r[0], lm.eye_r[2] + 1.6 * r)}
    return {"nose_tip": (lm.nose_tip[1], lm.nose_tip[2]), "chin": (lm.chin[1], lm.chin[2]),
            "chin_bottom": (lm.chin[1] + 0.03, chin_bottom_z),
            "brow": (lm.eye_l[1] - 0.004, lm.eye_l[2] + 1.6 * r), "ear": (lm.ear_y, lm.ear_z),
            "ear_top": (lm.ear_y - 0.003, lm.ear_z + 0.028), "ear_bottom": (lm.ear_y + 0.004, lm.ear_z - 0.03),
            "head_back": (back[1], back[2]), "head_top": (top[1], top[2]),
            "mouth": (lm.mouth[1] + 0.004, 0.5 * (lm.lips_top + lm.lips_bottom)),
            "hairline": (lm.nose_tip[1] + 0.02, lm.eye_l[2] + 0.034),
            "neck_front": (lm.chin[1] + 0.045, lm.chin[2] - 0.055), "neck_back": (lm.chin[1] + 0.18, lm.chin[2] - 0.055)}


# --- Eye openings

def edge_pins(mask, xs, ys, prefix, y_up=True):
    """Pins along the outline of an eye-opening mask, in the (xs, ys) coordinates of its cells:
    both corners plus the upper and lower lid edge at 1/4, 1/2 and 3/4 of the width.
    `y_up` is False for image coordinates (y grows downwards)."""
    cols = np.nonzero(mask.any(axis=0))[0]
    c0, c1 = cols.min(), cols.max()
    up, down = (np.max, np.min) if y_up else (np.min, np.max)
    pins = {}
    for name, c in (("c0", c0), ("c1", c1)):
        rows = np.nonzero(mask[:, c])[0]
        pins[f"{prefix}_{name}"] = (xs[c], ys[rows].mean())
    for k, f in enumerate((0.25, 0.5, 0.75)):
        c = int(round(c0 + f * (c1 - c0)))
        rows = np.nonzero(mask[:, c])[0]
        pins[f"{prefix}_u{k}"] = (xs[c], up(ys[rows]))
        pins[f"{prefix}_d{k}"] = (xs[c], down(ys[rows]))
    return pins


def extreme_pins(mask, xs, ys, prefix, y_up=True):
    """Pins at the four extremes (front/back/top/bottom) of an eye-opening mask."""
    rows, cols = np.nonzero(mask)
    top, bottom = (rows.max(), rows.min()) if y_up else (rows.min(), rows.max())
    return {f"{prefix}_front": (xs[cols.min()], ys[rows[cols == cols.min()]].mean()),
            f"{prefix}_back": (xs[cols.max()], ys[rows[cols == cols.max()]].mean()),
            f"{prefix}_top": (xs[cols[rows == top]].mean(), ys[top]),
            f"{prefix}_bottom": (xs[cols[rows == bottom]].mean(), ys[bottom])}


def nearest_depth_raster(pts2, depth, x0, x1, y0, y1, cs):
    """Rasterise points into cells of size cs; each cell keeps its smallest depth (inf if empty),
    with empty cells filled from their 8 neighbours. Returns (grid, xs, ys) with grid[iy, ix]."""
    nx, ny = int(round((x1 - x0) / cs)), int(round((y1 - y0) / cs))
    sel = (pts2[:, 0] >= x0) & (pts2[:, 0] < x1) & (pts2[:, 1] >= y0) & (pts2[:, 1] < y1)
    ix = np.clip(((pts2[sel, 0] - x0) / cs).astype(int), 0, nx - 1)
    iy = np.clip(((pts2[sel, 1] - y0) / cs).astype(int), 0, ny - 1)
    grid = np.full(nx * ny, np.inf, np.float32)
    np.minimum.at(grid, iy * nx + ix, depth[sel])
    grid = grid.reshape(ny, nx)
    filled = grid.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            filled = np.minimum(filled, np.roll(grid, (dy, dx), axis=(0, 1)))
    xs = x0 + (np.arange(nx) + 0.5) * cs
    ys = y0 + (np.arange(ny) + 0.5) * cs
    return filled, xs, ys


def eye_openings(P, eye_P, lm):
    """Where the eyeball shows through the skin, measured on the meshes.

    Skin and eyeball texels are rastered on a 0.5 mm grid from the front (and from the side for
    the profile view); a skin texel is inside the lid opening when the eyeball is in front of it.
    On the MPFB male this gives an opening ~22 mm wide and ~7 mm tall per eye, centred 2 mm
    outboard of and 2 mm below the eyeball centre (the eyeball itself is a 32 mm sphere).
    Returns (in_hole per skin texel, front pins {name: (x, z)}, side pins {name: (y, z)}).
    """
    cs = 0.0005
    z0, z1 = lm.eye_l[2] - 0.03, lm.eye_l[2] + 0.03
    x0, x1 = -0.08, 0.08
    gap = 0.0002  # eyeball must be this far in front of the skin to count as showing through
    # Front view (depth along +Y).
    eye_y, xs, zs = nearest_depth_raster(eye_P[:, [0, 2]], eye_P[:, 1], x0, x1, z0, z1, cs)
    skin_y, _, _ = nearest_depth_raster(P[:, [0, 2]], P[:, 1], x0, x1, z0, z1, cs)
    hole = skin_y > eye_y + gap
    front_pins = {}
    for prefix, side in (("eye_l", 1), ("eye_r", -1)):
        m = hole & ((xs[None, :] * side) > 0)
        m = largest_blob(morph(morph(m, 1, False), 1, True))  # drop stray cells from texel gaps
        front_pins.update(edge_pins(m, xs, zs, prefix))
    # Per-texel test with the texel's own depth (the lid margin shares cells with the socket).
    in_win = (P[:, 0] >= x0) & (P[:, 0] < x1) & (P[:, 2] >= z0) & (P[:, 2] < z1)
    ix = np.clip(((P[:, 0] - x0) / cs).astype(int), 0, len(xs) - 1)
    iz = np.clip(((P[:, 2] - z0) / cs).astype(int), 0, len(zs) - 1)
    in_hole = in_win & (P[:, 1] > eye_y[iz, ix] + gap) & (P[:, 1] < lm.eye_l[1] + 0.05)  # not the back of the head
    # Side view of the character's left eye (depth along -X, so use -x as depth).
    y0, y1 = lm.eye_l[1] - 0.03, lm.eye_l[1] + 0.03
    left_eye = eye_P[eye_P[:, 0] > 0]
    left_skin = P[P[:, 0] > 0]
    eye_x, ys, zs2 = nearest_depth_raster(left_eye[:, [1, 2]], -left_eye[:, 0], y0, y1, z0, z1, cs)
    skin_x, _, _ = nearest_depth_raster(left_skin[:, [1, 2]], -left_skin[:, 0], y0, y1, z0, z1, cs)
    side_hole = largest_blob(morph(morph(skin_x > eye_x + gap, 1, False), 1, True))
    side_pins = extreme_pins(side_hole, ys, zs2, "eye")
    return in_hole, front_pins, side_pins


def drawing_eye_pins(shape, body_name, view):
    """Pixel pins on the drawing's eye openings, named like the model ones. Returns (pins, mask)."""
    spec = EYE_OPENINGS[body_name]
    pins = {}
    if view == "front":
        mirror = spec["front_mirror_x"]
        polys = {"eye_l": spec["front"], "eye_r": [(2 * mirror - x, y) for x, y in spec["front"]]}
    else:
        polys = {"eye": spec["side"]}
    masks = {name: polygon_mask(shape, [poly]) for name, poly in polys.items()}
    xs, ys = np.arange(shape[1], dtype=np.float64), np.arange(shape[0], dtype=np.float64)
    for name, poly in polys.items():
        # The pins use the outline shrunk towards its centre (8% in x, 20% in y) so the lid ink,
        # which hugs the opening in the drawing, lands on the visible lid just outside the mesh
        # opening instead of disappearing into it; the sclera in the margin is masked below.
        cx, cy = np.mean(poly, axis=0)
        inner = [(cx + 0.92 * (x - cx), cy + 0.8 * (y - cy)) for x, y in poly]
        m = polygon_mask(shape, [inner])
        if view == "front":
            pins.update(edge_pins(m, xs, ys, name, y_up=False))
        else:
            pins.update(extreme_pins(m, xs, ys, name, y_up=False))
    union = np.zeros(shape[:2], bool)
    upper_band = np.zeros(shape[:2], bool)
    for name, m in masks.items():
        union |= m
        cy = np.mean(polys[name], axis=0)[1]
        band = morph(m, 12, True) & ~m
        band[int(cy):] = False  # only above the opening: the lower lid line stays thin
        upper_band |= band
    return pins, union, upper_band


def thicken_lid_ink(img, band, grow=2):
    """Grow the ink in the upper-lid band by `grow` px. The eye region is squashed ~5x vertically
    when matched to the mesh opening, so the drawing's 5 px lash line would end up ~1 mm wide; the
    Borderlands look wants it to be the heaviest line on the face (3-5 mm)."""
    lum = img @ np.array([0.3, 0.59, 0.11], np.float32)
    ink = morph((lum < 0.3) & band, grow, True) & band
    out = img.copy()
    out[ink] = np.minimum(out[ink], np.array([0.10, 0.06, 0.05], np.float32))
    return out


def project_head_view(P, N, img, lm, head_pts, body_name, view, eyes):
    """Project a face drawing onto the head using landmark correspondences. Returns (colour, weight).

    `eyes` is (in_hole, pins) from eye_openings(): the model-side eye pins are matched with the
    drawing's eye-opening outline, and any lid texel (outside the mesh opening) that would still
    pick up sclera/iris from inside the drawing's opening is painted with lid skin instead.
    """
    in_hole, eye_model_pins = eyes
    px = dict(FACE_LANDMARKS[body_name][view])
    mp = model_face_points(lm, head_pts, view)
    eye_px, eye_open, lid_band = drawing_eye_pins(img.shape, body_name, view)
    if view == "front":
        img = thicken_lid_ink(img, lid_band)
    px.update(eye_px)
    mp.update(eye_model_pins)
    names = [n for n in px if n in mp]
    src = np.array([mp[n] for n in names], np.float64)
    dst = np.array([px[n] for n in names], np.float64)
    scale = 2500.0  # model metres -> roughly image pixels, keeps the TPS well conditioned
    coef = tps_fit(src * scale, dst, reg=5.0)
    mask = drawing_mask(img)
    ref_src = pb.dilate(img, mask, 80)
    # Lid skin for texels that would otherwise show the drawing's sclera: flat skin from this drawing.
    lid_skin = skin_fill(img, mask, morph(eye_open, 12, True))
    eye_open_soft = blur_f(morph(eye_open, 1, True).astype(np.float32), 0.8)
    # 0 outside the drawing (no background bleed) and 0 on its outline ink, which belongs to the
    # silhouette, not to the surface (the profile's nose outline would otherwise land on the nose).
    soft_mask = blur_f(morph(mask, 4, False).astype(np.float32), 3.0)

    if view == "front":
        q = np.stack([P[:, 0], P[:, 2]], 1)
        depth = -P[:, 1]
        facing = pb.smoothstep(0.15, 0.8, -N[:, 1]) ** 2
    else:
        q = np.stack([P[:, 1], P[:, 2]], 1)
        depth = np.abs(P[:, 0])
        facing = pb.smoothstep(0.2, 0.75, np.abs(N[:, 0])) ** 2  # front and side cross over at ~45 deg
        # The front view owns the eyes: the profile's outer-corner lash wedge would smudge the temple.
        d_eye = np.hypot(P[:, 1] - lm.eye_l[1], P[:, 2] - lm.eye_l[2])
        facing *= 0.3 + 0.7 * pb.smoothstep(0.012, 0.03, d_eye)
    uv = tps_eval(coef, src * scale, q * scale)
    colour = sample(ref_src, uv[:, 0], uv[:, 1])
    spill = sample1(eye_open_soft, uv[:, 0], uv[:, 1]) * (~in_hole)
    colour = colour * (1 - spill)[:, None] + sample(lid_skin, uv[:, 0], uv[:, 1]) * spill[:, None]

    # Visibility along the view direction, on a 1.5 mm grid of the projection plane.
    cell_q = np.floor((q - q.min(axis=0)) / 0.0015).astype(np.int64)
    W = int(cell_q[:, 0].max()) + 1
    cell = cell_q[:, 1] * W + cell_q[:, 0]
    nearest = np.full(cell.max() + 1, -np.inf, np.float32)
    np.maximum.at(nearest, cell, depth)
    visible = depth >= nearest[cell] - 0.01
    inside = sample1(soft_mask, uv[:, 0], uv[:, 1])
    # The face drawings stop at the jaw: below the chin the neck comes from the body drawings.
    below_chin = pb.smoothstep(lm.chin[2] - 0.048, lm.chin[2] - 0.024, P[:, 2])
    return colour, facing * visible * inside * below_chin


def grade_image(img, contrast=1.0):
    """Scale a drawing so its skin median matches the palette and pull saturation down.

    `contrast` > 1 deepens the drawing's own shading (luminance power curve about the lit tone)."""
    flat = img.reshape(-1, 3)
    lum = luminance(flat)
    sat = flat.max(axis=1) - flat.min(axis=1)
    skin_px = (lum > 0.3) & (sat > 0.12) & (flat[:, 0] > flat[:, 2])
    if skin_px.sum() > 100:
        bright = flat[skin_px][lum[skin_px] >= np.percentile(lum[skin_px], 70)]
        lit = np.median(bright, axis=0)
    else:
        lit = SKIN
    graded = flat * (SKIN / np.maximum(lit, 1e-3))
    g_lum = luminance(graded)[:, None]
    graded = g_lum + (graded - g_lum) * 0.8
    if contrast != 1.0:
        lit_lum = float(luminance(SKIN[None])[0])
        g_lum = luminance(graded)[:, None]
        graded = graded * (np.power(np.maximum(g_lum, 1e-4) / lit_lum, contrast - 1.0))
    # Only skin-coloured pixels are re-tinted; grey cloth, ink and eyes keep their colours.
    skinness = pb.smoothstep(0.06, 0.16, sat) * pb.smoothstep(0.0, 0.08, flat[:, 0] - flat[:, 2])
    out = flat * (1 - skinness[:, None]) + graded * skinness[:, None]
    return np.clip(out, 0, 1).reshape(img.shape)


def shadow_bands(colour, shadow):
    half = pb.smoothstep(0.14, 0.22, shadow)[:, None]
    core = pb.smoothstep(0.32, 0.40, shadow)[:, None]
    mul = (1.0 + (HALF_MUL - 1.0) * half) * (1.0 + (CORE_MUL / HALF_MUL - 1.0) * core)
    return np.clip(colour * mul, 0.0, 1.0)


# Regions (pixel polygons) of hair, beard and brows in each face drawing. Anything inside is
# replaced with the drawing's clean skin tone so the base head stays plain; hair is a mesh,
# beard/brows are layers. In the full-body drawings only the head is stripped (the neck is used).
# Regions inside the strip areas that must be kept: the lips sit inside the beard area. These are
# the lip outlines themselves (not the moustache or the skin around them), with a feathered edge.
KEEP_REGIONS = {
    "Male_FaceFront": [[(172, 482), (185, 474), (205, 468), (220, 470), (228, 472), (236, 470), (251, 468),
                        (271, 474), (285, 482), (271, 492), (251, 499), (228, 501), (205, 499), (185, 492)]],
    "Male_FaceSide": [[(66, 401), (47, 401), (40, 407), (38, 414), (44, 418), (40, 426), (45, 436), (62, 441), (74, 437), (70, 418)]],
}

STRIP_REGIONS = {
    "Male_FaceFront": [
        [(0, 0), (445, 0), (445, 330), (400, 262), (350, 215), (300, 200), (228, 192), (160, 200),
         (100, 215), (45, 262), (0, 330)],                                            # hair
        [(28, 400), (95, 452), (165, 442), (228, 438), (292, 442), (360, 452), (425, 400), (420, 470),
         (395, 575), (340, 690), (228, 712), (118, 690), (60, 575), (30, 470)],      # beard + moustache
        [(84, 232), (218, 244), (218, 292), (84, 292)], [(240, 244), (376, 232), (376, 292), (240, 292)],  # brows
    ],
    "Male_FaceSide": [
        [(0, 0), (466, 0), (466, 330), (445, 420), (400, 470), (300, 400), (250, 380), (235, 320),
         (200, 300), (170, 255), (152, 210), (135, 185), (100, 165), (55, 180), (0, 230)],  # hair + sideburn
        [(10, 368), (60, 372), (120, 345), (200, 330), (255, 360), (290, 400), (305, 450), (260, 530),
         (180, 560), (120, 548), (60, 500), (25, 450), (10, 420)],                   # beard + moustache
        [(40, 214), (140, 226), (140, 256), (40, 250)],                             # brow
    ],
    "Male_Back": [[(230, 0), (370, 0), (370, 115), (230, 115)]],                    # back of the hair
    "Male_Front": [[(440, 0), (590, 0), (590, 152), (440, 152)]],                   # whole head (hair, beard)
}
# Drawings whose dark masses are found automatically on top of the regions (the face close-ups);
# on the full-body drawings that would also catch armpit shadows and nipples.
AUTO_STRIP = {"Male_FaceFront", "Male_FaceSide", "Male_Back"}


def skin_fill(img, body, mass):
    """An image of this drawing's clean skin tone with a hint of grain, to paint over `mass`.

    Every pixel takes the colour of the nearest clean skin pixel (lit, saturated, at least 10 px
    from any stripped area and 4 px from ink, so the greyish stubble next to the beard and the
    shadow under the hair line are never used), smoothed over ~20 px. Filled areas therefore
    continue the shading of the surrounding skin instead of showing a flat patch.
    """
    lum = img @ np.array([0.3, 0.59, 0.11], np.float32)
    sat = img.max(axis=2) - img.min(axis=2)
    clean = body & ~morph(mass, 10, True) & (lum > 0.3) & (sat > 0.12) & (img[:, :, 0] > img[:, :, 2])
    clean &= morph(lum > 0.3, 4, False)  # not next to ink lines
    if clean.sum() < 100:
        base = np.broadcast_to(SKIN, img.shape).astype(np.float32)
    else:
        base = blur_f(pb.dilate(img, clean, 400), 20.0)
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(img.shape[:2]).astype(np.float32)
    grain = blur_f(noise, 1.0) + 2.0 * blur_f(noise, 5.0)  # fine speckle + soft mottle
    grain *= 0.018 / grain.std()
    return np.clip(base * (1 + grain[:, :, None]), 0, 1).astype(np.float32)


def strip_hair(img, name=None):
    """Replace hair, beard and brows in a drawing with its clean skin tone, so the base head is plain.

    Hair/beard/brow pixels are the large very dark masses (ink lines are dark too, but thin: a
    5 px opening removes them from the mask) plus the STRIP_REGIONS, minus the KEEP_REGIONS (lips).
    The edge is feathered. Returns (skin-only image, hair+beard mask 0..1).
    """
    lum = img @ np.array([0.3, 0.59, 0.11], np.float32)
    body = drawing_mask(img)
    mass = np.zeros(body.shape, bool)
    if name in AUTO_STRIP:
        dark = (lum < 0.16) & body
        mass = morph(morph(dark, 4, False), 7, True)            # open: drop thin ink, keep masses
        mass = morph(morph(mass, 3, True), 3, False)            # close small holes
    if name in STRIP_REGIONS:
        mass |= polygon_mask(body.shape, STRIP_REGIONS[name])
    mass = morph(mass, 6, True) & body                          # take a margin of the lighter strokes too
    soft = blur_f(mass.astype(np.float32), 2.0) * body  # feathered edge, silhouette untouched
    keep = np.zeros(body.shape, bool)
    if name in KEEP_REGIONS:
        keep |= polygon_mask(body.shape, KEEP_REGIONS[name])
    body_name, _, view = (name or "").partition("_Face")  # "Male_FaceFront" -> "Male", "Front"
    if view and body_name in EYE_OPENINGS:
        # The lid ink hugs the eye opening and the brows sit right above it: keep a 7 px band
        # around the opening so stripping the brows cannot eat the lash line.
        _, eye_open, _ = drawing_eye_pins(img.shape, body_name, view.lower())
        keep |= morph(eye_open, 7, True)
    if keep.any():
        soft *= 1 - blur_f(keep.astype(np.float32), 1.5)
    fill = skin_fill(img, body, mass)
    out = img * (1 - soft[:, :, None]) + fill * soft[:, :, None]
    return out.astype(np.float32), soft


def load_ref(name, plain_head=False, contrast=1.0):
    path = os.path.join(REF_DIR, name + ".png")
    if not os.path.exists(path):
        return None
    img = grade_image(np.asarray(Image.open(path).convert("RGB")).astype(np.float32) / 255, contrast)
    if plain_head:
        img, mass = strip_hair(img, name)
        Image.fromarray((mass * 255).astype(np.uint8)).save(os.path.join(REF_DIR, name + "_HairMask.png"))
    return img


def tone_gain(colour, mask, ref_colour, ref_mask):
    """Per-channel gain that brings the median skin colour of `colour[mask]` to `ref_colour[ref_mask]`."""
    skin = mask & (luminance(colour) > 0.3)
    ref_skin = ref_mask & (luminance(ref_colour) > 0.3)
    if skin.sum() < 500 or ref_skin.sum() < 500:
        return np.ones(3, np.float32)
    return np.clip(np.median(ref_colour[ref_skin], axis=0) / np.maximum(np.median(colour[skin], axis=0), 1e-3), 0.75, 1.35)


def match_tone(colour, w, ref_colour, ref_w, z=None):
    """Scale `colour` so its skin tone matches `ref_colour` where both views overlap.

    With `z`, the gain is found per 1 cm band of height and interpolated, so a cheek that is
    shaded in one drawing and lit in the other still meets without a tone step."""
    both = (w > 0.3) & (ref_w > 0.3)
    if z is None or both.sum() < 2000:
        return np.clip(colour * tone_gain(colour, both, ref_colour, both), 0, 1)
    centres, gains = [], []
    for b0 in np.arange(z[both].min(), z[both].max(), 0.01):
        band = both & (z >= b0) & (z < b0 + 0.01)
        if band.sum() >= 300:
            centres.append(b0 + 0.005)
            gains.append(tone_gain(colour, band, ref_colour, band))
    if len(centres) < 2:
        return np.clip(colour * tone_gain(colour, both, ref_colour, both), 0, 1)
    gains = np.array(gains)
    gains[1:-1] = 0.25 * gains[:-2] + 0.5 * gains[1:-1] + 0.25 * gains[2:]
    g = np.stack([np.interp(z, centres, gains[:, c]) for c in range(3)], 1)
    return np.clip(colour * g, 0, 1)


def bake_maps(mesh_obj, res):
    """Baked per-texel data of the skin: positions, normals, head weight, long-range AO, plus the
    eyeball positions (for the lid openings)."""
    skin_obj = pb.make_copy(mesh_obj, keep_eyes=False)
    pos = pb.bake(skin_obj, res, pb.geom_socket("Position"))
    nrm = pb.bake(skin_obj, res, pb.geom_socket("Normal"))
    head = pb.bake(skin_obj, res, pb.attributes_socket(["head", None, None]))
    ao_l = pb.bake(skin_obj, res, pb.ao_socket(0.15), samples=24)
    eye_obj = pb.make_copy(mesh_obj, keep_eyes=True)
    eye_pos = pb.bake(eye_obj, EYE_RES, pb.geom_socket("Position"))
    valid = pos[:, :, 3].reshape(-1) > 0.5
    P = pos.reshape(-1, 4)[valid][:, :3].copy()
    N = nrm.reshape(-1, 4)[valid][:, :3].copy()
    head_w = head.reshape(-1, 4)[valid][:, 0].copy()
    shadow = 1.0 - ao_l.reshape(-1, 4)[valid][:, 0]
    eye_valid = eye_pos[:, :, 3].reshape(-1) > 0.5
    eye_P = eye_pos.reshape(-1, 4)[eye_valid][:, :3].copy()
    return P, N, head_w, shadow, eye_P, valid


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

    front = load_ref(f"{body_name}_Front", plain_head=True)
    back = load_ref(f"{body_name}_Back")
    face_front = load_ref(f"{body_name}_FaceFront", plain_head=True, contrast=1.15)
    face_side = load_ref(f"{body_name}_FaceSide", plain_head=True, contrast=1.15)
    back_head = load_ref(f"{body_name}_Back", plain_head=True)

    P, N, head_raw, shadow, eye_P, valid = bake_maps(mesh_obj, RES)
    head_w = pb.smoothstep(0.35, 0.65, head_raw)

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
        in_hole, eye_front, eye_side = eye_openings(P, eye_P, lm)
        for k, v in sorted({**eye_front, **eye_side}.items()):
            print(f"  eye pin {k}: ({v[0]:.4f}, {v[1]:.4f})")
        views = [project_head_view(P, N, face_front, lm, head_pts, body_name, "front", (in_hole, eye_front))]
        if face_side is not None:
            c_s, w_s = project_head_view(P, N, face_side, lm, head_pts, body_name, "side", (in_hole, eye_side))
            views.append((match_tone(c_s, w_s, views[0][0], views[0][1], z=P[:, 2]), w_s))
        if back_head is not None:
            colour_bh, w_bh = project_view(mesh_obj, P, N, back_head, mirror_x=True)
            w_bh = w_bh * pb.smoothstep(0.1, 0.6, N[:, 1]) ** 2
            views.append((match_tone(colour_bh, w_bh, views[-1][0], views[-1][1]), w_bh))
        wsum = sum(w for _, w in views)
        head_col = sum(c * w[:, None] for c, w in views) / np.maximum(wsum, 1e-6)[:, None]
        head_cov = np.clip(wsum, 0, 1)
        # One painting: the face drawings' skin tone follows the body drawing's chest tone.
        front_skin = (-N[:, 1] > 0.6)
        gain = tone_gain(head_col, front_skin & (head_w > 0.9) & (head_cov > 0.9) & (P[:, 2] > lm.chin[2]),
                         body, front_skin & (head_w < 0.05) & (w_f > 0.8) & (P[:, 2] > 1.25) & (P[:, 2] < 1.45))
        print("  head -> body tone gain", np.round(gain, 3))
        head_col = np.clip(head_col * gain, 0, 1)
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
