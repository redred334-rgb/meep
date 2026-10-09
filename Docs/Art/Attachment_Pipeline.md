# Attachment pipeline: fitting Tripo clothes and hair to the base bodies

How a Tripo-generated garment or hair piece becomes a skeletal mesh that shares the base body's
skeleton, skin weights and character-creator sliders, and what the Unreal side does with it.
Background and sources: `Docs/Art/Next_Steps_Research.md` sections 1 and 2.

| Tool | Does |
|---|---|
| `Tools/Blender/fit_garment.py` | Clothes: align, strip inner shell, shrinkwrap, copy skin weights, copy the 51 slider morphs, export |
| `Tools/Blender/fit_hair.py` | Hair: scale to the scalp, shrinkwrap the underside, rigid `head` weights, copy the `Head_*` morphs, export |
| `Tools/Blender/make_test_attachments.py` | Procedural stand-ins for Tripo assets (`Art/Characters/Test/*.obj`) and the check sheet (`--sheet`) |

All three run with the bpy module (`python3 Tools/Blender/<script>.py ...`) or inside Blender
(`blender -b -P Tools/Blender/<script>.py -- ...`). They open `BaseBody_<Body>.blend` read-only
and never save it; outputs go to `Art/Characters/Test/` by default (`--out` for another folder,
e.g. `Art/Characters/Clothing` for real assets).

## 1. Getting the asset out of Tripo

- Prompt for the pose we rig in: "... **T-pose**, arms straight out to the sides" for clothes
  with sleeves; hair is pose-free. Ask for "standalone <item> only, no body, no head, centered
  at origin, upright". Hair prompt template is in the research doc, section 1.
- Export OBJ or GLB (both import; FBX works too). Do **not** use Tripo auto-rig: its skeleton
  is not ours and the scripts throw away any rig they find.
- Tripo meshes are closed volumes; the garment tool strips the inner wall itself. Scale and
  origin are arbitrary; the tools re-fit from the body's bones, so no manual placement is needed.
- Keep the triangle count in budget (section 6) before fitting: Tripo Smart Mesh retopo, or
  Blender Decimate, on the raw export. The fit does not change the vertex count (except the
  stripped shell), so what goes in is what Unreal gets.

## 2. Clothes: `fit_garment.py`

```
python3 Tools/Blender/fit_garment.py Art/Characters/Test/Test_Shirt.obj Female --slot torso --name SK_Shirt_Test
python3 Tools/Blender/fit_garment.py tripo/jacket.glb Male --slot torso --name SK_Jacket_Leather --offset 0.010 --out Art/Characters/Clothing
python3 Tools/Blender/fit_garment.py tripo/jeans.glb Female --slot legs --name SK_Jeans
python3 Tools/Blender/fit_garment.py tripo/boots.glb Male --slot feet --name SK_Boots
```

Slots: `torso` (hangs from the shoulders; scale from the shirt height), `legs` (hangs from the
waist; scale from the hip width), `feet` (stands on the floor; scale from the width of both feet).

What it does, in order (each step prints one line):

1. **Import** and join into one mesh, drop any rig, materials and transforms.
2. **Aligned** - uniform scale from one bounding-box axis of the slot's skin region (the body
   vertices whose heaviest bone is one of the slot's bones), centred on it, anchored top or
   bottom. Override with `--scale 1.02`, `--scale-axis X`, or `--no-align` if the asset is a
   crop top / long coat / single shoe that the heuristic cannot measure.
3. **Normals** flipped if they point into the body (judged on the faces farthest from the skin).
4. **Inner shell** - a face is inner when its normal points at the skin and, looking against
   that normal, there is a facing face within `--shell-gap` (15 mm) that is farther from the
   skin. Those faces are deleted; rim faces at hems stay, so a solid Tripo shirt keeps a cloth
   thickness at the cuffs. `--keep-shell` skips it.
5. **Shrinkwrap** - Nearest Surface Point, Above Surface, offset = slot default (torso 6 mm, legs
   5 mm, feet 4 mm; `--offset` for jackets, 8-15 mm) on the *contact* vertices only: within
   `--contact` (20 mm) of the skin or inside it, never on a boundary edge (hems, cuffs, collar),
   which Above Surface would otherwise snap to the body and collapse the silhouette. Then up to
   four push-out passes snap any vertex or face centre still inside the skin (hems included)
   to an increasing offset, so creases like the crotch and armpit clear the body too.
6. **Weights** - Data Transfer modifier from the body, Nearest Face Interpolated, all vertex
   groups (`mask_*` painter groups are dropped afterwards), then Limit Total 4 and Normalize All.
7. **Shape keys** - the 51 slider morphs (every body key not named `Exp_*`). For each garment
   vertex the nearest skin polygon is found once; the body's per-key delta is interpolated
   with that polygon's barycentric weights. No add-on needed. Most keys move nothing on a shirt
   (they are face sliders) but are exported anyway so every part has the same morph list.
   Expressions are not transferred to clothes.
8. **HideMask** vertex colour of zeros (the garment is never hidden), placeholder material
   `M_<Name>` (`--colour r,g,b`), parent + Armature modifier on the body's armature.
9. **HideMask coverage** - which of the body's R/G/B/A regions sit under the garment
   (vertex counted as covered when the garment surface is within 30 mm on its outer side).
   Channels covered at 50 % or more are the `Hide*` scalars to set in Unreal (section 5).
10. **Export** `<out>/<name>.fbx` (deform bones only, no leaf bones, FBX unit scale, modifiers
    not applied so the shape keys survive, vertex colours sRGB) and `<name>.blend` with the
    body for inspection.
11. **Verify** re-imports the FBX and prints bones (must be 56 and the same names), vertex and
    triangle count, unweighted vertices (must be 0), morph targets (51), samples inside the body
    (must be 0; every vertex and face centre is tested against the skin with 0.5 mm tolerance)
    and the closest sample. Any failure prints `PROBLEM:` and the script exits 1.

### What to check by hand

- Open `<name>.blend`: wireframe over the body, look at armpits, crotch, collar and cuffs for
  pinching (too many contact vertices) or floating (too few). Tune `--contact` / `--offset`.
- Pose `thigh_l` / `upperarm_l` 45 degrees in the .blend: the garment must follow without
  tearing. If the hem tears, the Tripo mesh had the inner shell welded to the outer one;
  re-run with `--shell-gap 0.03`.
- Set `Neck_Thick`, `Head_Square` to 1 on both body and garment: collars must move together.
- The printed coverage: a long-sleeved shirt should say `HideTorso=1, HideArms=1`; if arms are
  below 50 % the forearm skin stays visible under the sleeve, which is right for short sleeves.

## 3. Hair: `fit_hair.py`

```
python3 Tools/Blender/fit_hair.py Art/Characters/Test/Test_Hair.obj Female --name SK_Hair_Test
python3 Tools/Blender/fit_hair.py tripo/hair_mohawk.glb Male --name SK_Hair_Mohawk --thickness 0.03 --out Art/Characters/Clothing
```

1. **Aligned to scalp** - uniform scale so the hair is as wide as the `mask_scalp` vertex
   group plus `--thickness` (15 mm) each side, centred over the skull, top of the hair
   `--thickness` above the top of the scalp. Long hair that falls to the shoulders is wider than
   the head only by the clump thickness, so the heuristic still holds; a huge afro needs
   `--scale` or `--no-align`.
2. **Shrinkwrap** 3 mm Above Surface (`--offset`) on the **underside** vertices only: normals
   facing the skull centre and within `--contact` (30 mm) of the skin, i.e. the inner wall of
   the clump volume. The outer clumps keep their shape. Push-out passes then clear anything
   still inside the head (fringe, sideburns).
3. **Weights** - one vertex group `head`, every vertex 1.0. No other bones, so Unreal's
   `jaw`/`eye_*` animation and body sliders never pull on the hair.
4. **Shape keys** - only `Head_Narrow`, `Head_Wide`, `Head_Round`, `Head_Square` (the sliders
   that change the skull). Face sliders and `Exp_*` are deliberately left out.
5. Same HideMask / material (`M_Hair`) / export / verify as clothes (expected morphs: 4).

Hair has no inner-shell strip: the underside of a hair volume is useful (it hides the scalp
when the head sliders move) and the shrinkwrap keeps it 3 mm above the skin.

## 4. Test assets and the check sheet

```
python3 Tools/Blender/make_test_attachments.py            # Art/Characters/Test/Test_*.obj
python3 Tools/Blender/fit_garment.py Art/Characters/Test/Test_Shirt.obj       Female --slot torso --name SK_Shirt_Test
python3 Tools/Blender/fit_garment.py Art/Characters/Test/Test_Shirt_Solid.obj Female --slot torso --name SK_ShirtSolid_Test
python3 Tools/Blender/fit_garment.py Art/Characters/Test/Test_Trousers.obj    Female --slot legs  --name SK_Trousers_Test
python3 Tools/Blender/fit_hair.py    Art/Characters/Test/Test_Hair.obj        Female --name SK_Hair_Test
python3 Tools/Blender/make_test_attachments.py --sheet    # Art/Characters/Test/Previews/Fit_Test.png
```

The test OBJs are cut from the Female skin (T-shirt = torso + upper arms pushed out 8 mm with
sleeve and collar lips; trousers = hip to ankle 6 mm; hair = scalp 15 mm with clump bumps,
closed into a volume; `Test_Shirt_Solid` is the shirt as a closed volume with an inner wall
2 mm from the skin). They are exported unrigged and deliberately mis-scaled and offset
(x0.97 + 2 cm, x1.04 - 2 cm, x0.90 + 2 cm) so the alignment has to work.

The sheet re-imports the **FBX files** (what Unreal gets), drives every part from the body's
armature like a Leader Pose Component, hides the skin regions the shirt and trousers report
(HideMask R and B, as the Unreal material does) and renders: T-pose, a bent pose (thigh, calf,
upper arm and forearm 45 degrees), the head neutral, and the head with `Jaw_Wide=1,
Head_Square=1` to show the hair following the head sliders. Needs Cycles on CPU (about 40 s).

Current numbers (all four pass verification):

| Part | Tris | Morphs | Inside body | Closest to skin | Hides |
|---|---|---|---|---|---|
| SK_Shirt_Test | 3392 | 51 | 0 | 1.9 mm (hem lip) | HideTorso (torso 90 %, arms 31 %) |
| SK_ShirtSolid_Test | 3724 (3356 inner faces stripped) | 51 | 0 | 1.7 mm | HideTorso |
| SK_Trousers_Test | 3003 | 51 | 0 | 1.3 mm | HideLegs (legs 100 %) |
| SK_Hair_Test | 1500 | 4 (`Head_*`) | 0 | 1.1 mm | - |

## 5. Unreal side

Import: `Tools/Unreal/import_characters.py` already imports `Art/Characters/Test/*.fbx` and
`Art/Characters/Clothing/*.fbx` onto `SK_BaseBody_Male_Skeleton` with Import Morph Targets on
and vertex colours **Replace**. By hand: Skeleton = the body skeleton, Import Morph Targets on,
Vertex Color Import Option = Replace, no materials from the FBX.

Character blueprint:

- One `SkeletalMeshComponent` per slot: `Body` (runs the AnimBP), `Hair`, `Torso`, `Legs`,
  `Feet`, all attached to `Body`. On each part call `SetLeaderPoseComponent(Body)`: the part
  renders with the body's bone transforms and runs no animation of its own. Bone names are
  identical, so nothing to map. Switch a part to Copy Pose From Mesh only if it needs
  RigidBody/AnimDynamics (long hair, coat tails).
- Sliders: the character creator sets each slider on `Body` with `SetMorphTarget(name, value)`;
  call the same on every part component (parts without that morph ignore it). Clothes carry
  all 51 slider morphs, hair the four `Head_*` ones, so a value set everywhere always matches.
  `Exp_*` only exist on the body; the AnimBP drives them there.
- Hiding skin: the skin material (`M_Skin` from `setup_comic_look.py`) masks pixels with
  `max(VC.R*HideTorso, VC.G*HideArms, VC.B*HideLegs, VC.A*HideFeet)`. Each garment data asset
  stores the channels the fit tool printed (e.g. shirt -> `HideTorso`, jeans -> `HideLegs`,
  boots -> `HideFeet`, long-sleeved jacket -> `HideTorso` + `HideArms`). When the outfit changes,
  OR the flags of every equipped garment and push them to the body's skin material instance
  with `SetScalarParameterValue`. Underwear regions have HideMask 0, so a shirt without trousers
  never opens a hole. Hair hides nothing (the scalp hair is painted into the skin texture).
- LODs: the import script generates LOD1-3 at 50 / 25 / 10 % for every mesh in those folders;
  hair at 2 LODs is enough (full, 40 %).

## 6. Triangle budgets (LOD0, per character on screen)

| Part | Tris | Notes |
|---|---|---|
| Base body | 27.7 k | fixed, has LODs |
| Hair | 2-6 k | opaque clumps, no alpha cards |
| Torso garment (shirt, jacket) | 3-6 k | jackets at the top, tees at the bottom |
| Legs garment | 2-4 k | |
| Feet | 1-2 k per pair | |
| Dressed character total | under 45 k | an MMO crowd of 30 at LOD2 is then about 350 k |

The test assets (3.4 k / 3.0 k / 1.5 k) sit in these ranges; cut a Tripo export down with
Smart Mesh or Decimate before fitting if it is above.

## 7. Known limitations

- Alignment is a one-axis bounding-box fit; garments whose length or width is not typical for
  the slot need `--scale` / `--scale-axis` / `--no-align`. The verify step tells you when it was
  not enough (samples inside the body).
- The Shrinkwrap "Above Surface" mode snaps every contact vertex to the offset distance, so
  loose folds within `--contact` of the skin are flattened. For baggy clothes raise `--offset`
  and lower `--contact`.
- The inner-shell test can also delete legitimately hidden layers (the inside of a lapel fold
  that faces the chest within 15 mm). Lower `--shell-gap` if that matters.
- Morph transfer is nearest-polygon sampling: it is right where the garment is close to the skin
  and approximate on loose parts (a skirt hem follows the nearest thigh vertices).
- Weight transfer is Blender's nearest-face interpolation; sleeves spanning an armpit get the
  armpit's mixed weights, which is what the body does too, but very loose garments (capes) need
  hand weighting or Copy Pose with physics.
- Blender 5 creates shape keys at value 1.0 and the FBX stores that value; the tools reset it to
  0 and the verify step fails if any morph is stored non-zero (re-importing in Blender would
  show the mesh deformed; Unreal ignores it).
- Only tested with the procedural assets so far; the first real Tripo asset should be run with
  `--keep-shell` and without it, and both .blend files compared.
