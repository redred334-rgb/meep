# Unreal tools

`setup_comic_look.py` builds every asset for the comic look described in
`Docs/Art/UE5_Comic_Look_Design.md`: the Sobel outline post-process, the toon skin / eye /
mouth materials, the `MPC_ComicStyle` style switch and the material instances. It rebuilds
everything under `/Game/ZombieMMO/Comic/` from scratch each time it runs.

## Running it

1. Edit > Plugins: enable **Python Editor Script Plugin** and **Editor Scripting Utilities**, restart.
2. Put the Unreal project next to `Art/` (the script looks for the textures at
   `<project>/../Art/Characters/Base/Textures`; change `TEXTURE_DIR_RELATIVE` at the top of the
   script if your project lives somewhere else). Paint the textures first with
   `Tools/Blender/paint_base_body.py`; missing files are skipped with a warning.
3. Tools > **Execute Python Script** > pick `Tools/Unreal/setup_comic_look.py`.
4. Read the Output Log (Window > Output Log, filter "LogPython"): it lists what was imported,
   every warning, and the manual steps below.

What it creates:

| Asset | What it is |
|---|---|
| `Textures/T_Body_*`, `T_Eyes_*` | Imported textures. Base colour sRGB; Masks / Beard / Eyes Masks linear |
| `MPC_ComicStyle` | Style switch: `Style`, `OutlineThicknessMul`, `Bands`, `BandSoftness`, `HatchStrength`, `LightDir`, `LightColor`, `AmbientColor`, `ShadowTint` |
| `PP_ComicOutline` | Post-process material (Before Tonemapping): one Custom HLSL node, 8-tap Sobel on SceneDepth + WorldNormal, CustomStencil centre test, depth-scaled thickness, distance fade |
| `M_Skin` | Masked, Default Lit: style lerp of `_BL`/`_TT`, divide-by-default tinting via the Masks texture, beard, toon bands from the MPC sun, unlit ink, HideMask opacity |
| `M_Eyes`, `M_Mouth` | Eyes with iris tint; flat dark red-brown mouth |
| `MI_Skin_Male`, `MI_Skin_Female`, `MI_Eyes`, `MI_Mouth` | Instances with the textures assigned (`BeardAmount` 1 on the male) |

The texture import prefers `T_Body_<Body>_BaseColor_Drawn.png` over `_BL.png` when it exists;
either one lands in the `BaseColorBL` slot.

## Manual steps after the script

The script cannot change project settings or touch levels and Blueprints, so:

1. Project Settings > Rendering > Postprocessing: **Custom Depth-Stencil Pass = Enabled with
   Stencil** (or add `r.CustomDepth=3` to `Config/DefaultEngine.ini` under
   `[/Script/Engine.RendererSettings]`). Restart the editor. Without this the outline is empty.
2. Project Settings > Rendering: Anti-Aliasing = TSR (the default).
3. In every level add a **PostProcessVolume**, tick *Infinite Extent (Unbound)*, under Post
   Process Materials add `PP_ComicOutline` with weight 1, and lock exposure (Min EV100 = Max
   EV100 = 1, or Metering Mode = Manual) so the band thresholds do not drift.
4. In `BP_PlayerCharacter` / `BP_Zombie` select the Mesh component: Rendering > *Render
   CustomDepth Pass* = true, *CustomDepth Stencil Value* = **1 players, 2 zombies, 3 props**.
   Leave terrain, foliage and sky at 0 so they never get ink.
5. On the skeletal meshes assign `MI_Skin_Male` / `MI_Skin_Female` to the `M_Skin` slot,
   `MI_Eyes` to `M_Eyes`, `MI_Mouth` to `M_Mouth`.
6. Create `BP_ComicStyleManager` (see below) and drop it in the level or GameInstance.
7. Verify: viewport Buffer Visualization > Custom Stencil shows the characters in colour;
   `stat gpu` shows PostProcessing Material under 1 ms; open each material in the Material
   Editor and confirm it compiles without errors.

## Switching Borderlands / Telltale at runtime

Everything reads `MPC_ComicStyle`, so a style change is a handful of
**Set Scalar/Vector Parameter Value** (Material Parameter Collection) calls and needs no
recompile. Put them in one Blueprint function so the values never disagree:

| Parameter | Borderlands (Style 0) | Telltale (Style 1) |
|---|---|---|
| `Style` | 0 | 1 |
| `OutlineThicknessMul` | 1.0 (about 2.5 px) | 0.6 (about 1.5 px) |
| `Bands` | 2 | 3 |
| `BandSoftness` | 0.0 | 0.08 |
| `HatchStrength` | 1 | 0 |
| `ShadowTint` | 0.45, 0.40, 0.55 | 0.60, 0.58, 0.62 |

`BP_ComicStyleManager` should also write the sun every tick (or on change): `LightDir` =
negated forward vector of the DirectionalLight, `LightColor` = its colour. `M_Skin` lerps the
`_BL`/`_TT` textures by `Style` and feeds `Bands` / `BandSoftness` / `ShadowTint` / `LightDir`
into its band maths; `PP_ComicOutline` multiplies `Thickness` by `OutlineThicknessMul`.

## Per-character control from Blueprint

On the character's skeletal mesh component (after assigning the instances):

- Skin tone: `SetVectorParameterValueOnMaterials("SkinTone", colour)`. The materials divide by
  the painted default and multiply by the parameter, so the default
  (`#C4947A`, converted to linear) means "as painted" and any other colour keeps the painted
  shading, mottling and hatching.
- Hair and underwear: `HairColor`, `UnderwearColor` the same way. `BeardAmount` (scalar 0..1)
  fades the beard layer; `LineworkStrength` (0..1) fades the painted ink.
- Eye colour: `SetVectorParameterValueOnMaterials("IrisColor", colour)` on the eye slot.
- Hiding skin under clothes: `SetScalarParameterValueOnMaterials("HideTorso" | "HideArms" |
  "HideLegs" | "HideFeet", 1.0)`. These multiply the `HideMask` vertex colour (R torso, G arms,
  B legs, A feet) into the opacity mask; underwear has HideMask 0 so it is never hidden.
- For per-character overrides without touching other characters, create a dynamic material
  instance (`CreateDynamicMaterialInstance`) or use the `...OnMaterials` calls above, which do
  that for you.
- Outline per character: *CustomDepth Stencil Value* 0 turns the outline off for that mesh.

## Known limitations

- The script was written against the UE 5.3-5.5 Python API docs but has not been run here.
  Open `PP_ComicOutline` and `M_Skin` in the Material Editor after the first run and check the
  stats panel for compile errors. Property and enum names that could not be verified are marked
  `UNVERIFIED` in the script (`BlendableLocation` names after the 5.4 rename, `MaterialUsage`
  members, `CollectionParameter` property names).
- UE 5.4 renamed the blendable locations; the script falls back to *Scene Color Before Bloom*,
  the closest replacement for *Before Tonemapping*.
- Material graphs cannot be read back from Python, so the script deletes and recreates the
  whole `/Game/ZombieMMO/Comic/` folder. Do not hand-edit those assets; change the script.
- Node wiring is done through Custom HLSL nodes, so the graphs are compact but not very
  readable in the editor. The HLSL is in the `*_HLSL` constants in the script.
- The band lighting only uses the `MPC_ComicStyle` sun; point lights still light the characters
  but do not contribute to the bands. Lumen and shadows modulate on top of the bands.
- The outline only draws where CustomStencil >= `StencilMin` (centre pixel), so it never
  draws on sky, terrain or fog, and needs the project setting from step 1.
- 4096 textures: the script turns off virtual texture streaming on import so the sampler type
  stays "Color"/"Linear Color"; if you re-import by hand, do the same or change the sampler type.
- The female body has no beard texture; `MI_Skin_Female` keeps the male beard texture as the
  parent default with `BeardAmount` 0.

## Importing the characters

`import_characters.py` imports `Art/Characters/Base/SK_BaseBody_Male.fbx` and
`SK_BaseBody_Female.fbx` into `/Game/ZombieMMO/Characters/` on one shared skeleton
(`SK_BaseBody_Male_Skeleton`), with morph targets and vertex colours (the HideMask), no FBX
materials and no physics asset. It then assigns `MI_Skin_Male` / `MI_Skin_Female`, `MI_Eyes`
and `MI_Mouth` by slot name, generates LOD1-3 at 50 / 25 / 10 %, checks for 85 morph targets
and 56 bones, and prints a report to the Output Log. Any `Art/Characters/Test/*.fbx` and
`Art/Characters/Clothing/*.fbx` are imported onto the same skeleton. Run `setup_comic_look.py`
first (the instances must exist), then Tools > **Execute Python Script** >
`Tools/Unreal/import_characters.py`. Existing meshes are skipped unless `REIMPORT = True` at
the top of the script; on 5.5 the script forces the legacy FBX importer for its own imports
(`USE_INTERCHANGE = True` switches to Interchange, which is asynchronous: run it twice).

How the meshes are assembled into `BP_PlayerCharacter`, the character creator, replication,
save/load and the face AnimBP layer is in `Docs/Art/Character_Assembly.md`.
