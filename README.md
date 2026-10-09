# ZombieMMO character art pipeline

Player bodies for ZombieMMO in a Borderlands / Telltale comic look. The bodies are built from
MPFB (MakeHuman for Blender), their textures come from hand-inked comic drawings projected onto
the mesh, and Unreal draws the outline and banded lighting on top. Everything is scripted so the
bodies and textures can be rebuilt from scratch.

## Folder layout

```
Art/Characters/Base/            the two player base bodies (see its README for bones, morphs, Unreal import)
  SK_BaseBody_<Body>.fbx        skeletal mesh + morph targets for Unreal (Male, Female)
  BaseBody_<Body>.blend         editable Blender scene the texture tools open
  Reference/                    comic reference drawings and the *_HairMask.png the projector writes
  Textures/                     painted textures (T_Body_*, T_Eyes_*), outputs of the Blender tools
  Previews/                     renders of the bodies, face sliders and face animation
  ForTripo/                     plain unrigged GLB exports used for an earlier Tripo texturing test
Docs/Art/                       research and design notes (index in Docs/README.md)
Tools/Blender/
  build_base_body.py            builds the bodies from MPFB and exports FBX + .blend
  paint_base_body.py            bakes body maps and paints the procedural comic skin and eyes
  comic_paint.py                the painting rules (colours, ink, hatching, bands); imported by the two above
  project_reference.py          projects the reference drawings onto the painted body
Tools/Unreal/
  setup_comic_look.py           builds the outline post-process, toon materials and instances in the editor
  README.md                     how to run it, the manual editor steps, runtime style switching
```

## Pipeline

Run from the repo root. The Blender scripts find the repo from their own path.

1. Build the bodies (MPFB base mesh, Unreal-style skeleton, face and expression morphs,
   HideMask vertex colours). Writes `SK_BaseBody_<Body>.fbx` and `BaseBody_<Body>.blend`.
   ```
   blender -b -P Tools/Blender/build_base_body.py            # both bodies
   blender -b -P Tools/Blender/build_base_body.py -- Female  # one body
   ```
2. Paint the procedural base textures (skin zones, ink in creases, hatching, lips, nails,
   underwear, eyes). Writes `T_Body_<Body>_BaseColor_BL.png` / `_TT.png` (Borderlands / Telltale),
   `T_Body_<Body>_Masks.png`, `T_Body_Male_Beard.png`, `T_Eyes_BaseColor.png`, `T_Eyes_Masks.png`.
   ```
   blender -b -P Tools/Blender/paint_base_body.py            # both bodies
   blender -b -P Tools/Blender/paint_base_body.py -- Male    # one body
   ```
   `PAINT_RES=2048` in the environment paints at a lower resolution for quick tests (default 4096).
3. Project the reference drawings onto the body. Writes `T_Body_<Body>_BaseColor_Drawn.png`,
   `T_Body_<Body>_DrawnCoverage.png` (debug) and the `Reference/*_HairMask.png` files.
   ```
   blender -b -P Tools/Blender/project_reference.py -- Male
   ```
4. Build the Unreal assets. This one runs inside the editor, not from a shell:
   Tools > Execute Python Script > `Tools/Unreal/setup_comic_look.py`. It imports the textures
   (preferring `_Drawn` over `_BL`), creates `PP_ComicOutline`, `M_Skin` / `M_Eyes` / `M_Mouth`,
   `MPC_ComicStyle` and the material instances under `/Game/ZombieMMO/Comic/`. The project
   settings, PostProcessVolume and stencil values are manual; `Tools/Unreal/README.md` lists them.

If the `bpy` pip module is installed instead of a Blender binary, the Blender scripts also run as
`python3 Tools/Blender/<script>.py -- <Body>`; `blender -b -P` is the tested path.

## Requirements

- Blender 4.2 or newer (or the `bpy` module of the same version) with the MPFB extension
  installed and enabled. `build_base_body.py` needs MPFB; the painting and projection tools need
  the `.blend` files it writes.
- `numpy` and `Pillow` importable from Blender's Python (`<blender>/python/bin/python -m pip
  install numpy Pillow`).
- Unreal Engine 5.3 to 5.5 with the Python Editor Script Plugin and Editor Scripting Utilities
  enabled, for `setup_comic_look.py`.

## Reference drawings for a new character

The projector takes comic-style drawings of the character and maps them onto the body. For a body
named `<Body>` (today `Male` or `Female`) put PNGs in `Art/Characters/Base/Reference/`:

| File | What to draw |
|---|---|
| `<Body>_Front.png` | Full body, front, strict T-pose (arms straight out, feet apart), underwear, feet at the bottom. Required: the whole body is coloured from it. |
| `<Body>_Back.png` | Same pose from behind. Optional: without it the back is filled with a softened copy of the front. Also supplies the back of the head. |
| `<Body>_FaceFront.png` | Head and neck, straight on, close-up, neutral expression, eyes open and looking forward. |
| `<Body>_FaceSide.png` | Head and neck in profile, facing image left (the character's left side). |

Rules that the tools depend on:

- Plain black background, nothing else in the frame. Pixels darker than 2% count as background,
  so the silhouette must be clean (no glow, no drop shadow, no text).
- The full-body drawings are matched by silhouette (head top, neck corners, hands, armpits,
  crotch, feet), so the pose has to match the T-pose of the mesh. The face drawings are matched by
  landmarks measured in pixels on the drawing: for a new body, add its entries to `FACE_LANDMARKS`
  and `EYE_OPENINGS` in `project_reference.py`, and its hair / beard / brow regions to
  `FACE_REGIONS` and `STRIP_REGIONS`.
- Hair, beard and brows are stripped from the drawings before projection and filled with the
  drawing's own skin tone, because in the game hair is a separate mesh and beard and brows are
  tintable layers. Draw them anyway (the beard and brow strokes are cut out as the layer masks),
  but keep the lips and lid lines clear of them. The stripped areas are written next to each
  drawing as `<name>_HairMask.png` so you can check what was removed.
- Every drawing is colour-graded to the skin palette, so the exact skin colour does not matter,
  but the ink does: lines that are thin and brown in the drawing come out as faint scratches.
- `Female_Sheet.png` is the combined sheet the female face and back crops were cut from; the
  projector does not read it.

## What is done and what is not

Done:

- Both bodies: mesh, skeleton with `jaw` / `eye_l` / `eye_r`, teeth and tongue, 51 face slider
  morphs, 34 `Exp_*` expression morphs, HideMask vertex colours, FBX and `.blend` files.
- Procedural painter and style guide; BL / TT textures, masks and eyes for the male.
- Male reference drawings (front, back, face front, face side) and the projected `_Drawn` texture.
  The projection itself is still being tuned (eye pins, lid ink, tone matching), so expect the
  male texture to change.

In progress or untested:

- Female textures. Her face and back drawings are in; the front T-pose drawing is still missing,
  and the projector needs `Female_Front.png` plus her landmark and region tables before it can run.
- `Tools/Unreal/setup_comic_look.py` was written against the UE 5.3-5.5 Python API docs and has
  not been run in a real editor. Enum and property names it could not verify are marked
  `UNVERIFIED`; check the Output Log and open every material after the first run.
- Hair, beard and brow assets, and clothes fitting onto the bodies: researched in
  `Docs/Art/Next_Steps_Research.md`, tools being built, nothing in the repo yet.

## Licences

- The MPFB base mesh and morph targets are CC0, so the exported bodies, morphs and textures can
  be shipped and sold.
- MPFB's code is GPL-3.0. It is only used as a build tool by `build_base_body.py` and is not
  shipped with the game or included in this repo.
- The reference drawings were generated for this project and are the project's own.
