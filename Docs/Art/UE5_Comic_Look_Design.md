# UE5 Comic Look: outlines, banded lighting, skin material, style switch

Target: UE 5.3-5.5, deferred renderer, Lumen optional, 20-30 characters on screen, ~3 ms budget
for the whole stylisation. Two looks: **Borderlands** (thick ink, hard 2-band) and **Telltale**
(thinner ink, 3 soft bands). Texture names follow `Tools/Blender/paint_base_body.py`.

## 1. Findings (with sources)

- **Gearbox's shipped technique** (SIGGRAPH 2010, Thibault/Cavanaugh): a Sobel filter over the *depth*
  buffer, in separate passes for world and first-person weapon; v1 drew lines on both sides of an edge
  and broke fog/DoF/bloom, v2 discards the exterior texel. Most of the "ink" in Borderlands is hand
  painted into textures; the post-process only adds silhouettes. BL3 exposes `EdgeDetectionTexelOffset`
  (line thickness) in its ini. [slides](https://en.ppt-online.org/1523232),
  [BL3 config thread](https://forums.gearboxsoftware.com/t/config-settings-for-outlines-cell-shading-and-hdr/3128406)
- **Stencil-masked outlines in UE**: Tom Looman's multi-colour outline uses `SceneTexture:CustomStencil`
  (1-255 per mesh) read in an unbound PostProcessVolume blendable; the single most common failure is the
  project setting *Custom Depth-Stencil Pass = Enabled with Stencil* not being set.
  [tomlooman.com](https://tomlooman.com/unreal-engine-outline-multi-color-post-process/),
  [soft variant](https://tomlooman.com/unreal-engine-soft-outline/),
  [mesh-local alternative](https://tomlooman.com/unreal-engine-mesh-outline-material/)
- **Parallelcube parts 1-4**: CustomDepth/Stencil basics, occlusion by comparing CustomDepth to SceneDepth,
  stencil values picking colour, "opaque zones" that suppress lines, and a camera-distance limit.
  [part 1](https://www.parallelcube.com/2017/12/05/outline-effect-part-1/),
  [part 2](https://www.parallelcube.com/2017/12/08/outline-effect-part-2/),
  [part 4 depth limit](https://www.parallelcube.com/2018/12/21/outline-effect-part-4/),
  [repo](https://github.com/Parallelcube/UE4_T-Outline)
- **Depth + normal edges**: Epic forum tutorials sample WorldNormal from the GBuffer and take the minimum
  dot product in the kernel, with blendable location *Before Tonemapping*; unlit materials have no GBuffer
  normal so they only get depth edges. A commenter's fix for far objects: scale the texel offset by depth.
  [normals tutorial](https://forums.unrealengine.com/t/tutorial-edge-detection-post-process-effect-using-world-normals/45964),
  [depth+normal feedback](https://forums.unrealengine.com/t/edge-detection-postprocess-feedbacks/95186),
  [UE3 Sobel gem](https://docs.unrealengine.com/udk/Three/DevelopmentKitGemsSobelEdgeDetection.html)
- **Epic Stylized Rendering sample**: outline lives in `M_PostProcess_Base` ("Line Render" section), masked
  by an AO SceneTexture; sample deprecated after 4.24, treat as reference only.
  [docs](https://dev.epicgames.com/documentation/en-us/unreal-engine/stylized-rendering-post-processing?application_version=4.27),
  [distance-scaled line mod](https://forums.unrealengine.com/t/cel-shading-mods-to-m_postprocess_base-from-epics-stylized-demo-project/12366)
- **kodeco cel shading / toon outlines**: post-process cel = `PostProcessInput0 / BaseColor` (lighting buffer),
  threshold, re-multiply, *Before Tonemapping*; toon outlines via inverted hull or Laplacian post-process.
  Only valid on diffuse surfaces (specular/roughness/metallic break it).
  [cel](https://www.kodeco.com/146-unreal-engine-4-cel-shading-tutorial),
  [outlines](https://www.kodeco.com/92-unreal-engine-4-toon-outlines-tutorial),
  [80.lv caveat](https://80.lv/articles/tutorial-cel-shading-with-post-process-material-in-ue4),
  [Lumen noise thread, answer: "don't use Lumen"](https://forums.unrealengine.com/t/noise-in-cel-shade-post-process-effect-with-lumen/639280)
- **Material-based toon**: MPC carries the sun direction, material does N dot L and steps it; works with Lumen
  because it never reads the lit buffer, but loses shadows/GI unless rebuilt.
  [MPC light vector](https://jesshiderue4.wordpress.com/materials/surface-material-cel-shader/),
  [strayspark overview](https://www.strayspark.studio/blog/stylized-rendering-ue5-non-photorealistic),
  [Vylocity material cel shader](https://www.youtube.com/watch?v=HDyswSWIdY0),
  [Vylocity PP outlines](https://www.youtube.com/watch?v=oOwI0QCSqXw)
- **Engine fork**: ashtonland's UE 5.0 fork adds a "Cel Shading" shading model + outline in the renderer; author
  reports shadow banding broke after UE5 renderer changes. Confirms forks rot fast.
  [repo](https://github.com/ashtonland/Unreal-Engine-Cel-Shading),
  [Treixatek](https://github.com/Treixatek/Unreal-Engine-Cel-Shading),
  [Christopher Sims plugin, claims Lumen/Nanite OK](https://github.com/miltoncandelero/ue5-toon-shader-plugin)
- **Inverted hull vs post-process**: hull doubles triangles/draw calls per character and leaks through concave
  joints; post-process is a fixed full-screen cost regardless of character count.
  [kodeco](https://www.kodeco.com/92-unreal-engine-4-toon-outlines-tutorial),
  [MRTK hull notes](https://github.com/microsoft/MixedReality-GraphicsTools-Unreal/blob/main/Docs/MeshOutlines.md)
- **Python**: `unreal.MaterialEditingLibrary` can create expressions, connect them and wire material properties;
  it cannot change project settings, and reading back node wiring is unsupported in 5.x.
  [5.4 API](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/MaterialEditingLibrary?application_version=5.4),
  [example](https://vitez.me/scripting-unreal), [limitations](https://github.com/db-lyon/ue-mcp/issues/1115)
- **UE5 interactions** (from threads + engine behaviour, not an Epic doc): Lumen adds low-frequency noise and
  temporal wobble to any band derived from the lit buffer; TSR runs *after* a Before-Tonemapping PP material, so
  thin 1-px lines get softened/ghosted unless thickness >= 1.5 px; Nanite meshes write CustomDepth/Stencil
  normally (5.1+), and VSM/Nanite shadows are fine for a material-based toon.

## 2. OUTLINE: post-process Sobel on depth + normal, stencil masked

Project setup: *Project Settings > Rendering > Postprocessing > Custom Depth-Stencil Pass = Enabled with
Stencil*. Equivalent `DefaultEngine.ini` line under `[/Script/Engine.RendererSettings]`:
`r.CustomDepth=3`. Restart the editor. Each outlined mesh: *Render CustomDepth Pass = true*, *CustomDepth
Stencil Value* = 1 (characters), 2 (zombies), 3 (props), 0 = no outline.

SceneTexture IDs (`ESceneTextureId`): SceneColor 0, **SceneDepth 1**, BaseColor 5, **WorldNormal 8**,
Roughness 11, **CustomDepth 13**, **PostProcessInput0 14**, AmbientOcclusion 21, **CustomStencil 25**.

Material `PP_ComicOutline`: Domain Post Process, Blendable Location **Before Tonemapping** (so lines are
AA'd by TSR and fogged), Output Alpha off. Parameters: `OutlineColor` (V3, 0.02,0.02,0.02),
`Thickness` (1.0-3.0 px), `DepthSensitivity` (1.0), `NormalSensitivity` (1.0), `MaxDistance` (4000 cm),
`StencilMin` (1).

Node graph, in order:
1. `ScreenPosition` (ViewportUV) -> `UV`. `ViewSize` -> `1/ViewSize` = one-texel step `Texel`.
2. Depth-scaled thickness: `SceneTexture:SceneDepth(UV).r` -> `DepthC`. `Step = Texel * Thickness *
   saturate(ReferenceDepth / DepthC)` with `ReferenceDepth` = 300 cm, clamped to [0.5, 1.0] of Thickness.
   Far objects therefore get thinner lines; the clamp keeps them from vanishing.
3. Eight offsets `(-1,-1) (0,-1) (1,-1) (-1,0) (1,0) (-1,1) (0,1) (1,1)` times `Step`, added to `UV`. Put
   this in a Material Function `MF_Sobel8` taking UV, Step, SceneTextureId so it is written once.
4. Depth Sobel: sample `SceneTexture:SceneDepth` at the 8 offsets. Gx = (TR + 2R + BR) - (TL + 2L + BL),
   Gy = (BL + 2B + BR) - (TL + 2T + TR). `DepthEdge = sqrt(Gx*Gx+Gy*Gy) / DepthC * DepthSensitivity`.
   Dividing by the centre depth makes the threshold distance-independent (Gearbox's "depth precision"
   note). Then `DepthEdge = smoothstep(0.02, 0.08, DepthEdge)`.
5. Normal Sobel: same 8 samples of `SceneTexture:WorldNormal` (ID 8). Either Sobel each channel and take
   `length`, or cheaper: `NormalEdge = 1 - min over 8 of dot(N_centre, N_i)`; multiply by
   `NormalSensitivity`, `smoothstep(0.1, 0.4, ...)`. Catches creases that depth misses (jaw, fingers).
6. Combine: `Edge = max(DepthEdge, NormalEdge)`.
7. Stencil mask: sample `SceneTexture:CustomStencil` (ID 25) at centre AND the 8 offsets; `Tagged = any
   sample >= StencilMin` (max of the 9 `step(StencilMin, s)`). Taking the max over the kernel lets the line
   straddle the silhouette; taking only the centre draws the line on the object side only (Gearbox v2,
   cleaner against fog and sky). Use centre-only by default: `Edge *= step(StencilMin, StencilC)`.
8. Distance fade: `Edge *= 1 - smoothstep(MaxDistance*0.8, MaxDistance, DepthC)`.
9. Style read: multiply `Thickness` by `MPC_ComicStyle.OutlineThicknessMul` (see section 5).
10. Output: `Lerp(SceneTexture:PostProcessInput0, OutlineColor, saturate(Edge))` -> Emissive Color.

Cost: 8 depth + 8 normal + 9 stencil fetches per pixel; ~0.6-0.9 ms at 1440p on a mid GPU. If budget is
tight drop the normal Sobel to 4 taps (cross pattern). One PP material, scene-wide, independent of character
count, which is why it is the right fit for 20-30 characters.

**Inverted-hull alternative** (per mesh, no post-process): duplicate the skeletal mesh component, material
`M_OutlineHull`: Unlit, Two Sided off, *Cull Front Faces* (or flip normals in Blender), WorldPositionOffset =
`VertexNormalWS * HullWidth * (PixelDepth / ReferenceDepth)` for screen-constant width, Emissive =
OutlineColor. Pros: no project setting, works in forward/mobile, per-character width. Cons: doubles triangles
and draw calls per character (27k tris -> 54k, x30 characters), hull pokes through armpits/knees, no
interior crease lines, interacts badly with morph targets (face sliders) unless the hull is the same mesh.
Use it only for the first-person/menu character or if the PP pass has to go on low-end; use the PP Sobel for
the crowd.

## 3. CEL / BANDED LIGHTING options

**(a) Post-process quantise (kodeco):** `Lighting = PostProcessInput0 / max(BaseColor, 0.001)` ->
desaturate -> `floor(L * Bands) / Bands` -> `* BaseColor`. Before Tonemapping, exposure disabled
(PPV: Min/Max EV100 equal) so the band thresholds do not drift. Pros: global, zero per-material work,
~0.3 ms. Cons: only correct for diffuse; specular/metal/emissive produce wrong bands; **under Lumen the
indirect term is noisy and temporally unstable, so bands crawl** (forum answer: turn Lumen off); sky,
fog and translucency get banded too unless stencil-masked; cannot be per-character.

**(b) Material-based toon (recommended):** Blueprint `BP_ComicSun` (or the level's DirectionalLight with a
tick) writes `-LightDirection`, `LightColor` and `AmbientColor` into `MPC_ComicStyle`. Surface material
(Lit, Default Lit, Roughness 1, Specular 0) computes its own bands and feeds them into **Base Color**, so
real shadows, Lumen GI and VSM still apply on top but only as a gentle multiplier:
```
NdotL   = dot(PixelNormalWS, MPC.LightDir)                      // -1..1
Half    = NdotL * 0.5 + 0.5
Bands   = MPC.Bands (2 or 3), Soft = MPC.BandSoftness (0.0 BL, 0.08 TT)
Band    = floor(Half * Bands) / (Bands - 1)                     // hard
Band    = lerp(Band, smoothstep-stepped ramp, Soft)             // or sample T_ToonRamp_BL / _TT (1D)
Lit     = lerp(ShadowTint, 1.0, Band)                           // ShadowTint ~ (0.55,0.5,0.6)
BaseColor = Albedo * Lit
```
Pros: stable under Lumen/TSR, per-material control, ~0 extra cost (a dot and a floor), works on Nanite and
skeletal meshes, bands rotate with the sun. Cons: ignores point lights (acceptable for an outdoor zombie
game; add a second MPC slot for one local "hero" light if needed); double-darkening where real shadow
overlaps the dark band, fixed by raising the shadow band's floor or setting the DirectionalLight to low
intensity with a flat skylight (Lumen then mostly supplies colour bounce, not contrast).

**(c) Engine-fork shading model:** rejected. Needs a source build for every machine and CI, every 5.x
minor breaks it (ashtonland: shadow banding already broken in 5.x), and an indie MMO cannot afford to be
pinned to one engine version for the live service.

Recommendation: **(b)** in `M_Skin`/`M_Cloth`/`M_Zombie`, with option (a) kept only as a debug PP
material, and the PP outline from section 2 on top.

## 4. SKIN MATERIAL `M_Skin` spec

Inputs: `T_Body_<Body>_BaseColor_BL` and `_TT` (sRGB, painted at default skin 0.77,0.58,0.47),
`T_Body_<Body>_Masks` (linear; R skin, G hair, B underwear), optional `T_Body_Male_Beard` (linear, white =
beard), vertex colour `HideMask` (R torso, G arms, B legs, A feet). Params: `SkinTone` (V3, default
0.77,0.58,0.47), `HairColor` (0.09,0.07,0.06), `UnderwearColor` (0.20,0.20,0.22), scalars `HideTorso`,
`HideArms`, `HideLegs`, `HideFeet` (0/1), `LineworkStrength` (1.0), `BeardAmount` (0/1).
Blend Mode **Masked**, Shading Model Default Lit, Two Sided off, Opacity Mask Clip 0.5.

Graph:
1. `Base = lerp(Tex_BL, Tex_TT, MPC.Style)` (or a StaticSwitch per instance to save a sample).
2. Tint per region: `SkinTint = Base / DefaultSkin * SkinTone`; `HairTint = Base / DefaultHair * HairColor`;
   `UnderTint = Base / DefaultUnder * UnderwearColor`. Divide-then-multiply keeps the painted mottling,
   hatching and shading; clamp the divide input with `max(Default, 0.02)`.
3. `Col = lerp(Base, SkinTint, Masks.R)`; `Col = lerp(Col, HairTint, Masks.G)`; `Col = lerp(Col, UnderTint,
   Masks.B)`. Ink is excluded from all three masks by the paint script, so ink stays ink.
4. Beard: `Col = lerp(Col, HairColor * Beard.r(0.8..1.0 shade), Beard.r * BeardAmount)`.
5. Toon bands from section 3b: `BaseColor = Col * Lit`.
6. Ink/linework: `Ink = saturate(1 - (Masks.R + Masks.G + Masks.B))` (exactly the pixels the painter
   inked); `BaseColor = lerp(Col * Lit, Col, Ink * LineworkStrength)` so ink is not lit, and
   `Roughness = 1`, `Specular = lerp(0.2, 0, Ink)` so ink never catches highlights. This emulates
   Telltale's separate matte linework texture without a second texture; if a real `T_Body_Ink` is painted
   later, swap the `Ink` input for it.
7. Opacity mask: `Hidden = max(VC.R*HideTorso, VC.G*HideArms, VC.B*HideLegs, VC.A*HideFeet)`;
   `OpacityMask = 1 - Hidden`. Underwear has HideMask 0 so it is never hidden.
8. Normal: none (flat is the point); optional `T_Body_Normal` at 0.3 strength for Telltale.
9. Enable *Use with Skeletal Mesh* and *Use with Morph Targets*.

## 5. STYLE SWITCH `MPC_ComicStyle`

Scalars: `Style` (0 = Borderlands, 1 = Telltale), `OutlineThicknessMul` (1.0 -> 2.5 px, 0.6 -> 1.5 px),
`Bands` (2 / 3), `BandSoftness` (0.0 / 0.08), `HatchStrength` (1 / 0). Vectors: `LightDir`, `LightColor`,
`ShadowTint` (BL 0.45,0.4,0.55; TT 0.6,0.58,0.62).
`BP_ComicStyleManager` (GameInstance subsystem or level BP) sets them with
`SetScalarParameterValue(MPC, "Style", v)` and the derived values in one function so they never disagree.
Materials read with `CollectionParameter` nodes: PP outline multiplies `Thickness` by `OutlineThicknessMul`;
`M_Skin` lerps `_BL`/`_TT` textures by `Style` and feeds `Bands`/`BandSoftness` into the band formula. Switching
is instant and needs no material recompile; a per-instance override is still possible because MPC values are
only multipliers.

## 6. Editor Python feasibility (`Tools/Unreal/setup_comic_look.py`)

Can be scripted with `unreal.MaterialEditingLibrary` (5.3-5.5, Python plugin enabled):
- Create `PP_ComicOutline`, `M_Skin`, `MF_Sobel8`, `MPC_ComicStyle`, material instances per body, and the
  `T_ToonRamp` textures via `AssetToolsHelpers`. `create_material_expression(mat, unreal.MaterialExpression*
  , x, y)` for every node (SceneTexture: set `scene_texture_id` with `set_editor_property`;
  Custom HLSL nodes are the easiest way to express the Sobel loop), `connect_material_expressions`,
  `connect_material_property(expr, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR / MP_BASE_COLOR /
  MP_OPACITY_MASK)`, `set_material_instance_*_parameter_value`, then `recompile_material` and save.
- Material domain / blend mode / blendable location via `set_editor_property("material_domain",
  unreal.MaterialDomain.MD_POST_PROCESS)`, `blend_mode`, `blendable_location`.
- MPC: create `MaterialParameterCollection`, set `scalar_parameters` / `vector_parameters` arrays.
- Import the textures with `AssetImportTask` (sRGB on for BaseColor, off for Masks/Beard).
Cannot (or should not) be scripted: project rendering settings, PostProcessVolume placement in each map,
per-actor CustomDepth flags on placed actors (can be set on the Character Blueprint's mesh component instead),
and visual verification. Reading an existing graph back is unsupported, so the script must create assets
fresh (delete-and-rebuild, never edit in place).

Manual steps for a non-expert:
- Edit > Plugins: enable *Python Editor Script Plugin* and *Editor Scripting Utilities*; restart.
- Project Settings > Rendering > Postprocessing: *Custom Depth-Stencil Pass* = **Enabled with Stencil**
  (or add `r.CustomDepth=3` to `Config/DefaultEngine.ini` under `[/Script/Engine.RendererSettings]`); restart.
- Project Settings > Rendering: Anti-Aliasing = TSR (default), keep *Auto Exposure* off in the PPV below.
- Run the script: Tools > Execute Python Script > `Tools/Unreal/setup_comic_look.py`.
- Drop a **PostProcessVolume** in each level, tick *Infinite Extent (Unbound)*, Post Process Materials >
  add `PP_ComicOutline` (weight 1), Exposure: Min EV100 = Max EV100 = 1 (or Metering Mode Manual).
- In `BP_PlayerCharacter` / `BP_Zombie`: select the Mesh component, Rendering > *Render CustomDepth Pass* =
  true, *CustomDepth Stencil Value* = 1 (players) / 2 (zombies); props that need ink = 3; leave terrain,
  foliage and sky at 0.
- Assign `MI_Skin_Male` / `MI_Skin_Female` to the `M_Skin` slot, `M_Eyes` to eyes; set `SkinTone` per
  character from the character creator.
- Add `BP_ComicStyleManager` to the level (or GameInstance) and point its `Sun` reference at the
  DirectionalLight; pick Style 0/1 in its details.
- Verify: viewport *Buffer Visualization > Custom Stencil* shows characters in colour; `stat gpu` shows
  PostProcessing Material < 1 ms.

## Recommended approach (10 bullets)

1. One post-process material `PP_ComicOutline`, Before Tonemapping, Sobel on SceneDepth (ID 1) and
   WorldNormal (ID 8), thickness scaled by `ReferenceDepth / SceneDepth` and clamped.
2. Mask it with CustomStencil (ID 25), centre-pixel test, values 1 players / 2 zombies / 3 props; project
   setting `r.CustomDepth=3`.
3. Keep the pass near 0.8 ms: 8-tap kernel, 4-tap normal fallback on low scalability.
4. Do not use inverted hulls for the crowd; reserve them for menus or a mobile/low path.
5. Banded lighting lives in the surface materials (option b): MPC light direction, N dot L, floor/ramp,
   bands written into BaseColor so Lumen/VSM only modulate.
6. Keep the kodeco divide-by-BaseColor PP cel shader as a debug toggle only; it fights Lumen and specular.
7. Reject engine forks/custom shading models for a live-service indie title.
8. `M_Skin`: Masked, divide-by-default-colour tinting via Masks RGB, beard lerp, ink = 1 - sum(masks),
   ink unlit and roughness 1 / specular 0, HideMask x Hide* scalars into OpacityMask.
9. `MPC_ComicStyle` drives Style, thickness multiplier, band count/softness, shadow tint, light direction;
   one Blueprint writes it so Borderlands/Telltale is a single runtime switch.
10. Script every asset with `MaterialEditingLibrary` (rebuild-from-scratch style); humans do the project
    setting, PPV, exposure lock, stencil flags on the character Blueprints and the visual check.
