"""
Borderlands / Telltale style skin painting, in numpy, on baked body maps.

Every rule and number here follows Docs/Art/Borderlands_Style_Guide.md (measured from Borderlands
frames and the studios' own descriptions of their process). The short version:

  * ink is a warm black; thick lines ~100% opaque, thin lines 45-65% so they read brown over skin
  * ink sits on the shadow side / underside of forms, never on both sides, and big areas stay clean
  * three painted values: lit, half-tone (x0.72) and core (x0.55); shadows shift warm, never grey
  * hatching only inside shadow pockets, along the form, strokes in small clusters, under 5% of skin
  * placed highlights (nose bridge, cheekbone, brow, chin, collarbone, deltoid), not a global light
  * eyes: one heavy upper-lid wedge, thin broken lower lid, tinted sclera, limbal ring, one highlight
  * hair as flat clumps separated by ink with one light streak each; beard as a soft mass + strokes

The silhouette outline is NOT painted: the engine's Sobel post-process does that.

Inputs are flat per-texel arrays (valid texels only): P position (m), N normal, ao_s (12 mm
occlusion), ao_m (40 mm), ao_l (150 mm), point (pointiness, 0.5 flat), hatch axis per texel, and
the vertex-group masks lips / nails / ears / scalp / nipple / head.
"""

import numpy as np

# ---------------------------------------------------------------------------------------------
# Colours (sRGB 0..1)


def hex_col(h):
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5)], np.float32)


SKIN = hex_col("#C4947A")        # lit base, "light/medium" tone; Unreal re-tints from this
INK = hex_col("#1A100C")         # warm black
HAIR = hex_col("#17110E")
UNDERWEAR = hex_col("#333338")
IRIS = hex_col("#4D2B17")
LIPS = hex_col("#B5786A")
UNDER_EYE_MUL = np.array([0.86, 0.83, 0.86], np.float32)   # x0.85, hue toward purple-brown
WARMTH = hex_col("#C97C64")
SCLERA = hex_col("#D9D2C6")
EYE_HIGHLIGHT = hex_col("#F2EEE8")
THROAT = hex_col("#3A1A18")      # mouth cavity (inside of the lips), matches paint_mouth.DARK

# Band multipliers: core = x0.55 luminance with hue -10 deg and saturation x1.3 (never grey).
CORE_MUL = np.array([0.78, 0.52, 0.42], np.float32)
HALF_MUL = 1.0 + (CORE_MUL - 1.0) * 0.6      # ~x0.72
CREASE_MUL = np.array([0.62, 0.40, 0.32], np.float32)   # x0.42 in creases
HIGHLIGHT_MUL = np.array([1.11, 1.13, 1.06], np.float32)   # +12% L, hue +3 toward yellow

# Hair, brows and beard are separate layers/meshes in the game (swappable in the character
# creator), so the head is painted plain. The beard mask is still written as an optional layer.
PLAIN_HEAD = True

STYLES = {
    # ink: line strength; thin_alpha: thin-line opacity; hatch: hatching amount; hard: band edge
    # hardness; warmth: warmth-zone blend; sat: saturation multiplier on the lit skin
    "BL": {"ink": 1.0, "thin_alpha": 0.55, "hatch": 1.0, "bands": 1.0, "warmth": 0.30, "sat": 1.0,
           "uniform_lines": False, "ink_col": INK},
    "TT": {"ink": 1.0, "thin_alpha": 1.0, "hatch": 0.0, "bands": 0.7, "warmth": 0.15, "sat": 0.9,
           "uniform_lines": True, "ink_col": np.zeros(3, np.float32)},
}


# ---------------------------------------------------------------------------------------------
# Maths helpers


def smoothstep(e0, e1, x):
    span = np.asarray(e1, np.float32) - e0
    span = np.where(np.abs(span) < 1e-8, 1e-8, span)
    t = np.clip((x - e0) / span, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _hash(ix, iy, iz, seed):
    h = (ix * 73856093) ^ (iy * 19349663) ^ (iz * 83492791) ^ (seed * 2654435761)
    h = (h ^ (h >> 13)) * 1274126177
    return ((h ^ (h >> 16)) & 0xFFFF).astype(np.float32) / 65535.0


def hash1(x, seed=0):
    """Hash of integer array -> 0..1."""
    return _hash(x.astype(np.int64), np.zeros_like(x, dtype=np.int64), np.zeros_like(x, dtype=np.int64), seed)


def noise(p, scale, seed=0):
    """Smooth 3D value noise in 0..1. p is (N, 3); scale is a float or a 3-vector (cycles per metre)."""
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


def luminance(c):
    return c[:, 0] * 0.3 + c[:, 1] * 0.59 + c[:, 2] * 0.11


def saturate_colour(c, amount):
    lum = luminance(c)[:, None]
    return np.clip(lum + (c - lum) * amount, 0.0, 1.0)


def line_band(dist, width, soft=0.0004):
    """1 inside a line of the given full width (array or float), with a slightly soft edge."""
    half = np.maximum(np.asarray(width, np.float32) * 0.5, 1e-5)
    return smoothstep(half + soft, half - soft * 0.5, np.abs(dist))


def taper(t):
    """Line ends: full in the middle, tapering over the last 20% at each end. t in 0..1."""
    return smoothstep(0.0, 0.2, t) * smoothstep(1.0, 0.8, t)


def broken(P, scale=90.0, coverage=0.7, seed=5):
    """Gate for broken lines: ~coverage of the length is drawn."""
    return smoothstep(1.0 - coverage - 0.05, 1.0 - coverage + 0.05, noise(P, scale, seed))


def wobble(P, amount=0.0006, seed=7):
    return (noise(P, 200.0, seed) - 0.5) * 2.0 * amount


def dist_to_polyline(xz, pts):
    """Distance from 2D points (N,2) to a polyline (K,2), and the 0..1 position along it."""
    best = np.full(len(xz), np.inf, np.float32)
    best_t = np.zeros(len(xz), np.float32)
    seg_len = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    total = seg_len.sum()
    acc = 0.0
    for k in range(len(pts) - 1):
        a, b = pts[k], pts[k + 1]
        ab = b - a
        t = np.clip(((xz - a) @ ab) / max(ab @ ab, 1e-9), 0.0, 1.0)
        d = np.linalg.norm(xz - (a + t[:, None] * ab), axis=1)
        closer = d < best
        best = np.where(closer, d, best)
        best_t = np.where(closer, (acc + t * seg_len[k]) / total, best_t)
        acc += seg_len[k]
    return best, best_t


# ---------------------------------------------------------------------------------------------
# Face frames


def eye_frame(P, lm):
    """Per-texel coordinates around the nearest eye: outward, up, forward(+y = back), distance."""
    left = P[:, 0] > 0
    centre = np.where(left[:, None], lm.eye_l, lm.eye_r)
    side = np.where(left, 1.0, -1.0).astype(np.float32)
    d = P - centre
    return d[:, 0] * side, d[:, 2], d[:, 1], np.linalg.norm(d, axis=1)


def face_front(P, N, lm):
    """1 on the front of the face (where landmark lines are drawn in the x/z plane)."""
    on_head = (P[:, 2] > lm.chin[2] - 0.05) & (P[:, 1] < lm.ear_y + 0.01)
    return smoothstep(-0.15, -0.45, N[:, 1]) * on_head


# ---------------------------------------------------------------------------------------------
# Hair, brows, beard


def paint_brows(P, N, lm, male):
    """Angled wedge brow: fill mask and the medium ink line on its underside."""
    ox, up, fwd, _ = eye_frame(P, lm)
    r = lm.eye_radius
    eye_w = 2.0 * r
    start, end = -0.35 * eye_w, 0.55 * eye_w + 0.005          # inner end near the nose, tail 5 mm past corner
    t = np.clip((ox - start) / (end - start), 0.0, 1.0)
    under = r * 1.1 + 0.001
    # Inner end angled down toward the nose (resting frown), tail lifts then drops.
    centre = under + (0.0035 if male else 0.0045) + 0.004 * np.sin(t * np.pi) - 0.004 * (1.0 - t) ** 2
    height = (0.0075 if male else 0.0055) * (1.0 - 0.72 * smoothstep(0.35, 1.0, t))
    edge = (noise(P, (320.0, 320.0, 900.0), seed=12) - 0.5) * 0.0012
    inside = smoothstep(0.0006, -0.0006, np.abs(up - centre + edge) - height * 0.5)
    ends = smoothstep(start - 0.002, start + 0.002, ox) * smoothstep(end + 0.001, end - 0.004, ox)
    fill = inside * ends * smoothstep(-0.15, -0.45, N[:, 1]) * (fwd < 0.012)
    # Hair strokes on the top edge (thin) and the underside ink (medium, inner 2/3, broken in two).
    top_edge = line_band(up - (centre + height * 0.5), 0.0009) * ends * (noise(P, (900.0, 1.0, 1.0), 14) > 0.45)
    under_ink = line_band(up - (centre - height * 0.5), 0.0018) * ends * smoothstep(0.75, 0.6, t) * broken(P, 60.0, 0.8, 15)
    strokes = noise(P, (160.0, 600.0, 1200.0), seed=11)
    fill = np.clip(fill * (0.85 + 0.25 * strokes), 0.0, 1.0)
    ink = np.clip(np.maximum(top_edge * 0.6, under_ink * 0.9), 0.0, 1.0) * (fwd < 0.012)
    return fill, ink


def paint_beard(P, N, lm, lips_f):
    """Short full beard: a soft solid mass plus ink strokes along its lower edge and moustache."""
    ax = np.abs(P[:, 0])
    z = P[:, 2]
    zn = z + (fbm(P, 220.0, 2, seed=21) - 0.5) * 0.006
    corner_x = lm.mouth_half_width + 0.003
    nose_base = lm.nose_tip[2] - 0.011

    side_t = np.clip((ax - corner_x) / max(lm.ear_x - 0.012 - corner_x, 1e-3), 0.0, 1.0)
    cheek_top = (lm.lips_top + 0.004) + side_t ** 1.4 * ((lm.eye_l[2] - 0.012) - (lm.lips_top + 0.004))
    cheeks = smoothstep(0.003, -0.003, zn - cheek_top) * smoothstep(corner_x - 0.008, corner_x, ax)

    jaw_t = np.clip(ax / lm.ear_x, 0.0, 1.0)
    low = (lm.chin[2] - 0.045) + jaw_t ** 2 * ((lm.mouth[2] - 0.02) - (lm.chin[2] - 0.045))
    lower = smoothstep(-0.004, 0.004, zn - low)

    m_top = nose_base + 0.002 - 0.008 * (ax / corner_x) ** 2
    moustache = (smoothstep(-0.003, 0.003, zn - (lm.lips_top - 0.004)) * smoothstep(0.002, -0.002, zn - m_top)
                 * smoothstep(0.002, -0.004, ax - (corner_x + 0.006)))
    chin = smoothstep(0.003, -0.003, zn - (lm.lips_bottom - 0.002)) * smoothstep(0.004, -0.002, ax - (corner_x + 0.004))
    beside_soul = (smoothstep(-0.013, -0.009, zn - lm.lips_bottom) * smoothstep(0.002, -0.002, zn - lm.lips_bottom)
                   * smoothstep(0.007, 0.010, ax) * smoothstep(corner_x - 0.002, corner_x - 0.008, ax))
    chin *= 1.0 - beside_soul

    mass = np.maximum(np.maximum(cheeks, moustache), chin) * lower
    mass *= smoothstep(lm.ear_y + 0.006, lm.ear_y - 0.008, P[:, 1])
    mass *= 1.0 - smoothstep(0.25, 0.5, lips_f)
    mass = np.clip(mass, 0.0, 1.0)

    # Ink strokes: short downward strokes along the lower edge and the moustache corners.
    stroke_cols = np.floor(P[:, 0] / 0.011)
    col_on = hash1(stroke_cols, 31) > 0.35
    col_off = (hash1(stroke_cols, 32) - 0.5) * 0.004
    u = P[:, 0] - (stroke_cols + 0.5) * 0.011 + col_off
    along = (zn - low) / 0.009
    edge_strokes = line_band(u, 0.001) * taper(np.clip(along, 0, 1)) * (along > 0) * (along < 1) * col_on
    edge_strokes *= smoothstep(0.0, 0.3, mass)
    m_corner = np.exp(-((ax - corner_x - 0.004) / 0.006) ** 2 - ((zn - lm.lips_top) / 0.006) ** 2)
    corner_strokes = line_band(np.sin((zn - lm.lips_top) * 1400.0) * 0.0025, 0.0009) * m_corner * moustache
    strokes = np.clip(np.maximum(edge_strokes, corner_strokes), 0.0, 1.0)
    return mass, strokes


def paint_scalp(P, N, scalp_f):
    """Flat hair clumps separated by ink, each with one light streak; spiky hairline."""
    hairline = scalp_f + (fbm(P, 90.0, 2, seed=31) - 0.5) * 0.9
    density = smoothstep(0.42, 0.55, hairline)
    # Clump cells: jittered grid on a skewed xz plane so clumps run back over the skull.
    cell_size = 0.018
    q = np.stack([P[:, 0] + 0.35 * P[:, 1], P[:, 2] - 0.25 * P[:, 1]], 1) / cell_size
    ci = np.floor(q)
    best_d, second_d, best_id = np.full(len(P), np.inf), np.full(len(P), np.inf), np.zeros(len(P))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            c = ci + np.array([dx, dy])
            jitter = np.stack([hash1(c[:, 0] * 131 + c[:, 1], 41), hash1(c[:, 0] * 131 + c[:, 1], 42)], 1)
            centre = c + 0.2 + 0.6 * jitter
            d = np.linalg.norm(q - centre, axis=1)
            closer = d < best_d
            second_d = np.where(closer, best_d, np.minimum(second_d, d))
            best_id = np.where(closer, c[:, 0] * 131 + c[:, 1], best_id)
            best_d = np.where(closer, d, best_d)
    border = (second_d - best_d) * cell_size          # metres to the clump border
    ink = line_band(border, 0.0015) * density
    shade = np.where(hash1(best_id, 43) > 0.5, 1.0, 0.62)
    streak = smoothstep(0.0032, 0.0016, np.abs(border - 0.006)) * (hash1(best_id, 44) > 0.3)
    value = shade * (1.0 + 0.35 * streak)
    return np.clip(density, 0, 1), np.clip(ink, 0, 1), value


# ---------------------------------------------------------------------------------------------
# Underwear


def underwear_mask(P, lm, female, briefs_top=0.035, briefs_leg=0.16):
    top = lm.bone_head["pelvis"][2] + briefs_top
    left = P[:, 0] > 0
    thigh_head = np.where(left[:, None], lm.bone_head["thigh_l"], lm.bone_head["thigh_r"])
    thigh_tail = np.where(left[:, None], lm.bone_tail["thigh_l"], lm.bone_tail["thigh_r"])
    axis = thigh_tail - thigh_head
    axis /= np.linalg.norm(axis, axis=1, keepdims=True)
    along = np.einsum("ij,ij->i", P - thigh_head, axis)
    d_top = top - P[:, 2]
    d_leg = briefs_leg - along
    near_hips = P[:, 2] > lm.bone_head["thigh_l"][2] - 0.35
    inside = np.where(near_hips, np.minimum(d_top, d_leg), -1.0)
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
    return smoothstep(-0.0008, 0.0008, inside), edge_dist, waistband


# ---------------------------------------------------------------------------------------------
# Face ink drawn from landmarks


def face_ink(P, N, lm, st):
    """Landmark-drawn lines: lid wedge, lower lid, nasolabial, mouth, corners, chin crease."""
    front = face_front(P, N, lm)
    ox, up, fwd, eye_d = eye_frame(P, lm)
    r = lm.eye_radius
    w = wobble(P)
    thick_alpha, thin_alpha = 1.0, st["thin_alpha"]
    uni = st["uniform_lines"]

    # Upper lid + lash line: one wedge, 3 mm inner -> 5 mm outer, tapering past the outer corner.
    t_outer = np.clip((ox + 0.3 * r) / (1.3 * r), 0.0, 1.0)
    width = 0.0022 if uni else 0.003 + 0.002 * smoothstep(0.0, 1.0, t_outer)
    past = np.clip((ox - 0.9 * r) / (0.6 * r), 0.0, 1.0)
    width = width * (1.0 - 0.75 * past)
    on_upper = smoothstep(-0.002, 0.002, up + 0.2 * r * past)
    lid = line_band(eye_d - (r + width * 0.5) + w, width, 0.0003) * on_upper * (ox > -1.05 * r) * (ox < 1.5 * r)
    ink = lid * thick_alpha

    # Lower lid: thin, outer half, broken, plus lash ticks at the outer corner.
    lower = line_band(eye_d - (r + 0.0005) + w, 0.0022 if uni else 0.0008) * (up < -0.001) * (ox > 0.1 * r)
    lower *= broken(P, 120.0, 0.65, 16)
    tick_d = (up + 0.3 * r) * 0.8 + (ox - 1.15 * r) * 0.6   # angled lash ticks at the outer corner
    ticks = line_band(np.sin((ox - 1.05 * r) * 3000.0) * 0.0012, 0.0005) * smoothstep(0.0025, 0.0005, np.abs(tick_d)) \
        * (ox > 1.0 * r) * (ox < 1.28 * r)
    ink = np.maximum(ink, (lower * thin_alpha + ticks * 0.7 * thin_alpha) * (fwd < 0.01))

    # Crow's feet (BL only): 2 broken strokes radiating from the outer corner.
    if not uni:
        for k, ang in enumerate((-0.35, 0.15)):
            d = (up - 0.1 * r) * np.cos(ang) - (ox - 1.25 * r) * np.sin(ang)
            along_s = (ox - 1.25 * r) * np.cos(ang) + (up - 0.1 * r) * np.sin(ang)
            s = line_band(d + w, 0.0008) * taper(np.clip(along_s / 0.010, 0, 1)) * (along_s > 0) * (along_s < 0.010)
            ink = np.maximum(ink, s * thin_alpha * 0.8 * broken(P, 150.0, 0.7, 17 + k))

    # Landmark lines in the x/z plane of the face front.
    xz = np.stack([np.abs(P[:, 0]), P[:, 2]], 1)
    mhw = lm.mouth_half_width
    z_mid = 0.5 * (lm.lips_top + lm.lips_bottom)
    nose_z, nose_x = lm.nose_tip[2], 0.0

    # Nasolabial fold: ala to 8 mm past the mouth corner, bulging outward; shadow (outer) side.
    ala = np.array([0.013, nose_z - 0.006])
    end = np.array([mhw + 0.010, z_mid - 0.012])
    mid = np.array([mhw + 0.013, nose_z - 0.025])
    d, t = dist_to_polyline(xz, np.array([ala, mid, end]))
    outer_side = smoothstep(-0.001, 0.001, xz[:, 0] - (ala[0] + (end[0] - ala[0]) * t))
    naso = line_band(d + w, 0.0022 if uni else 0.0015) * taper(t) * (0.4 + 0.6 * outer_side)
    ink = np.maximum(ink, naso * 0.9 * front)

    # Mouth closure line: 2 mm at the centre tapering to 1 mm at the ends; pure ink.
    t_m = np.clip(xz[:, 0] / mhw, 0, 1)
    closure = line_band(xz[:, 1] - z_mid + w * 0.5, 0.0022 if uni else 0.002 - 0.001 * t_m) * (xz[:, 0] < mhw * 1.02)
    closure *= smoothstep(0.012, 0.006, np.abs(P[:, 1] - lm.mouth[1]))
    ink = np.maximum(ink, closure * thick_alpha * front)

    # Mouth corner brackets: thick 2.5-3 mm, 4-6 mm long, curving down.
    cx_, cz_ = mhw + 0.0015, z_mid
    dc = np.hypot(xz[:, 0] - cx_, xz[:, 1] - cz_)
    bracket = line_band(dc - 0.0025, 0.0022 if uni else 0.0028) * (xz[:, 0] > cx_ - 0.0005) * (xz[:, 1] < cz_ + 0.002)
    ink = np.maximum(ink, bracket * thick_alpha * front)

    # Lower-lip underside / chin crease: medium, centre 60%, broken.
    chin_crease = line_band(xz[:, 1] - (lm.lips_bottom - 0.0055) + w, 0.0022 if uni else 0.0015) \
        * smoothstep(mhw * 0.7, mhw * 0.5, xz[:, 0]) * broken(P, 100.0, 0.7, 18)
    ink = np.maximum(ink, chin_crease * 0.9 * front * smoothstep(0.012, 0.006, np.abs(P[:, 1] - lm.mouth[1])))

    # Nose: one broken scratch along each side of the bridge (thin) and the ala crease (medium).
    bridge = line_band(xz[:, 0] - 0.0075 + w, 0.0008) * (xz[:, 1] > nose_z + 0.008) * (xz[:, 1] < lm.eye_l[2] - 0.004) \
        * broken(P, 80.0, 0.5, 19) * smoothstep(-0.25, -0.6, N[:, 1])
    ala_c = np.hypot(xz[:, 0] - 0.0105, xz[:, 1] - (nose_z - 0.004))
    ala_ink = line_band(ala_c - 0.0065, 0.0022 if uni else 0.0016) * (xz[:, 0] > 0.0105) * (xz[:, 1] < nose_z + 0.002)
    ink = np.maximum(ink, np.maximum(bridge * thin_alpha, ala_ink * 0.9) * front)

    return np.clip(ink, 0.0, 1.0)


# ---------------------------------------------------------------------------------------------
# Hatching


def hatching(P, axis, shadow, ao_s, ao_m, face_zone, st):
    """Shadow-pocket hatching along the form axis, in clusters, tapering toward the terminator."""
    if st["hatch"] <= 0:
        return np.zeros(len(P), np.float32)
    # Perpendicular direction used for spacing: world-fixed per axis so lines stay straight.
    ref = np.where(np.abs(axis[:, 2:3]) > 0.7, np.array([[1.0, 0.0, 0.0]], np.float32), np.array([[0.0, 0.0, 1.0]], np.float32))
    perp = np.cross(axis, ref)
    perp /= np.maximum(np.linalg.norm(perp, axis=1, keepdims=True), 1e-6)
    u = np.einsum("ij,ij->i", P, perp)       # across strokes
    v = np.einsum("ij,ij->i", P, axis)       # along strokes

    depth = smoothstep(0.28, 0.45, shadow)
    spacing = 0.0055 + (0.003 - 0.0055) * depth
    length = np.where(face_zone > 0.5, 0.006, 0.018) * (0.5 + 0.5 * depth)
    line_id = np.floor(u / spacing)
    du = u - (line_id + 0.5) * spacing + (hash1(line_id, 51) - 0.5) * spacing * 0.3
    seg_len = length * 1.6
    seg_id = np.floor(v / seg_len)
    tv = (v - seg_id * seg_len) / length
    stroke = line_band(du + wobble(P, 0.0003, 52), 0.0008, 0.0003) * taper(np.clip(tv, 0, 1)) * (tv < 1.0)
    stroke *= hash1(line_id * 977 + seg_id, 53) > 0.3
    cluster = smoothstep(0.45, 0.6, noise(P, 110.0, 54))
    zone = (shadow > 0.28) * smoothstep(0.92, 0.86, ao_m) * smoothstep(0.98, 0.9, ao_s)
    hatch = stroke * cluster * zone * (0.5 + 0.5 * depth) * 0.65
    # Cross-hatch only in the darkest pockets (armpit, groin).
    cross_zone = (shadow > 0.6) * smoothstep(0.75, 0.6, ao_s)
    if cross_zone.any():
        rot = np.cos(1.047) * perp + np.sin(1.047) * np.cross(axis, perp)
        u2 = np.einsum("ij,ij->i", P, rot)
        line2 = np.floor(u2 / (spacing * 2))
        du2 = u2 - (line2 + 0.5) * spacing * 2
        hatch = np.maximum(hatch, line_band(du2, 0.0008) * cross_zone * cluster * 0.4)
    return np.clip(hatch * st["hatch"], 0.0, 1.0)


# ---------------------------------------------------------------------------------------------
# Body


def paint_body(body_name, maps, lm, style):
    """Returns (colour, skin mask, hair mask, underwear mask, beard mask) per texel."""
    male = body_name == "Male"
    st = STYLES[style]
    P, N = maps["pos"], maps["nrm"]
    ao_s, ao_m, ao_l, point = maps["ao_s"], maps["ao_m"], maps["ao_l"], maps["point"]
    lips_f, nails_f, ears_f = maps["m1"][:, 0], maps["m1"][:, 1], maps["m1"][:, 2]
    scalp_f, nipple_f, head_f = maps["m2"][:, 0], maps["m2"][:, 1], maps["m2"][:, 2]
    axis = maps["axis"]
    shadow = 1.0 - ao_l
    ink_col = st["ink_col"]

    lit = saturate_colour(np.repeat(SKIN[None, :], len(P), axis=0), st["sat"])
    colour = lit.copy()

    # --- Three painted values: half-tone (soft 6-8 mm edge), core (2-4 mm cut), crease core.
    half = smoothstep(0.14, 0.22, shadow)
    core = smoothstep(0.32, 0.40, shadow)
    crease = smoothstep(0.70, 0.55, ao_s)
    if st["bands"] < 1.0:  # Telltale: a single hard-edged shadow shape
        half, core = smoothstep(0.24, 0.28, shadow), smoothstep(0.24, 0.28, shadow) * 0.5
    mul = 1.0 + (HALF_MUL - 1.0) * half[:, None]
    mul = mul * (1.0 + (CORE_MUL / HALF_MUL - 1.0) * core[:, None])
    mul = mul * (1.0 + (CREASE_MUL / CORE_MUL - 1.0) * (crease * (0.5 + 0.5 * core))[:, None])
    colour = colour * mul

    # --- Brush grit and a faint 10 cm "stain" patching; skin stays smooth.
    mottle = (fbm(P, 45.0, 3, seed=1) - 0.5) * 0.06
    stain = (fbm(P, 10.0, 2, seed=3) - 0.5) * 0.03
    colour = colour * (1.0 + mottle[:, None]) * (1.0 + stain[:, None] * np.array([1.0, 0.6, -0.4], np.float32))

    # --- Warmth zones at ~30%: cheeks, nose tip/ala, ears, knuckles, elbows, knees, heels.
    ox, up, fwd, eye_d = eye_frame(P, lm)
    cheek = np.exp(-(((ox - 0.012) / 0.022) ** 2 + ((up + 0.035) / 0.015) ** 2)) * (fwd < 0.0)
    nose = np.exp(-np.sum((P - lm.nose_tip) ** 2, axis=1) / (0.012 ** 2))
    warm = 0.9 * cheek + 1.0 * nose + 0.9 * smoothstep(0.2, 0.8, ears_f) + 0.8 * smoothstep(0.1, 0.6, nails_f)
    for joint, radius in (("lowerarm", 0.045), ("calf", 0.055), ("hand", 0.05), ("ball", 0.05)):
        for side in ("l", "r"):
            warm += 0.8 * np.exp(-np.sum((P - lm.bone_head[f"{joint}_{side}"]) ** 2, axis=1) / radius ** 2)
    colour = blend(colour, WARMTH * luminance(colour)[:, None] / max(luminance(WARMTH[None])[0], 1e-3),
                   np.clip(warm, 0, 1) * st["warmth"])

    # --- Under-eye: x0.85, slightly purple, 8 mm tall ellipse.
    under = np.exp(-((ox / 0.018) ** 2 + ((up + 0.014) / 0.006) ** 2)) * (fwd < 0.005)
    colour = colour * (1.0 + (UNDER_EYE_MUL - 1.0) * under[:, None])
    # Socket: x0.85 inside 1.3 x radius.
    socket = smoothstep(1.4 * lm.eye_radius, 1.1 * lm.eye_radius, eye_d) * (fwd < 0.01)
    colour = colour * (1.0 - 0.15 * socket[:, None])

    # --- Placed highlights (+12% L, hue +3), 60% opacity.
    hi = np.zeros(len(P), np.float32)
    nose_bridge = smoothstep(0.004, 0.002, np.abs(P[:, 0])) * smoothstep(lm.nose_tip[2], lm.nose_tip[2] + 0.008, P[:, 2]) \
        * smoothstep(lm.eye_l[2] + 0.008, lm.eye_l[2] - 0.002, P[:, 2]) * smoothstep(-0.3, -0.7, N[:, 1])
    cheekbone = np.exp(-(((ox - 0.016) / 0.012) ** 2 + ((up + 0.022) / 0.007) ** 2)) * (fwd < 0.0)
    brow_top = np.exp(-((up - 1.85 * lm.eye_radius) / 0.004) ** 2) * np.exp(-((ox - 0.2 * lm.eye_radius) / (1.1 * lm.eye_radius)) ** 2) \
        * (fwd < 0.0)
    chin_ball = np.exp(-np.sum((P - lm.chin) ** 2, axis=1) / 0.009 ** 2)
    hi = np.maximum.reduce([nose_bridge, cheekbone, brow_top, chin_ball * 0.8])
    for bone, radius in (("clavicle_l", 0.03), ("clavicle_r", 0.03), ("upperarm_l", 0.045), ("upperarm_r", 0.045)):
        hi = np.maximum(hi, np.exp(-np.sum((P - lm.bone_head[bone]) ** 2, axis=1) / radius ** 2) * smoothstep(0.3, 0.7, N[:, 2]))
    hi = hi * (1.0 - core) * 0.6
    colour = colour * (1.0 + (HIGHLIGHT_MUL - 1.0) * hi[:, None])

    # --- Lips: 85%, upper lip x0.85, pale catch on the lower ridge.
    lips = smoothstep(0.35, 0.6, lips_f)
    lip_col = np.where((P[:, 2] > 0.5 * (lm.lips_top + lm.lips_bottom))[:, None], LIPS * 0.85, LIPS)
    colour = blend(colour, lip_col * (luminance(colour)[:, None] / max(luminance(LIPS[None])[0], 1e-3)) ** 0.5, lips * 0.85)
    catch = lips * np.exp(-((P[:, 0] / 0.012) ** 2 + ((P[:, 2] - (lm.lips_bottom + 0.004)) / 0.0015) ** 2))
    colour = colour + 0.12 * catch[:, None]

    # --- Nails and nipples.
    nails = smoothstep(0.4, 0.7, nails_f)
    colour = blend(colour, colour * np.array([1.08, 1.0, 1.0], np.float32) + 0.08, nails * 0.8)
    if male:
        colour = blend(colour, WARMTH * 0.8, smoothstep(0.15, 0.5, nipple_f) * 0.5)

    skin_colour = colour.copy()

    # --- Hair: brows and scalp clumps (only when the head is not kept plain).
    if PLAIN_HEAD:
        hair = np.zeros(len(P), np.float32)
        brow_ink = scalp_ink = hair
    else:
        brow_fill, brow_ink = paint_brows(P, N, lm, male)
        scalp_d, scalp_ink, scalp_val = paint_scalp(P, N, scalp_f)
        hair = np.clip(np.maximum(brow_fill * 0.85, scalp_d), 0.0, 1.0)
        hair_col = HAIR[None, :] * np.where(scalp_d[:, None] > brow_fill[:, None], scalp_val[:, None], 1.0)
        colour = blend(colour, hair_col, hair)

    # --- Underwear: painted cloth, darker in shadow, waistband, stitch dashes.
    under_m, edge_dist, waistband = underwear_mask(P, lm, female=not male)
    cloth = np.repeat(UNDERWEAR[None, :], len(P), axis=0)
    cloth = cloth * (1.0 - 0.45 * smoothstep(0.08, 0.35, shadow)[:, None])
    cloth = cloth * (1.0 + (noise(P, (60.0, 60.0, 220.0), seed=41)[:, None] - 0.5) * 0.2)
    cloth = cloth + 0.06 * waistband[:, None]
    stitch_line = smoothstep(0.0012, 0.0004, np.abs(edge_dist - 0.006))
    dashes = np.sin(np.einsum("ij,j->i", P, np.array([700.0, 300.0, 120.0], np.float32))) > 0.2
    cloth = cloth + 0.2 * (stitch_line * dashes)[:, None]
    colour = blend(colour, cloth, under_m)

    # --- Ink. Occlusion-driven hierarchy, gated to the shadow side, then landmark lines.
    thick = smoothstep(0.62, 0.50, ao_s)
    medium = smoothstep(0.76, 0.64, ao_s) * 0.9 * broken(P, 90.0, 0.75, 61)
    thin = smoothstep(0.84, 0.76, ao_s) * st["thin_alpha"] * broken(P, 90.0, 0.65, 62)
    edge = smoothstep(0.46, 0.42, point) * 0.7
    if st["uniform_lines"]:
        medium, thin = smoothstep(0.76, 0.66, ao_s) * broken(P, 90.0, 0.8, 61), np.zeros_like(thick)
    ao_ink = np.maximum.reduce([thick, medium, thin, edge])
    ao_ink *= 0.35 + 0.65 * smoothstep(-0.1, 0.4, shadow + 0.3 * (1.0 - ao_s))
    # Clean fields: forehead, cheek plateau, nose top, tops of shoulders/pecs stay open.
    forehead = (up > 1.9 * lm.eye_radius) * (np.abs(P[:, 0]) < 0.045) * (fwd < 0.01) * (P[:, 2] < lm.eye_l[2] + 0.07)
    ao_ink *= 1.0 - 0.8 * forehead - 0.6 * cheek
    ao_ink *= smoothstep(1.3 * lm.eye_radius, 2.0 * lm.eye_radius, eye_d)  # socket: only lid lines
    ao_ink *= 1.0 - hair  # hair has its own ink
    ink = ao_ink
    ink = np.maximum(ink, face_ink(P, N, lm, st) * (0.75 if PLAIN_HEAD else 1.0))
    ink = np.maximum(ink, brow_ink)
    ink = np.maximum(ink, scalp_ink * 0.9)
    ink = np.maximum(ink, smoothstep(0.15, 0.0, np.abs(nails_f - 0.5)) * 0.5)
    ink = np.maximum(ink, smoothstep(0.0015, 0.0005, edge_dist) * (P[:, 2] > 0.6) * 0.9)
    # Nipple: shadow-side crescent only.
    if male:
        nip_edge = smoothstep(0.12, 0.0, np.abs(nipple_f - 0.3)) * smoothstep(0.1, 0.4, shadow + 0.3 * (1 - ao_m))
        ink = np.maximum(ink, nip_edge * 0.7)
    hatch = hatching(P, axis, shadow, ao_s, ao_m, head_f, st) * (1.0 - hair) * (1.0 - under_m)
    if PLAIN_HEAD:
        hatch *= 1.0 - smoothstep(0.3, 0.6, head_f)
    hatch_area = float((hatch > 0.3).mean())
    if hatch_area > 0.05:
        hatch *= 0.05 / hatch_area
    ink = np.clip(np.maximum(ink * st["ink"], hatch), 0.0, 1.0)
    colour = blend(colour, ink_col, ink)

    beard_mass, beard_strokes = (paint_beard(P, N, lm, lips_f) if male else (None, None))
    beard = None
    if male:
        beard = np.clip(np.maximum(beard_mass * 0.88, beard_strokes), 0.0, 1.0)

    # --- Mouth cavity: the skin faces behind the lip line (inside of the lips, roof and floor of
    # the mouth) are the dark inside, not skin, with a 2-3 mm soft edge at the inner lip; they
    # are left out of the skin tint mask. The face looks down -Y, so "behind" is larger y.
    cavity_top = min(lm.lips_top + 0.015, lm.nose_tip[2] - 0.012)
    cavity = smoothstep(0.0, 0.003, P[:, 1] - (lm.mouth[1] + 0.004)) \
        * smoothstep(0.04, 0.034, np.abs(P[:, 0])) \
        * smoothstep(lm.lips_bottom - 0.02, lm.lips_bottom - 0.016, P[:, 2]) * smoothstep(cavity_top, cavity_top - 0.004, P[:, 2]) \
        * smoothstep(-0.3, 0.0, N[:, 1]) * smoothstep(0.3, 0.6, head_f)
    colour = blend(colour, THROAT, cavity)

    skin_mask = np.clip((1.0 - hair) * (1.0 - under_m) * (1.0 - ink) * (1.0 - cavity), 0.0, 1.0)
    return np.clip(colour, 0.0, 1.0), skin_mask, hair, under_m, beard


# ---------------------------------------------------------------------------------------------
# Eyes


def paint_eyes(maps, lm):
    P = maps["pos"]
    left = P[:, 0] > 0
    centre = np.where(left[:, None], lm.eye_l, lm.eye_r)
    d = P - centre
    d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-6)
    angle = np.degrees(np.arccos(np.clip(-d[:, 1], -1.0, 1.0)))  # 0 = straight ahead

    colour = np.repeat(SCLERA[None, :], len(P), axis=0)
    colour = colour * (1.0 - 0.4 * smoothstep(0.15, 0.55, d[:, 2])[:, None])  # under the upper lid
    caruncle = smoothstep(40.0, 60.0, angle) * (d[:, 0] * np.where(left, -1, 1) > 0.3)
    colour = blend(colour, hex_col("#B0705C"), caruncle * 0.6)

    iris_r, pupil_r = 25.0, 10.5
    iris = smoothstep(iris_r + 0.6, iris_r - 0.6, angle)
    swirl = np.arctan2(d[:, 2], d[:, 0])
    spokes = 0.5 + 0.5 * np.cos(swirl * 7.0 + noise(np.stack([swirl, angle * 0.05, np.zeros_like(angle)], 1), 1.0, 61) * 3.0)
    spokes = smoothstep(0.55, 0.9, spokes) * smoothstep(pupil_r, iris_r * 0.7, angle)
    iris_col = IRIS[None, :] * (1.0 - 0.3 * spokes[:, None])
    iris_col = iris_col * (1.0 + 0.5 * smoothstep(iris_r * 0.75, pupil_r + 1.0, angle)[:, None])  # lighter inner ring
    iris_col = iris_col * (1.0 - 0.4 * smoothstep(0.1, 0.8, d[:, 2])[:, None])
    colour = blend(colour, iris_col, iris)
    colour = blend(colour, INK, smoothstep(1.6, 0.0, np.abs(angle - iris_r)) * 0.8)   # limbal ring 0.8 mm
    colour = blend(colour, hex_col("#080505"), smoothstep(pupil_r + 0.5, pupil_r - 0.5, angle))
    rim = smoothstep(2.0, 0.0, np.abs(angle - (iris_r - 2.0))) * smoothstep(-0.2, -0.8, d[:, 2]) * 0.35
    colour = colour + rim[:, None] * 0.25
    hl_dir = np.array([-0.3, -0.9, 0.32], np.float32)
    hl_dir /= np.linalg.norm(hl_dir)
    hl_dir = hl_dir * np.array([np.where(left, 1.0, -1.0)[0] if False else 1.0, 1.0, 1.0], np.float32)
    highlight = smoothstep(np.cos(np.radians(4.2)), np.cos(np.radians(3.4)), np.einsum("ij,j->i", d, hl_dir))
    colour = blend(colour, EYE_HIGHLIGHT, highlight)
    return np.clip(colour, 0.0, 1.0), iris * (1.0 - highlight)
