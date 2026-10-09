# Player base bodies

| File | What it is |
|---|---|
| `SK_BaseBody_Male.fbx` / `SK_BaseBody_Female.fbx` | Skeletal meshes to import into Unreal |
| `BaseBody_Male.blend` / `BaseBody_Female.blend` | Editable Blender scenes |
| `Previews/` | Renders: front, 3/4, bend test, hide-mask regions, face sliders |

Built by `Tools/Blender/build_base_body.py` from MPFB (MakeHuman for Blender). The MPFB
base mesh and targets are CC0, so they're free to use commercially. Rebuild with:

```
blender -b -P Tools/Blender/build_base_body.py            # both bodies
blender -b -P Tools/Blender/build_base_body.py -- Female  # just one
```

## Specs

- T-pose, feet at 0. Male about 1.79 m tall, female about 1.59 m.
- About 27k triangles each. Set up LODs in Unreal (LOD1 50%, LOD2 25%, LOD3 10%).
- 53 bones with Unreal-style names (`root`, `pelvis`, `spine_01..03`, `neck_01`, `head`,
  `clavicle_l`, `upperarm_l`, `thigh_l`, fingers...). Both bodies have identical bones.
- Materials: `M_Skin`, `M_Eyes`, `M_Underwear`. These are placeholders until the comic-style
  textures are done.
- 51 face morph targets for the character creator, e.g. `Nose_Narrow` / `Nose_Wide`,
  `Jaw_Narrow` / `Jaw_Wide`, `Eyes_Small` / `Eyes_Big`, `Ears_Pointed`, `Head_Square`.
  Each pair drives one slider: negative values use the first morph, positive values the second.
- Vertex colour `HideMask` for hiding skin under clothes: R = torso, G = arms, B = legs,
  A = feet. Underwear areas are 0 everywhere, so they're never hidden.

## Importing into Unreal

1. Import `SK_BaseBody_Male.fbx` with **Skeleton** set to *None* (it creates a new skeleton),
   with **Import Morph Targets** on, and with **Vertex Color Import Option** set to *Replace*.
2. Import `SK_BaseBody_Female.fbx` with **Skeleton** set to the skeleton the male import created.
3. To use UE5 Manny/Quinn animations, right-click an animation, choose **Retarget Animations**,
   set the source to Manny and the target to `SK_BaseBody_Male`. The bone names match, so
   auto-mapping lines them up.
