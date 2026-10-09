"""
Fits a Tripo-generated hair piece (unrigged OBJ / GLB / FBX, opaque clump mesh) onto a player
base body and exports it as a skeletal mesh rigged rigidly to the `head` bone.

    python3 Tools/Blender/fit_hair.py <hair.obj> <Female|Male> [--name SK_Hair_Test] [options]

Steps (see Docs/Art/Attachment_Pipeline.md and Docs/Art/Next_Steps_Research.md section 1.2):
    1. Scale the hair to the scalp bounding box (mask_scalp vertex group) and place it on the
       skull: centred, top of the hair --thickness above the top of the scalp.
    2. Shrinkwrap "Above Surface" (Nearest Surface Point, 3 mm) on the underside vertices only
       (normals facing the skull); any vertex still inside the head is pushed out.
    3. One vertex group `head` with every vertex at 1.0 (rigid), Armature modifier on the
       body's armature.
    4. Only the Head_* slider shape keys are transferred (nearest-face sampling of the body's
       deltas), so the hair follows the head-shape sliders but not the face sliders or Exp_*.
    5. HideMask vertex colour of zeros, placeholder material M_Hair, FBX + .blend, verify.

The base-body .blend is opened read-only: this script never saves it.
"""

import argparse
import os
import sys

import bpy  # noqa: E402  (bpy must load before bmesh)
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fit_garment as fg  # noqa: E402

HAIR_KEY_PREFIXES = ("Head_",)


def scalp_bbox(body, surface, min_weight=0.5):
    group = body.vertex_groups["mask_scalp"].index
    idx = [v.index for v in body.data.vertices if any(g.group == group and g.weight > min_weight for g in v.groups)]
    pts = surface.coords[idx]
    return pts.min(axis=0), pts.max(axis=0)


def align_to_scalp(obj, lo, hi, thickness, scale=None):
    """Uniform scale so the hair is as wide as the scalp plus the clump thickness on each
    side; centred over the skull; top of the hair `thickness` above the top of the scalp."""
    pts = np.array([v.co[:] for v in obj.data.vertices])
    glo, ghi = pts.min(axis=0), pts.max(axis=0)
    if scale is None:
        scale = (hi[0] - lo[0] + 2 * thickness) / max(ghi[0] - glo[0], 1e-6)
    fg.transform_mesh(obj, Matrix.Scale(scale, 4))
    pts = np.array([v.co[:] for v in obj.data.vertices])
    glo, ghi = pts.min(axis=0), pts.max(axis=0)
    move = Vector(((lo[0] + hi[0]) / 2 - (glo[0] + ghi[0]) / 2,
                   (lo[1] + hi[1]) / 2 - (glo[1] + ghi[1]) / 2,
                   hi[2] + thickness - ghi[2]))
    fg.transform_mesh(obj, Matrix.Translation(move))
    print(f"Aligned to scalp: scale x{scale:.3f}, moved {tuple(round(m, 3) for m in move)} m")
    return scale


def underside_vertices(obj, surface, skull_centre, max_dist):
    """Vertices whose normal faces the skull and that are within max_dist of the skin: the
    inner wall of a closed hair volume, or the inside of a cap."""
    result = []
    for v in obj.data.vertices:
        to_skull = (Vector(skull_centre) - v.co)
        if to_skull.length > 1e-6 and v.normal.dot(to_skull.normalized()) > 0.3 \
                and surface.signed_distance(v.co) < max_dist:
            result.append(v.index)
    return result


def rigid_head_weights(obj):
    for g in list(obj.vertex_groups):
        obj.vertex_groups.remove(g)
    fg.make_group(obj, "head", range(len(obj.data.vertices)), 1.0)
    print(f"Weights: all {len(obj.data.vertices)} verts 1.0 to head")


def parse(argv):
    ap = argparse.ArgumentParser(description="Fit a hair piece onto a base body")
    ap.add_argument("hair", help="OBJ / GLB / FBX from Tripo (unrigged, no head)")
    ap.add_argument("body", help="Female or Male")
    ap.add_argument("--name", default=None, help="Mesh / file name, e.g. SK_Hair_Test")
    ap.add_argument("--out", default=fg.TEST_DIR, help="Output folder (default Art/Characters/Test)")
    ap.add_argument("--offset", type=float, default=0.003, help="Shrinkwrap offset above the scalp (m)")
    ap.add_argument("--thickness", type=float, default=0.015, help="Expected clump thickness used for the scalp fit (m)")
    ap.add_argument("--contact", type=float, default=0.03, help="Underside verts closer than this to the skin are wrapped")
    ap.add_argument("--scale", type=float, default=None, help="Force this uniform scale instead of the scalp fit")
    ap.add_argument("--no-align", action="store_true", help="Hair is already placed on the head")
    ap.add_argument("--colour", default="0.35,0.2,0.1", help="Placeholder material colour r,g,b")
    ap.add_argument("--no-verify", action="store_true")
    return ap.parse_args(argv)


def fit_hair(args):
    name = args.name or f"SK_{os.path.splitext(os.path.basename(args.hair))[0]}"
    body, armature = fg.open_body(args.body)
    bones = fg.bone_names(armature)
    surface = fg.BodySurface(body)
    hair = fg.import_mesh(os.path.abspath(args.hair), name)

    lo, hi = scalp_bbox(body, surface)
    if not args.no_align:
        align_to_scalp(hair, lo, hi, args.thickness, args.scale)
    fg.fix_normals(hair, surface)
    skull_centre = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2] + 0.35 * (hi[2] - lo[2]))
    underside = underside_vertices(hair, surface, skull_centre, args.contact)
    if underside:
        fg.make_group(hair, "contact", underside)
        fg.shrinkwrap(hair, body, "contact", args.offset, 'ABOVE_SURFACE')
        hair.vertex_groups.remove(hair.vertex_groups["contact"])
    pushed = fg.push_out_of_body(hair, body, surface, args.offset)
    print(f"Shrinkwrap: {len(underside)} underside verts at {args.offset * 1000:.0f} mm, {pushed} extra verts pushed out")

    rigid_head_weights(hair)
    keys = fg.slider_key_names(body, HAIR_KEY_PREFIXES)
    fg.transfer_shape_keys(hair, body, surface, keys)
    fg.add_hide_mask(hair)
    colour = tuple(float(c) for c in args.colour.split(",")) + (1.0,)
    fg.placeholder_material(hair, "M_Hair", colour)
    fg.parent_to_armature(hair, armature)

    fbx_path = os.path.join(args.out, name + ".fbx")
    blend_path = os.path.join(args.out, name + ".blend")
    fg.export_fbx(hair, armature, fbx_path)
    fg.save_blend(blend_path)
    ok = True
    if not args.no_verify:
        ok = fg.verify_fbx(fbx_path, body, surface, bones, keys)
    tris = sum(len(p.vertices) - 2 for p in hair.data.polygons)
    print(f"Done: {name} ({tris} tris), morphs {keys}, verified: {ok}")
    return ok


def main():
    if not fit_hair(parse(fg.script_args())):
        sys.exit(1)


if __name__ == "__main__":
    main()
