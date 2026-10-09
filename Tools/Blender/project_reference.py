"""
Projects a hand-drawn (or AI-drawn) comic reference of the character onto the base body texture.

The drawing must be a full-body T-pose on a plain black background, like
Art/Characters/Base/Reference/Male_Front.png. The silhouette of the drawing is matched to the
silhouette of the 3D body (head top, neck, hands, armpits, crotch, feet), the drawing is warped
to fit, and every texel that faces the camera is coloured from it. A back-view drawing is used
the same way for the back; without one, the back is filled with a softened copy of the front
(ink lines dissolved) so the colour is continuous.

The head is NOT taken from the full-body drawing (the face is far too small in it); it keeps the
colour already in the base texture, so the face can be painted by a separate close-up pass.

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


def grade_to_palette(drawn, shadow):
    """Match the drawing's skin to the palette (AI drawings run ~2x too saturated), then add the
    painted half-tone / core shadow bands the drawing lacks."""
    lum = luminance(drawn)
    sat = drawn.max(axis=1) - drawn.min(axis=1)
    skin_px = (lum > 0.35) & (sat > 0.15) & (drawn[:, 0] > drawn[:, 2])
    median = np.median(drawn[skin_px], axis=0) if skin_px.sum() > 100 else SKIN
    graded = drawn * (SKIN / np.maximum(median, 1e-3))
    # Pull saturation down toward the palette (keeps ink dark, since ink has little saturation).
    g_lum = luminance(graded)[:, None]
    graded = g_lum + (graded - g_lum) * 0.8
    half = pb.smoothstep(0.14, 0.22, shadow)[:, None]
    core = pb.smoothstep(0.32, 0.40, shadow)[:, None]
    mul = (1.0 + (HALF_MUL - 1.0) * half) * (1.0 + (CORE_MUL / HALF_MUL - 1.0) * core)
    return np.clip(graded * mul, 0.0, 1.0)


def main():
    body_name = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "Male"
    blend_path = os.path.join(BASE_DIR, f"BaseBody_{body_name}.blend")
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    mesh_obj = bpy.data.objects[f"SK_BaseBody_{body_name}"]

    front = np.asarray(Image.open(os.path.join(REF_DIR, f"{body_name}_Front.png")).convert("RGB")).astype(np.float32) / 255
    back_path = os.path.join(REF_DIR, f"{body_name}_Back.png")
    back = (np.asarray(Image.open(back_path).convert("RGB")).astype(np.float32) / 255
            if os.path.exists(back_path) else None)

    skin_obj = pb.make_copy(mesh_obj, keep_eyes=False)
    pos = pb.bake(skin_obj, RES, pb.geom_socket("Position"))
    nrm = pb.bake(skin_obj, RES, pb.geom_socket("Normal"))
    head = pb.bake(skin_obj, RES, pb.attributes_socket(["head", "neck_01", None]))
    ao_l = pb.bake(skin_obj, RES, pb.ao_socket(0.15), samples=24)
    valid = pos[:, :, 3].reshape(-1) > 0.5
    P = pos.reshape(-1, 4)[valid][:, :3].copy()
    N = nrm.reshape(-1, 4)[valid][:, :3].copy()
    head_w = head.reshape(-1, 4)[valid][:, 0]
    shadow = 1.0 - ao_l.reshape(-1, 4)[valid][:, 0]
    del pos, nrm, head, ao_l

    colour_f, w_f = project_view(mesh_obj, P, N, front, mirror_x=False)
    if back is not None:
        colour_b, w_b = project_view(mesh_obj, P, N, back, mirror_x=True)
    else:
        soft = soft_copy(front, drawing_mask(front))
        colour_b, w_b = project_view(mesh_obj, P, N, soft, mirror_x=True)
        w_b *= 0.999  # keep slightly under the front so the front wins where both see a texel

    # Anything neither view sees gets the softened front colour at the same height/side.
    soft = soft_copy(front, drawing_mask(front))
    colour_fill, _ = project_view(mesh_obj, P, N, soft, mirror_x=False)

    total = np.clip(w_f + w_b, 0, 1)
    drawn = (colour_f * w_f[:, None] + colour_b * w_b[:, None]) / np.maximum(w_f + w_b, 1e-6)[:, None]
    drawn = drawn * total[:, None] + colour_fill * (1 - total)[:, None]
    drawn = grade_to_palette(drawn, shadow)

    base_path = os.path.join(TEX_DIR, f"T_Body_{body_name}_BaseColor_BL.png")
    base = np.asarray(Image.open(base_path).convert("RGB").resize((RES, RES))).astype(np.float32) / 255
    base = base[::-1].reshape(-1, 3)[valid]
    keep_head = pb.smoothstep(0.35, 0.65, head_w)
    out = drawn * (1 - keep_head)[:, None] + base * keep_head[:, None]

    pb.save_png(pb.to_image(out, valid, RES, 3), os.path.join(TEX_DIR, f"T_Body_{body_name}_BaseColor_Drawn.png"))
    pb.save_png(pb.to_image(total * (1 - keep_head), valid, RES, 1),
                os.path.join(TEX_DIR, f"T_Body_{body_name}_DrawnCoverage.png"), size=1024)


if __name__ == "__main__":
    main()
