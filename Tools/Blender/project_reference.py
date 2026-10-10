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
    T_Body_<Body>_Masks.png             R skin / G hair / B underwear tint masks matching the drawn texture
    T_Body_<Body>_Beard.png, _Brows.png swappable layers cut from the face drawings (white = hair)
    T_Body_<Body>_DrawnCoverage.png     white where the drawing was used (debug)
"""

import collections
import json
import os
import sys

import bpy  # noqa: E402  (bpy must load before bmesh)
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paint_base_body as pb  # noqa: E402
from comic_paint import CORE_MUL, HALF_MUL, INK, SKIN, THROAT, luminance, mouth_cavity, underwear_mask  # noqa: E402

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
    crotch = deepest(contour, foot_r, foot_l)
    # The armpit is the deepest point from the hand-to-HIP line, not hand-to-foot: against the
    # long slanted hand-to-foot line the bulge of the lat can be deeper than the armpit corner
    # (Male_Back.png: one armpit landed 58 px down the lat, which skewed the TPS on that side
    # and lifted the boxers' waistband 4 cm on one hip). The hip is the widest point of the
    # torso side above the crotch, found from the first estimate.
    n = len(contour)

    def widest(i0, i1, sign):
        idx = np.arange(i0, i1 + (n if i1 < i0 else 0)) % n
        idx = idx[y[idx] < y[crotch]]
        return int(idx[np.argmax(sign * x[idx])]) if len(idx) else i1

    armpit_r = deepest(contour, hand_r, widest(deepest(contour, hand_r, foot_r), foot_r, 1))
    armpit_l = deepest(contour, widest(foot_l, deepest(contour, foot_l, hand_l), -1), hand_l)
    return [head, deepest(contour, head, hand_r), hand_r, armpit_r, foot_r,
            crotch, foot_l, armpit_l, hand_l, deepest(contour, hand_l, head)]


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


def strip_outline(img, mask, ring=None, reach=None):
    """The drawing without its silhouette outline ink, inpainted from the skin just inside.

    The engine draws the silhouette (Sobel post-process), so the drawing's outline must not be in
    the texture: projected onto the grazing surfaces along the silhouette (shoulder tops, upper
    edges of the arms, sides of the legs) and extended outwards by the dilation below, it became
    wide black bands. Outline = ink (lum < 0.3) in the `ring` px band inside the silhouette plus
    every dark pixel connected to it through dark pixels within `reach` px: that also takes the
    solid black shading masses the drawing hangs off the outline (the trapezius / shoulder tops
    are a 14 px black band in Male_Front.png), while interior lines separated from the outline by
    skin stay. The removed pixels are refilled from the nearest remaining skin pixel.
    """
    ring = OUTLINE_RING if ring is None else ring
    reach = OUTLINE_REACH if reach is None else reach
    lum = img @ np.array([0.3, 0.59, 0.11], np.float32)
    sat = img.max(axis=2) - img.min(axis=2)
    band = mask & ~morph(mask, ring, False)
    # The grey underwear is darker than lum 0.30 too, and its hem / waistband ink touches the
    # silhouette: without this the removal ate 40 px into the garment from each side and refilled
    # it from the nearest skin (grey blocks on the front boxers, a skin-coloured swirl on the back).
    # Everything inside the garment (plus 5 px for its seam lines) stays, except the silhouette ring.
    dark = (lum < 0.30) & mask & ~(morph(cloth_region(img, mask), 5, True) & ~band)
    cur = dark & band
    for _ in range(reach):
        grown = cur.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            grown |= np.roll(cur, (dy, dx), axis=(0, 1))
        grown &= dark
        if (grown == cur).all():
            break
        cur = grown
    # The drawing also has a 2-3 px pale rim light along the silhouette (inside the outline):
    # anything in the band that is not plain skin (bright or unsaturated) goes too.
    rim = band & ((lum > 0.70) | (sat < 0.10))
    outline = morph(cur | rim, 1, True) & mask
    return pb.dilate(img, mask & ~outline, ring + reach + 2) * mask[:, :, None]


def label_blobs(mask):
    """4-connected components of `mask`: (label image, 1-based, 0 = background; sizes in px)."""
    h, w = mask.shape
    label = np.zeros(mask.shape, np.int32)
    sizes = []
    for y, x in zip(*np.nonzero(mask)):
        if label[y, x]:
            continue
        k = len(sizes) + 1
        label[y, x] = k
        q = collections.deque([(y, x)])
        count = 0
        while q:
            cy, cx = q.popleft()
            count += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not label[ny, nx]:
                    label[ny, nx] = k
                    q.append((ny, nx))
        sizes.append(count)
    return label, sizes


def keep_blobs(mask, n, minimum=2000):
    """The `n` largest 4-connected blobs of `mask` (at least `minimum` px each). With n == 1 this
    is largest_blob(): the blob under the median pixel, which is the garment on the one-garment
    drawings (the Male's boxers); with two garments (the Female's bra and briefs) the median
    pixel can fall between them, so the components are labelled and sized instead."""
    if n == 1:
        return largest_blob(mask)
    label, sizes = label_blobs(mask)
    order = np.argsort(sizes)[::-1][:n]
    return np.isin(label, [int(i) + 1 for i in order if sizes[i] >= minimum])


def garment_pieces(region):
    """The separate garments of a cloth region (one array per blob), largest first."""
    label, sizes = label_blobs(region)
    return [label == int(i) + 1 for i in np.argsort(sizes)[::-1]]


def cloth_region(img, mask):
    """The grey garment(s) (underwear) of a body drawing: the big neutral-grey blob(s), holes
    closed. N_GARMENTS says how many there are (the Female wears a sports bra and briefs)."""
    lum = img @ np.array([0.3, 0.59, 0.11], np.float32)
    sat = img.max(axis=2) - img.min(axis=2)
    grey = (sat < 0.10) & (lum > 0.12) & (lum < 0.50) & mask
    region = morph(morph(grey, 8, True), 8, False)          # close the ink lines and hatching inside
    region = morph(morph(region, 4, False), 4, True)        # drop thin grey slivers (shaded skin edges)
    # Only where the pixels really are grey: the anti-aliased edges on both sides of a black ink
    # band (the shoulder tops) closed into a "grey" strip too, which then protected the band from
    # the outline strip. The garment is grey nearly everywhere; a black band is grey only at its
    # edges. Holes the test leaves in the garment's hatched parts are closed again.
    region &= blur_f(grey.astype(np.float32), 5.0) > 0.4
    region = morph(morph(region, 8, True), 8, False) & mask
    if region.sum() < 2000:
        return np.zeros_like(mask)
    return keep_blobs(region, N_GARMENTS)                   # the garment(s), no stray patches


def clean_cloth(img, mask):
    """The garment of a body drawing reduced to its base cloth tone plus its seam lines.

    The drawings shade the boxers with hatched masses, dark blocks and a mottled swirl on the
    buttocks, which projected onto the model as patches. Only the low-frequency tone is replaced
    (one flat grey, the median of the garment); real ink stays: thin dark lines (the waistband,
    hems, the crotch and buttock seams), not the hatched masses (ink density over 35 % in a
    13 px window) and not the broad shading. The engine's own shading does the rest."""
    region = cloth_region(img, mask)
    if not region.any():
        return img
    lum = img @ np.array([0.3, 0.59, 0.11], np.float32)
    inner = region & (lum > 0.12)
    base_lum = float(np.median(lum[inner]))
    base = np.median(img[inner & (np.abs(lum - base_lum) < 0.04)], axis=0).astype(np.float32)
    flat = np.broadcast_to(base, img.shape).copy()
    ink_level = np.full(img.shape[:2], 0.62 * base_lum, np.float32)
    if N_GARMENTS > 1:
        # Each garment gets its own flat grey (the bra and the briefs are drawn at slightly
        # different levels) and its own ink threshold.
        for piece in garment_pieces(region):
            p_inner = piece & inner
            if p_inner.sum() < 500:
                continue
            p_lum = float(np.median(lum[p_inner]))
            flat[piece] = np.median(img[p_inner & (np.abs(lum - p_lum) < 0.04)], axis=0).astype(np.float32)
            ink_level[piece] = 0.62 * p_lum
            print("  garment piece: %d px, base lum %.3f" % (piece.sum(), p_lum))
    ink = region & (lum < ink_level)
    thin = ink & ~morph(morph(ink, 3, False), 3, True)      # lines up to ~6 px, not masses
    hatch = blur_f(ink.astype(np.float32), 6.0) > 0.35
    keep = thin & ~morph(hatch, 2, True)
    keep_w = np.clip(blur_f(keep.astype(np.float32), 0.7) * 1.5, 0, 1)
    cloth = flat * (1 - keep_w)[:, :, None] + np.minimum(img, flat) * keep_w[:, :, None]
    region_w = blur_f(region.astype(np.float32), 1.0)
    out = img * (1 - region_w)[:, :, None] + cloth * region_w[:, :, None]
    print("  cloth: %d px, base tone %s (lum %.3f), ink kept %d px of %d" % (region.sum(), np.round(base, 3), base_lum, keep.sum(), ink.sum()))
    return np.clip(out, 0, 1).astype(np.float32)


def body_source(img, mask):
    """A body drawing prepared for sampling: outline ink removed, colours extended 60 px past the
    silhouette (texels at grazing angles map a little outside it), and the EDGE_BAND px just inside
    the silhouette blurred. Texels on the grazing surfaces along the model's silhouette (tops of the
    shoulders and arms, sides of the legs) all map into the drawing's last few edge pixels, so any
    line or highlight there is stretched into streaks across 2-3 cm of skin; the engine draws the
    outline on those surfaces anyway, so the band is better plain."""
    src = pb.dilate(clean_cloth(strip_outline(img, mask), mask), mask, 60)
    inner = morph(mask, EDGE_BAND, False)
    return np.where(inner[:, :, None], src, blur_f(src, EDGE_BLUR))


def project_view(mesh_obj, P, N, ref_img, mirror_x):
    """Colour every texel from one drawing. Returns (colour, weight) with weight 0 where unseen."""
    ref_mask = drawing_mask(ref_img)
    ref_src = body_source(ref_img, ref_mask)

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
    # A texel just past the silhouette of this view (self-occluded by a few cm) still samples the
    # drawing's plain edge band, so it may blend; one hidden behind another part (inner thigh,
    # armpit, under the chin: several cm deeper) may not. The front and back then cross over
    # smoothly across the sides (full weight 17 deg before the silhouette, zero 17 deg past it)
    # instead of meeting at a hard visibility cut.
    visible = pb.smoothstep(SIDE_GATE, 0.012, nearest[cell] - depth)
    facing = pb.smoothstep(-SIDE_BLEND, SIDE_BLEND, N[:, 1] * depth_sign)
    return colour, facing * visible


def soft_copy(img, mask):
    """The drawing with its ink lines dissolved: for filling parts no drawing covers."""
    src = body_source(img, mask)
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
                  # chin_bottom = where the chin meets the neck (the beard's lower edge), NOT lower
                  # down: at y=695 the drawing is already the shadowed neck, which then got
                  # stretched over the chin and darkened it. neck_* sit 5 cm below the chin.
                  "corner_l": (285, 482), "corner_r": (172, 482), "chin_bottom": (228, 626),
                  "ear_l": (430, 355), "ear_r": (28, 355), "hairline": (228, 195), "head_top": (228, 60),
                  "temple_l": (375, 235), "temple_r": (80, 235), "neck_l": (335, 692), "neck_r": (121, 692),
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
            # The drawing's neck pins sit on its silhouette, so these are the mesh's own front and
            # back neck surface at that height (measured in Landmarks, not offsets from the chin).
            "neck_front": (lm.neck_front, lm.chin[2] - 0.055), "neck_back": (lm.neck_back, lm.chin[2] - 0.055)}


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


def project_head_view(P, N, img, lm, head_pts, body_name, view, eyes, layers=None):
    """Project a face drawing onto the head using landmark correspondences.
    Returns (colour, weight, {layer: value}) with `layers` (drawing-space masks) sampled the same way.

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
        # 3D distance to the eyeball centre (a (y, z)-only distance was a cylinder along x that also
        # suppressed the cheekbone and temple at the eye's height, leaving a hole in the coverage).
        eye_c = np.where((P[:, 0] > 0)[:, None], lm.eye_l, lm.eye_r)
        d_eye = np.linalg.norm(P - eye_c, axis=1)
        facing *= 0.3 + 0.7 * pb.smoothstep(0.022, 0.038, d_eye)
        # ... and the mouth: the profile's lips (kept un-stripped, with their outline) would land
        # beside the mouth corners as a pale ringed blob.
        mouth_z = 0.5 * (lm.lips_top + lm.lips_bottom)
        d_corner = np.hypot(np.abs(P[:, 0]) - lm.mouth_half_width, P[:, 2] - mouth_z)
        facing *= pb.smoothstep(0.02, 0.045, d_corner)
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
    sampled = {k: sample1(v, uv[:, 0], uv[:, 1]) * inside for k, v in (layers or {}).items()}
    return colour, facing * visible * inside * below_chin, sampled


def rgb_to_hsv(c):
    """(N, 3) sRGB 0..1 -> hue 0..1, saturation, value."""
    mx, mn = c.max(axis=1), c.min(axis=1)
    d = mx - mn
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)
    h = np.zeros(len(c), np.float32)
    nz = d > 1e-6
    r, g, b = c[:, 0], c[:, 1], c[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        hr = ((g - b) / d) % 6
        hg = (b - r) / d + 2
        hb = (r - g) / d + 4
    h = np.where(mx == r, hr, np.where(mx == g, hg, hb)) / 6.0
    h = np.where(nz, h, 0.0) % 1.0
    return h.astype(np.float32), s.astype(np.float32), mx.astype(np.float32)


def hsv_to_rgb(h, s, v):
    i = np.floor(h * 6.0)
    f = h * 6.0 - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    i = i.astype(int) % 6
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], 1).astype(np.float32)


def grade_image(img, contrast=1.0):
    """Grade a drawing so its lit skin tone becomes the palette's SKIN, keeping its own shading.

    The transfer is done in HSV as ratios to the drawing's lit skin: hue is shifted by the
    difference between the palette and the drawing, saturation and value are scaled. So a
    highlight the drawing paints toward white stays a warm pale skin (a per-channel gain turned
    the orange drawing's highlights grey-pink, because its weak blue channel got the largest
    gain), and shadows keep the skin hue instead of going grey.

    `contrast` > 1 deepens the drawing's own shading (luminance power curve about the lit tone).
    Only skin-coloured pixels are re-tinted; grey cloth, ink and eyes keep their colours."""
    flat = img.reshape(-1, 3)
    lum = luminance(flat)
    sat = flat.max(axis=1) - flat.min(axis=1)
    skin_px = (lum > 0.3) & (sat > 0.12) & (flat[:, 0] > flat[:, 2])
    if skin_px.sum() > 100:
        bright = flat[skin_px][lum[skin_px] >= np.percentile(lum[skin_px], 70)]
        lit = np.median(bright, axis=0)
    else:
        lit = SKIN
    h, s, v = rgb_to_hsv(flat)
    hl, sl, vl = (float(x[0]) for x in rgb_to_hsv(lit[None].astype(np.float32)))
    hs, ss, vs = (float(x[0]) for x in rgb_to_hsv(SKIN[None].astype(np.float32)))
    graded = hsv_to_rgb((h + (hs - hl)) % 1.0, np.clip(s * (ss / max(sl, 1e-3)), 0, 1), np.clip(v * (vs / max(vl, 1e-3)), 0, 1))
    if contrast != 1.0:
        lit_lum = float(luminance(SKIN[None])[0])
        g_lum = luminance(graded)[:, None]
        graded = graded * (np.power(np.maximum(g_lum, 1e-4) / lit_lum, contrast - 1.0))
    skinness = pb.smoothstep(0.06, 0.16, sat) * pb.smoothstep(0.0, 0.08, flat[:, 0] - flat[:, 2])
    out = flat * (1 - skinness[:, None]) + graded * skinness[:, None]
    return np.clip(out, 0, 1).reshape(img.shape).astype(np.float32)


def shadow_bands(colour, shadow, no_core=None):
    """The painted half-tone (x0.72) and core (x0.55) bands from the long-range occlusion.
    `no_core` (0..1 per texel) keeps the core band out of a region (under the chin, which should
    read as one soft band of at most x0.72, not a cut)."""
    half = pb.smoothstep(0.14, 0.22, shadow)[:, None]
    core = pb.smoothstep(0.32, 0.40, shadow)[:, None]
    if no_core is not None:
        core = core * (1.0 - no_core)[:, None]
    mul = (1.0 + (HALF_MUL - 1.0) * half) * (1.0 + (CORE_MUL / HALF_MUL - 1.0) * core)
    return np.clip(colour * mul, 0.0, 1.0)


# Regions (pixel polygons) of hair, beard and brows in each face drawing. Anything inside is
# replaced with the drawing's clean skin tone so the base head stays plain; hair is a mesh,
# beard/brows are layers. In the full-body drawings only the head is stripped (the neck is used).
# Regions inside or next to the strip areas that must be kept: the lips sit inside the beard area
# (the lip outlines themselves, not the moustache or the skin around them) and the nostril ink sits
# right above the moustache. Feathered edge.
KEEP_REGIONS = {
    "Male_FaceFront": [[(172, 482), (185, 474), (205, 468), (220, 470), (228, 472), (236, 470), (251, 468),
                        (271, 474), (285, 482), (271, 492), (251, 499), (228, 501), (205, 499), (185, 492)],
                       [(180, 392), (276, 392), (276, 424), (180, 424)]],                        # nostrils
    "Male_FaceSide": [[(66, 401), (47, 401), (40, 407), (38, 414), (44, 418), (40, 426), (45, 436), (62, 441), (74, 437), (70, 418)],
                      [(0, 328), (72, 328), (72, 366), (0, 366)]],                              # nostril
}

# The face drawings' regions are named, because the beard and brows are also cut out of the
# drawings as swappable layers (T_Body_<Body>_Beard.png / _Brows.png, see layer_masks()).
FACE_REGIONS = {
    "Male_FaceFront": {
        "hair": [[(0, 0), (445, 0), (445, 330), (400, 262), (350, 215), (300, 200), (228, 192), (160, 200),
                  (100, 215), (45, 262), (0, 330)]],
        # The dense beard mass (moustache, cheeks, chin); the neck hatching below it is skin shading.
        "beard": [[(62, 385), (100, 398), (150, 422), (185, 432), (228, 428), (271, 432), (306, 422), (356, 398),
                   (394, 385), (395, 470), (385, 560), (356, 600), (300, 620), (228, 630), (156, 620), (100, 600),
                   (71, 560), (60, 470)]],
        "brows": [[(84, 232), (218, 244), (218, 292), (84, 292)], [(240, 244), (376, 232), (376, 292), (240, 292)]],
    },
    "Male_FaceSide": {
        "hair": [[(0, 0), (466, 0), (466, 330), (445, 420), (400, 470), (300, 400), (250, 380), (235, 320),
                  (200, 300), (170, 255), (152, 210), (135, 185), (100, 165), (55, 180), (0, 230)]],  # + sideburn
        "beard": [[(10, 368), (60, 372), (100, 368), (150, 350), (200, 330), (240, 320), (262, 340), (265, 380),
                   (255, 450), (235, 485), (195, 510), (150, 530), (90, 535), (45, 520), (25, 495), (10, 440)]],
        "brows": [[(40, 214), (140, 226), (140, 256), (40, 250)]],
    },
}

STRIP_REGIONS = {name: [poly for polys in regions.values() for poly in polys] for name, regions in FACE_REGIONS.items()}
STRIP_REGIONS["Male_Back"] = [[(225, 0), (375, 0), (375, 112), (225, 112)]]        # back of the hair, down the nape (the trapezius below y=112 is drawn skin)
STRIP_REGIONS["Male_Front"] = [[(440, 0), (590, 0), (590, 152), (440, 152)]]       # whole head (hair, beard)
# Drawings whose dark masses are found automatically on top of the regions (the face close-ups);
# on the full-body drawings that would also catch armpit shadows and nipples.
AUTO_STRIP = {"Male_FaceFront", "Male_FaceSide", "Male_Back"}
# Pixel polygons painted black (background) before anything else: stray fragments of another
# figure at the edge of a drawing (Female_Back.png has a strand of hair of the next sheet
# cell at its left edge; it is not part of the silhouette and must not be auto-stripped).
BLACKOUT = {}
# Drawings whose auto-strip masses only count within this many px of their strip polygons
# (the full-body drawings always use 20 px).
AUTO_STRIP_MARGIN = {}
# Regions of a face drawing whose dark hatching becomes a soft warm shadow instead of ink
# (strip_hair): the Female's dense hatch under the lower lip projected as a black smear.
SOFTEN_REGIONS = {}
SOFTEN_MUL = 0.74  # the shadow's luminance relative to the clean skin fill (about the half-tone)
# Bodies whose garments wrap around the torso (the Female's bra band and briefs): the texels on
# the sides of the torso, which neither body drawing sees squarely (|N.y| below this), take the
# colour of the nearest squarely seen texel in 3D instead of the drawings' blurred edge pixels,
# which mixed skin and grey into pale streaks across the cloth. The Male's boxers were accepted
# as projected, so he is not listed.
CLOTH_SIDE_FILL = {"Female": 0.45}
# Top of the briefs zone above the pelvis bone (the Female's briefs are cut high on the hips).
BRIEFS_ZONE_TOP = {"Male": 0.12, "Female": 0.15}
# Garments in the body drawings: the Male wears boxers, the Female a sports bra and briefs
# (two grey regions for cloth_region / clean_cloth; N_GARMENTS is set from this in main()).
GARMENTS = {"Male": 1, "Female": 2}
N_GARMENTS = 1
# The lit-skin reference for the body tone (even_skin, head -> body gain): a front-facing band
# of plain skin, in metres of height. Male: the upper chest; Female: the belly between the bra
# and the briefs (her upper chest is bra and collarbone hatching).
CHEST_BAND = {"Male": (1.25, 1.45), "Female": (0.95, 1.07)}


def load_body_tables(body_name):
    """Fill the tables above for a body from Reference/<Body>_landmarks.json (the Female; the
    Male's tables are inline). The JSON holds the measured pixel landmarks (`front`, `side`), the
    eye openings, the face regions (hair / beard / brows, for the strip and the layers), the keep
    and strip polygons per drawing, the auto-strip list and optional blackout polygons. Landmark
    names the model side does not define (the iris centres, the drawn hairline) are reference
    only and ignored by project_head_view()."""
    path = os.path.join(REF_DIR, f"{body_name}_landmarks.json")
    if not os.path.exists(path):
        return
    with open(path) as f:
        data = json.load(f)

    def polys(ps):
        return [[tuple(pt) for pt in poly] for poly in ps]

    FACE_LANDMARKS[body_name] = {view: {k: tuple(v) for k, v in data[view].items()} for view in ("front", "side")}
    eo = data["eye_openings"]
    EYE_OPENINGS[body_name] = {"front": [tuple(p) for p in eo["front"]], "front_mirror_x": eo["front_mirror_x"],
                               "side": [tuple(p) for p in eo["side"]]}
    for name, regions in data.get("face_regions", {}).items():
        FACE_REGIONS[name] = {layer: polys(ps) for layer, ps in regions.items()}
    for name, ps in data.get("keep", {}).items():
        KEEP_REGIONS[name] = polys(ps)
    for name, ps in data.get("strip", {}).items():
        STRIP_REGIONS[name] = polys(ps)
    AUTO_STRIP.update(data.get("auto_strip", []))
    for name, ps in data.get("blackout", {}).items():
        BLACKOUT[name] = polys(ps)
    AUTO_STRIP_MARGIN.update(data.get("auto_strip_margin", {}))
    for name, ps in data.get("soften", {}).items():
        SOFTEN_REGIONS[name] = polys(ps)
    print("  loaded tables for %s from %s" % (body_name, os.path.relpath(path, REPO_ROOT)))


def skin_fill(img, body, mass, face=False):
    """An image of this drawing's clean skin tone with a hint of grain, to paint over `mass`.

    Every pixel takes the colour of the nearest clean skin pixel (lit, saturated, at least 10 px
    from any stripped area and 4 px from ink, so the greyish stubble next to the beard, the
    shadow under the hair line and the shadowed neck are never used), smoothed over ~20 px. Filled areas therefore
    continue the shading of the surrounding skin instead of showing a flat patch.
    """
    lum = img @ np.array([0.3, 0.59, 0.11], np.float32)
    sat = img.max(axis=2) - img.min(axis=2)
    clean = body & ~morph(mass, 10, True) & (lum > 0.3) & (sat > 0.12) & (img[:, :, 0] > img[:, :, 2])
    clean &= morph(lum > 0.3, 4, False)  # not next to ink lines
    if face and mass.any():
        # Face close-ups: nothing below the beard's lower edge (the neck) is a source, or the chin,
        # whose nearest clean skin is the neck 20 px below rather than the cheeks 150 px away, takes
        # the neck's shadow tone. The full-body drawings do fill their head from the neck.
        clean[np.nonzero(mass.any(axis=1))[0].max():] = False
    if clean.sum() > 100:
        clean &= lum >= np.percentile(lum[clean], 35)  # lit skin only, not the shaded parts
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
        margin = AUTO_STRIP_MARGIN.get(name, None if "_Face" in name else 20)
        if margin is not None and name in STRIP_REGIONS:
            # Full-body drawing: only the masses at the hair region count. The dark crotch of the
            # boxers in Male_Back.png was taken for a hair mass and painted over with a round
            # skin fill, which landed on the model's buttocks as a skin-coloured swirl. (Face
            # drawings may ask for the same with AUTO_STRIP_MARGIN: the Female's under-chin
            # hatching was a "mass" too.)
            mass &= morph(polygon_mask(body.shape, STRIP_REGIONS[name]), margin, True)
    if name in STRIP_REGIONS:
        mass |= polygon_mask(body.shape, STRIP_REGIONS[name])
    mass = morph(mass, 6, True) & body                          # take a margin of the lighter strokes too
    fill = skin_fill(img, body, mass, face="_Face" in (name or ""))
    # Stray strokes around the masses (hairline wisps on the forehead, stubble next to the beard,
    # the sideburn's tail): anything in a band around the mass that differs from the clean skin
    # fill is a stroke, not skin, so it goes too. Thin lines inside the band are not important
    # ink (the lid lines and lips are protected by `keep` below).
    band = morph(mass, SCRATCH_BAND, True) & body & ~mass
    fill_lum = fill @ np.array([0.3, 0.59, 0.11], np.float32)
    scratch = band & (np.abs(lum - fill_lum) > SCRATCH_DEV)
    scratch = morph(scratch, 2, True) & band
    soft = np.maximum(blur_f(mass.astype(np.float32), 2.0), blur_f(scratch.astype(np.float32), 1.0)) * body
    keep = np.zeros(body.shape, bool)
    if name in KEEP_REGIONS:
        keep |= polygon_mask(body.shape, KEEP_REGIONS[name])
    body_name, _, view = (name or "").partition("_Face")  # "Male_FaceFront" -> "Male", "Front"
    if view and body_name in EYE_OPENINGS:
        # The lid ink hugs the eye opening and the brows sit right above it: keep a 7 px band
        # around the opening so stripping the brows cannot eat the lash line.
        _, eye_open, _ = drawing_eye_pins(img.shape, body_name, view.lower())
        keep |= morph(eye_open, 7, True)
    keep_w = blur_f(keep.astype(np.float32), 1.5) if keep.any() else None
    if keep_w is not None:
        soft *= 1 - keep_w
    out = img * (1 - soft[:, :, None]) + fill * soft[:, :, None]
    if name in SOFTEN_REGIONS:
        # Hatching -> soft shadow: the dark strokes take the shadow tone, then the region is
        # blurred so the strokes merge into one warm half-tone; feathered edge, lips kept.
        region = polygon_mask(body.shape, SOFTEN_REGIONS[name]) & body
        sw = blur_f(region.astype(np.float32), 2.0) * body
        if keep_w is not None:
            sw *= 1 - keep_w
        out_lum = out @ np.array([0.3, 0.59, 0.11], np.float32)
        shaded = np.where((out_lum < 0.45)[:, :, None], fill * SOFTEN_MUL, out)
        shaded = blur_f(shaded, 3.0)
        out = out * (1 - sw[:, :, None]) + shaded * sw[:, :, None]
    return np.clip(out, 0, 1).astype(np.float32), soft


SCRATCH_BAND = 14   # px around a stripped mass that is checked for stray strokes
SCRATCH_DEV = 0.07  # luminance difference from the clean skin fill that counts as a stroke


def strengthen_ink(img, body, gamma=2.0, grow=1):
    """Darken and widen the drawing's ink so it survives projection (levels on the dark pixels).

    The face drawings' lines are 1-2 px (about 1 mm on the head); projected and filtered they come
    out as faint brown scratches. The Borderlands look wants the lid wedge, nostrils, mouth line
    and nasolabial fold to read as heavy ink, so everything below luminance INK_LEVEL gets a
    power curve (`gamma`) that pushes it towards black, the darkest pixels settle on the palette's
    warm black INK (never pure black), and the result is grown by `grow` px. The mid-tone hatching
    on the nose sides and cheeks (lum > INK_LEVEL) is shading and stays a soft brown. No new lines
    are invented: only what the drawing already has gets heavier.
    """
    lum = img @ np.array([0.3, 0.59, 0.11], np.float32)
    ink_lum = float(luminance(INK[None])[0])
    target = INK_LEVEL * np.power(np.clip(lum, 1e-4, INK_LEVEL) / INK_LEVEL, gamma)
    target = np.maximum(target, ink_lum)
    f = np.where((lum < INK_LEVEL) & body, target / np.maximum(lum, 1e-4), 1.0).astype(np.float32)
    f = np.minimum(f, 1.0)
    for _ in range(grow):  # grow the darkening by a pixel each side (4-neighbour minimum)
        g = f.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            g = np.minimum(g, np.roll(f, (dy, dx), axis=(0, 1)))
        f = np.where(body, g * FACE_INK_RING + f * (1 - FACE_INK_RING), f)
    f = np.minimum(f, blur_f(f, 0.6))
    out = img * f[:, :, None]
    # Pixels that went very dark take the warm black's hue instead of whatever the darkened pixel had.
    new_lum = lum * f
    warm = pb.smoothstep(0.16, 0.09, new_lum) * body
    out = out * (1 - warm)[:, :, None] + INK * (np.maximum(new_lum, ink_lum) / ink_lum)[:, :, None] * warm[:, :, None]
    return np.clip(out, 0, 1).astype(np.float32)


def layer_masks(img, name):
    """Drawing-space masks (0..1) of the beard and brows: the drawing's hair strokes inside the
    FACE_REGIONS polygons, minus the lips and the lid band (which belong to the skin).

    The mask carries the drawing's own stroke texture (how dark each pixel is), so the projected
    layer looks drawn rather than like a flat blob: solid where the beard is dense, broken strokes
    along its edges and in the stubble.
    """
    if name not in FACE_REGIONS:
        return {}
    lum = img @ np.array([0.3, 0.59, 0.11], np.float32)
    body = drawing_mask(img)
    strokes = pb.smoothstep(0.50, 0.22, lum) * body
    keep = np.zeros(body.shape, bool)
    if name in KEEP_REGIONS:
        keep |= polygon_mask(body.shape, KEEP_REGIONS[name])
    body_name, _, view = name.partition("_Face")
    if body_name in EYE_OPENINGS:
        _, eye_open, _ = drawing_eye_pins(img.shape, body_name, view.lower())
        keep |= morph(eye_open, 7, True)
    keep_soft = blur_f(keep.astype(np.float32), 1.5)
    out = {}
    for layer in ("beard", "brows"):
        region = blur_f(polygon_mask(body.shape, FACE_REGIONS[name][layer]).astype(np.float32), 3.0)
        out[layer] = np.clip(region * strokes * (1 - keep_soft), 0, 1).astype(np.float32)
        if layer == "beard":  # the stripped zone itself (no stroke texture), see even_tone() in main
            out["beard_region"] = np.clip(region * (1 - keep_soft), 0, 1).astype(np.float32)
    return out


def load_ref(name, plain_head=False, contrast=1.0, ink=0.0):
    path = os.path.join(REF_DIR, name + ".png")
    if not os.path.exists(path):
        return None
    img = np.asarray(Image.open(path).convert("RGB")).astype(np.float32) / 255
    if name in BLACKOUT:
        img[polygon_mask(img.shape[:2], BLACKOUT[name])] = 0.0
    img = grade_image(img, contrast)
    if plain_head:
        img, mass = strip_hair(img, name)
        Image.fromarray((mass * 255).astype(np.uint8)).save(os.path.join(REF_DIR, name + "_HairMask.png"))
    if ink > 0:
        img = strengthen_ink(img, drawing_mask(img), ink, FACE_INK_GROW)
    return img


def load_layers(name):
    """Beard/brow masks of a face drawing (see layer_masks), or {} without the drawing."""
    path = os.path.join(REF_DIR, name + ".png")
    if not os.path.exists(path):
        return {}
    return layer_masks(grade_image(np.asarray(Image.open(path).convert("RGB")).astype(np.float32) / 255), name)


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


def skin_tone_stats(colour, sel):
    """Median HSV of the lit skin (upper half by luminance of the skin-coloured texels in `sel`),
    and the luminance ratio of the shaded half to it. None with too few skin texels."""
    c = colour[sel]
    lum = luminance(c)
    sat = c.max(axis=1) - c.min(axis=1)
    skin = (lum > 0.3) & (sat > 0.12) & (c[:, 0] > c[:, 2])
    if skin.sum() < 2000:
        return None
    s, l = c[skin], lum[skin]
    split = np.median(l)
    lit, shade = s[l >= split], s[l < split]
    h, sa, v = (float(np.median(x)) for x in rgb_to_hsv(lit))
    lit_lum = float(np.median(luminance(lit)))
    return h, sa, v, lit_lum, float(np.median(luminance(shade))) / lit_lum


def match_skin_tone(colour, sel, ref_colour, ref_sel):
    """HSV transfer of the skin in `colour` so its lit skin (median hue / saturation / value of the
    lit half of the skin texels in `sel`) equals that of `ref_colour[ref_sel]`. Only skin-coloured
    pixels change (skinness weight as in grade_image): cloth, ink and eyes keep their colours.
    The shading depth is not matched: the front's shade half is its few deep shadows, the back's
    is its all-over hatching, and matching the ratio only deepened the hatching.

    The back drawing is graded like the front one, but its lit skin is measured on a drawing that
    is hatched almost everywhere, so after projection the back texels came out darker and greyer
    than the front's; this matches them on the projected texels themselves."""
    st, ref = skin_tone_stats(colour, sel), skin_tone_stats(ref_colour, ref_sel)
    if st is None or ref is None:
        return colour
    h, s, v = rgb_to_hsv(colour)
    graded = hsv_to_rgb((h + (ref[0] - st[0])) % 1.0, np.clip(s * ref[1] / max(st[1], 1e-3), 0, 1),
                        np.clip(v * ref[2] / max(st[2], 1e-3), 0, 1))
    sat = colour.max(axis=1) - colour.min(axis=1)
    skinness = pb.smoothstep(0.06, 0.16, sat) * pb.smoothstep(0.0, 0.08, colour[:, 0] - colour[:, 2])
    print("  skin tone match: H %.3f->%.3f S %.3f->%.3f V %.3f->%.3f (shade ratio %.3f vs %.3f, left alone)" % (
        st[0], ref[0], st[1], ref[1], st[2], ref[2], st[4], ref[4]))
    return np.clip(colour * (1 - skinness[:, None]) + graded * skinness[:, None], 0, 1).astype(np.float32)


def even_cloth(colour, P, sel, cell=0.01):
    """Flatten the low-frequency tone of the garment texels `sel` to one grey (their median),
    keeping the ink (texels well below the median) and the grain. Front and back cloth then meet
    at the sides at one tone, and any left-over shading patch of the drawings goes."""
    lum = luminance(colour)
    base = float(np.median(lum[sel]))
    ink_w = pb.smoothstep(0.75 * base, 0.55 * base, lum)
    mean = np.ones_like(lum)
    mean[sel] = local_mean(P, sel, np.where(ink_w < 0.5, lum, base))
    gain = np.clip(base / np.maximum(mean, 1e-3), 0.7, 1.4) * (1 - ink_w) + ink_w
    print("  cloth texels %d, base lum %.3f, local mean p10/p90 %.3f/%.3f" % (sel.sum(), base, *np.percentile(mean[sel], [10, 90])))
    out = colour.copy()
    out[sel] = np.clip(colour[sel] * gain[sel, None], 0, 1)
    return out


def even_skin(colour, P, N, head_w, lm, strength, cell=0.01, chest_band=(1.25, 1.45)):
    """Even the low-frequency tone of the body skin towards the front chest's lit tone.

    The drawings shade the sides of the torso and limbs darker and hatch the back densely, so
    the projected skin carried dark bands along the sides (where the front and back drawings
    meet) and a browner back. The local mean luminance (3 x `cell` m window, ink left out) is
    pulled towards the chest's lit median by `strength` (a power: 1 = fully flat), lifting the
    dark zones; the ink and the hatching strokes keep their contrast to the skin around them.
    The head and the graded neck zone are left alone."""
    lum = luminance(colour)
    sat = colour.max(axis=1) - colour.min(axis=1)
    skin = (head_w < 0.1) & (P[:, 2] < lm.chin[2] - NECK_FADE) & (lum > 0.3) & (sat > 0.12) & (colour[:, 0] > colour[:, 2])
    skin &= np.abs(P).sum(axis=1) > 0  # not the off-island texels, which the bake leaves at the origin
    chest = skin & (-N[:, 1] > 0.6) & (np.abs(P[:, 0]) < 0.15) & (P[:, 2] > chest_band[0]) & (P[:, 2] < chest_band[1])
    if chest.sum() < 500 or skin.sum() < 5000:
        return colour
    ref = float(np.median(lum[chest][lum[chest] >= np.median(lum[chest])]))
    mean = np.full_like(lum, ref)
    mean[skin] = local_mean(P, skin, np.where(lum > 0.35, lum, ref), cell)
    gain = np.clip(np.power(ref / np.maximum(mean, 1e-3), strength), 0.9, 1.35)
    ink_w = pb.smoothstep(0.35, 0.22, lum)
    gain = gain * (1 - ink_w) + ink_w * (0.5 + 0.5 * gain)  # ink lifts half as much: stays dark against lifted skin
    print("  skin evening: ref %.3f, local mean p10/p50/p90 %.3f/%.3f/%.3f -> gain p10/p90 %.2f/%.2f" % (
        ref, *np.percentile(mean[skin], [10, 50, 90]), *np.percentile(gain[skin], [10, 90])))
    out = colour.copy()
    out[skin] = np.clip(colour[skin] * gain[skin, None], 0, 1)
    return out


def fill_from_nearest(P, fill, src, colour, cell=0.002, steps=80):
    """Colour for the `fill` texels: that of the nearest `src` texel in 3D (voxel-grid dilation,
    `cell` metres per voxel, up to `steps` voxels away; further texels keep NaN -> caller's own)."""
    pts = P[fill | src]
    lo = pts.min(axis=0) - 2 * cell
    dims = np.ceil((pts.max(axis=0) + 2 * cell - lo) / cell).astype(int) + 1
    n = int(np.prod(dims))
    src_i = np.ravel_multi_index(((P[src] - lo) / cell).astype(int).T, dims)
    acc = np.zeros((n, 3), np.float64)
    cnt = np.zeros(n, np.float64)
    np.add.at(acc, src_i, colour[src])
    np.add.at(cnt, src_i, 1)
    have = (cnt > 0).reshape(dims)
    grid = (acc / np.maximum(cnt, 1)[:, None]).reshape(*dims, 3).astype(np.float32)
    fill_i = ((P[fill] - lo) / cell).astype(int)
    for _ in range(steps):
        if have[fill_i[:, 0], fill_i[:, 1], fill_i[:, 2]].all():
            break
        new_have, new_grid = have.copy(), grid.copy()
        for axis in range(3):
            for sh in (1, -1):
                sh_have = np.roll(have, sh, axis=axis)
                take = sh_have & ~new_have
                new_grid[take] = np.roll(grid, sh, axis=axis)[take]
                new_have |= take
        for axis in range(3):  # the border never fills, so np.roll's wrap-around carries nothing
            np.moveaxis(new_have, axis, 0)[[0, -1]] = False
        have, grid = new_have, new_grid
    out = grid[fill_i[:, 0], fill_i[:, 1], fill_i[:, 2]]
    missing = ~have[fill_i[:, 0], fill_i[:, 1], fill_i[:, 2]]
    out[missing] = colour[fill][missing]
    return out


def local_mean(P, sel, values, cell=0.01):
    """Mean of `values` over the 3x3x3 voxel neighbourhood (cell metres) of each `sel` texel."""
    pts = P[sel]
    lo = pts.min(axis=0) - 2 * cell
    dims = np.ceil((pts.max(axis=0) + 2 * cell - lo) / cell).astype(int) + 1
    idx = ((pts - lo) / cell).astype(int)
    flat = np.ravel_multi_index(idx.T, dims)
    acc = np.zeros(int(np.prod(dims)), np.float64)
    cnt = np.zeros(int(np.prod(dims)), np.float64)
    np.add.at(acc, flat, values[sel])
    np.add.at(cnt, flat, 1)
    acc, cnt = acc.reshape(dims), cnt.reshape(dims)
    acc_s, cnt_s = np.zeros_like(acc), np.zeros_like(cnt)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                acc_s += np.roll(acc, (dx, dy, dz), axis=(0, 1, 2))
                cnt_s += np.roll(cnt, (dx, dy, dz), axis=(0, 1, 2))
    mean = acc_s / np.maximum(cnt_s, 1)
    return mean[idx[:, 0], idx[:, 1], idx[:, 2]].astype(np.float32)


def bake_maps(mesh_obj, res):
    """Baked per-texel data of the skin: positions, normals, head weight, long-range AO, plus the
    eyeball positions (for the lid openings)."""
    skin_obj = pb.make_copy(mesh_obj, keep_eyes=False)
    pos = pb.bake(skin_obj, res, pb.geom_socket("Position"))
    nrm = pb.bake(skin_obj, res, pb.geom_socket("Normal"))
    # Head weight = head + jaw bones: the chin and lower lip follow the jaw bone since the rebuild.
    head = pb.bake(skin_obj, res, pb.sum_groups_socket(pb.HEAD_GROUPS))
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


def garment_zones(lm, body_name):
    """Height windows (z0, z1) in which grey texels are garment: the briefs around the pelvis,
    plus the sports bra around the bust on the Female."""
    pelvis_z = lm.bone_head["pelvis"][2]
    zones = [(pelvis_z - 0.30, pelvis_z + BRIEFS_ZONE_TOP.get(body_name, 0.12))]
    if GARMENTS.get(body_name, 1) > 1 and lm.nipple is not None:
        zones.append((lm.nipple - 0.11, lm.nipple + 0.11))
    return zones


def in_zones(z, zones):
    inside = np.zeros(len(z), bool)
    for z0, z1 in zones:
        inside |= (z > z0) & (z < z1)
    return inside


def main():
    global N_GARMENTS
    body_name = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "Male"
    load_body_tables(body_name)
    N_GARMENTS = GARMENTS.get(body_name, 1)
    chest_band = CHEST_BAND.get(body_name, (1.25, 1.45))
    blend_path = os.path.join(BASE_DIR, f"BaseBody_{body_name}.blend")
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    mesh_obj = bpy.data.objects[f"SK_BaseBody_{body_name}"]
    armature = bpy.data.objects["Armature"]
    lm = pb.Landmarks(mesh_obj, armature)
    # Head = head + jaw bones (the chin and lower lip are weighted to the jaw), like the baked
    # head weight below; with the head group alone the face composite stopped at the mouth line.
    head_idx = {mesh_obj.vertex_groups[g].index for g in pb.HEAD_GROUPS}
    head_pts = np.array([v.co[:] for v in mesh_obj.data.vertices
                         if sum(g.weight for g in v.groups if g.group in head_idx) > 0.5], np.float32)

    front = load_ref(f"{body_name}_Front", plain_head=True)
    back = load_ref(f"{body_name}_Back", plain_head=True)  # plain head too: the nape hair is below the head bone
    face_front = load_ref(f"{body_name}_FaceFront", plain_head=True, contrast=1.15, ink=FACE_INK)
    face_side = load_ref(f"{body_name}_FaceSide", plain_head=True, contrast=1.15, ink=FACE_INK)
    back_head = back
    layers_front = load_layers(f"{body_name}_FaceFront")
    layers_side = load_layers(f"{body_name}_FaceSide")

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
    # One skin: the back's lit skin tone and shading depth follow the front's (measured on the
    # body texels each view owns outright, head and underwear excluded).
    under, _, _ = underwear_mask(P, lm, body_name == "Female")  # the Female mask includes the bra band
    own = (head_w < 0.1) & (under < 0.5)
    colour_b = match_skin_tone(colour_b, own & (w_b > 0.9) & (N[:, 1] > 0.7), colour_f, own & (w_f > 0.9) & (-N[:, 1] > 0.7))
    total = np.clip(w_f + w_b, 0, 1)
    body = (colour_f * w_f[:, None] + colour_b * w_b[:, None]) / np.maximum(w_f + w_b, 1e-6)[:, None]
    body = body * total[:, None] + colour_fill * (1 - total)[:, None]
    # The underwear: one flat grey with its seam lines (clean_cloth did the drawings; this evens
    # what the two views and the fill still disagree on). Garment texels = grey texels around the
    # pelvis, plus whatever the 3D briefs mask covers that came out grey.
    lum_b = luminance(body)
    sat_b = body.max(axis=1) - body.min(axis=1)
    zones = garment_zones(lm, body_name)
    grey_b = (sat_b < 0.10) & (lum_b > 0.10) & (lum_b < 0.45)
    cloth = grey_b & in_zones(P[:, 2], zones)
    side_limit = CLOTH_SIDE_FILL.get(body_name)
    if side_limit:
        on_mesh = np.abs(P).sum(axis=1) > 0  # the bake leaves off-island texels at the origin
        for z0, z1 in zones:
            piece = cloth & (P[:, 2] > z0) & (P[:, 2] < z1)
            if piece.sum() < 1000:
                continue
            g0, g1 = np.percentile(P[piece, 2], [1, 99])  # the garment's own height range
            in_zone = on_mesh & (P[:, 2] > g0 - 0.01) & (P[:, 2] < g1 + 0.01) & (np.abs(P[:, 0]) < 0.18)
            side = in_zone & (np.abs(N[:, 1]) < side_limit)
            src = in_zone & (np.abs(N[:, 1]) > 0.6) & ((w_f > 0.9) | (w_b > 0.9))
            if side.sum() and src.sum():
                near = fill_from_nearest(P, side, src, body)
                t = pb.smoothstep(side_limit, side_limit - 0.15, np.abs(N[side, 1]))[:, None]
                body[side] = body[side] * (1 - t) + near * t
                print("  garment side fill z %.3f..%.3f: %d side texels from %d sources" % (g0, g1, side.sum(), src.sum()))
        lum_b = luminance(body)
        sat_b = body.max(axis=1) - body.min(axis=1)
        grey_b = (sat_b < 0.10) & (lum_b > 0.10) & (lum_b < 0.45)
        cloth = grey_b & in_zones(P[:, 2], zones)
    print("  3D underwear mask vs drawn garment: %.1f%% of mask texels are grey, %.1f%% of grey texels inside mask" % (
        100 * grey_b[under > 0.5].mean(), 100 * (under[cloth] > 0.5).mean()))
    for z0, z1 in zones:  # each garment evened to its own grey
        piece = cloth & (P[:, 2] > z0) & (P[:, 2] < z1)
        if piece.sum() > 1000:
            body = even_cloth(body, P, piece)
    if SKIN_EVEN > 0:
        body = even_skin(body, P, N, head_w, lm, SKIN_EVEN, SKIN_EVEN_CELL, chest_band)

    # --- Head: face drawings matched by landmarks (front + profile), back of the head from the back view.
    drawn = body
    coverage = total
    layers = {}
    if face_front is not None and body_name in FACE_LANDMARKS:
        in_hole, eye_front, eye_side = eye_openings(P, eye_P, lm)
        for k, v in sorted({**eye_front, **eye_side}.items()):
            print(f"  eye pin {k}: ({v[0]:.4f}, {v[1]:.4f})")
        c_f, w_fr, l_f = project_head_view(P, N, face_front, lm, head_pts, body_name, "front", (in_hole, eye_front), layers_front)
        views = [(c_f, w_fr)]
        layer_views = [(l_f, w_fr)]
        if face_side is not None:
            c_s, w_s, l_s = project_head_view(P, N, face_side, lm, head_pts, body_name, "side", (in_hole, eye_side), layers_side)
            views.append((match_tone(c_s, w_s, c_f, w_fr, z=P[:, 2]), w_s))
            layer_views.append((l_s, w_s))
        if back_head is not None:
            colour_bh, w_bh = project_view(mesh_obj, P, N, back_head, mirror_x=True)
            w_bh = w_bh * pb.smoothstep(0.1, 0.6, N[:, 1]) ** 2
            views.append((match_tone(colour_bh, w_bh, views[-1][0], views[-1][1]), w_bh))
        wsum = sum(w for _, w in views)
        head_col = sum(c * w[:, None] for c, w in views) / np.maximum(wsum, 1e-6)[:, None]
        head_cov = np.clip(wsum, 0, 1)
        # Where no face drawing looks (top of the head, the temples between the front and the
        # profile, behind the ears) the composite would fall back to the full-body drawing's
        # stripped head, which is filled from its shadowed neck: a dark smear. Fill those texels
        # from the nearest well-covered head texel in 3D instead, so the skin simply continues.
        # (The texels facing down under the jaw continue the neck from the body drawing instead,
        # with a soft changeover so there is no edge along the jaw line.)
        head_region = head_w > 0.3
        gaps = head_region & (head_cov < 0.95)
        near = fill_from_nearest(P, gaps, head_region & (head_cov >= 0.95), head_col)
        up = pb.smoothstep(-0.55, -0.15, N[gaps, 2])[:, None]
        filler = near * up + body[gaps] * (1 - up)
        head_col[gaps] = head_col[gaps] * head_cov[gaps, None] + filler * (1 - head_cov[gaps, None])
        head_cov[gaps] = 1.0
        # One painting: the face drawings' skin tone follows the body drawing's chest tone.
        front_skin = (-N[:, 1] > 0.6)
        face_px = front_skin & (head_w > 0.9) & (head_cov > 0.9) & (P[:, 2] > lm.chin[2])
        gain = tone_gain(head_col, face_px,
                         body, front_skin & (head_w < 0.05) & (w_f > 0.8) & (P[:, 2] > chest_band[0]) & (P[:, 2] < chest_band[1]))
        print("  head -> body tone gain", np.round(gain, 3))
        head_col = np.clip(head_col * gain, 0, 1)
        lsum = sum(w for _, w in layer_views)
        for key in layer_views[0][0]:
            value = sum(lv[key] * w for lv, w in layer_views) / np.maximum(lsum, 1e-6)
            layers[key] = np.clip(value * np.clip(lsum, 0, 1) * head_w, 0, 1)
        # The stripped beard zone is skin fill from two drawings (and the gap fill), each with its
        # own level: even its low-frequency tone to the cheeks (1 cm local mean -> the lit cheek
        # median), keeping the grain and any ink. The real shading comes back from shadow_bands().
        zone = layers.pop("beard_region", None)
        if zone is not None and (zone > 0.02).any():  # the Female has no beard region
            lum_h = luminance(head_col)
            cheek_px = face_px & (np.abs(P[:, 0]) > 0.03) & (np.abs(P[:, 0]) < 0.06) \
                & (P[:, 2] > lm.mouth[2]) & (P[:, 2] < lm.eye_l[2] - 0.015) & (lum_h > 0.4)
            ref = float(np.median(lum_h[cheek_px])) if cheek_px.sum() > 200 else float(luminance(SKIN[None])[0])
            on = head_region & (zone > 0.02)
            mean = np.ones_like(lum_h)
            mean[on] = local_mean(P, on, np.where(lum_h > 0.35, lum_h, ref))  # ink does not drag the mean
            even = np.clip(ref / np.maximum(mean, 1e-3), 0.8, 1.3)
            print("  beard zone tone: ref %.3f, zone mean %.3f -> gain p10/p90 %.2f/%.2f" % (
                ref, float(mean[on].mean()), *np.percentile(even[on], [10, 90])))
            head_col = np.clip(head_col * (1 + (even - 1) * zone)[:, None], 0, 1)
        # The body drawing's neck is a shadow tone, darker than the face drawings' jaw: grade the
        # neck to the jaw tone where the two meet, fading back to the body's own tone by the chest.
        # (No visibility test: from the front the upper neck hides behind the chin, its colour comes
        # from the drawing's soft copy at the same height.)
        # Lit skin only on both sides (no ink, no lips, no mouth interior): the upper neck against
        # the lower cheeks / upper lip zone.
        lit_body = luminance(body) > 0.42
        lit_head = luminance(head_col) > 0.5
        neck_px = front_skin & (head_w < 0.5) & (P[:, 2] > lm.chin[2] - 0.12) & (P[:, 2] < lm.chin[2] - 0.03) & lit_body
        jaw_px = face_px & (P[:, 2] > lm.chin[2] + 0.04) & (P[:, 2] < lm.chin[2] + 0.11) & lit_head
        neck_gain = tone_gain(body, neck_px, head_col, jaw_px)
        print("  neck -> jaw tone gain", np.round(neck_gain, 3))
        neck_t = pb.smoothstep(lm.chin[2] - NECK_FADE, lm.chin[2] - 0.05, P[:, 2]) * (1 - head_w)
        body = np.clip(body * (1 + (neck_gain - 1) * neck_t[:, None]), 0, 1)
        head_col = head_col * head_cov[:, None] + body * (1 - head_cov)[:, None]
        drawn = head_col * head_w[:, None] + body * (1 - head_w)[:, None]
        coverage = np.maximum(total, head_cov * head_w)

    under_chin = pb.smoothstep(lm.chin[2] - 0.065, lm.chin[2] - 0.05, P[:, 2]) * pb.smoothstep(lm.chin[2] + 0.01, lm.chin[2], P[:, 2]) \
        * pb.smoothstep(0.09, 0.07, np.abs(P[:, 0])) * pb.smoothstep(lm.chin[1] + 0.10, lm.chin[1] + 0.08, P[:, 1])
    out = shadow_bands(drawn, shadow, no_core=under_chin)
    # Mouth cavity (inner lips, throat): no drawing sees it, so it would stay the fill colour;
    # paint it the dark inside like the procedural paint does, and keep it out of the skin mask.
    cavity = mouth_cavity(P, N, 1.0 - shadow, lm, head_w)
    out = out * (1 - cavity)[:, None] + THROAT * cavity[:, None]
    if os.environ.get("PROJ_DEBUG"):  # dump the intermediates for inspection (iteration helper)
        np.savez(os.environ["PROJ_DEBUG"], body=body, drawn=drawn, out=out, coverage=coverage, head_w=head_w,
                 w_f=w_f, w_b=w_b, shadow=shadow, cavity=cavity, P=P, N=N,
                 **{f"layer_{k}": v for k, v in layers.items()},
                 **({"head_col": head_col, "head_cov": head_cov, "w_front": views[0][1],
                     "w_side": views[1][1] if len(views) > 1 else w_f * 0} if face_front is not None else {}))
    pb.save_png(pb.to_image(out, valid, RES, 3), os.path.join(TEX_DIR, f"T_Body_{body_name}_BaseColor_Drawn.png"))
    pb.save_png(pb.to_image(coverage, valid, RES, 1),
                os.path.join(TEX_DIR, f"T_Body_{body_name}_DrawnCoverage.png"), size=1024)
    # Tint masks matching the drawn texture: R skin (no ink, no underwear), G hair (none: plain
    # head), B underwear (the drawing's grey cloth around the pelvis).
    lum = luminance(out)
    ink = pb.smoothstep(0.30, 0.20, lum)
    sat = out.max(axis=1) - out.min(axis=1)
    under = pb.smoothstep(0.12, 0.06, sat) * in_zones(P[:, 2], zones) * (lum > 0.12)
    masks = np.stack([np.clip((1 - ink) * (1 - under) * (1 - cavity), 0, 1), np.zeros_like(lum), under], axis=1)
    pb.save_png(pb.to_image(masks, valid, RES, 3), os.path.join(TEX_DIR, f"T_Body_{body_name}_Masks.png"), size=pb.MASK_RES)
    for key, value in layers.items():
        if value.max() <= 0.0:  # the Female has no beard region: no empty layer file
            print("  layer %s is empty, not written" % key)
            continue
        pb.save_png(pb.to_image(value, valid, RES, 1),
                    os.path.join(TEX_DIR, f"T_Body_{body_name}_{key.capitalize()}.png"), size=pb.MASK_RES)
    pb.hook_up_materials(body_name, blend_path, colour_file=f"T_Body_{body_name}_BaseColor_Drawn.png")


FACE_INK = 2.5    # power curve applied to the face drawings' dark pixels before projection (strengthen_ink)
FACE_INK_GROW = 1  # px the face ink is widened by
FACE_INK_RING = 0.85  # strength of the widening ring (1 = the full darkness of the line next to it)
INK_LEVEL = 0.35   # luminance below which a face drawing pixel counts as ink for the levels
OUTLINE_RING = 6   # px inside the body drawings' silhouette that count as outline ink (strip_outline)
EDGE_BAND = 10     # px inside the body drawings' silhouette that are blurred plain (body_source)
EDGE_BLUR = 5      # sigma of that blur
OUTLINE_REACH = 40  # px the outline removal follows connected dark pixels inwards (shoulder-top black masses)
NECK_FADE = 0.16  # m below the chin where the neck grading has faded back to the body drawing's tone
SKIN_EVEN = 0.75   # how far the body skin's low-frequency tone is pulled to the chest's lit tone (even_skin)
SKIN_EVEN_CELL = 0.01  # m: the evening works on 3 x this window
SIDE_BLEND = 0.30  # sin of the half-angle over which the front and back drawings cross over at the sides
SIDE_GATE = 0.05   # m of self-occlusion a texel may have past a view's silhouette and still blend from it


if __name__ == "__main__":
    main()
