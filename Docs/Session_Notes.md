# Session notes: 9-10 October 2026

Hand-over for the project owner. Everything below was done in one working session on branch
`claude/happy-ride-xnk3cu` (24 commits, `git log --date=short` lists them). It is written so you can
pick the project up later without the chat. Commands assume you are in the repo root; the Blender
scripts run with a normal Blender install (`blender -b -P <script> -- <args>`) or with the `bpy` pip
module (`python3 <script> -- <args>`). "Untested in Unreal" means written against the Unreal docs
but never executed inside an editor, because there is no Unreal in this environment.

## 1. What exists now

**Male and female base bodies.** `Art/Characters/Base/SK_BaseBody_Male.fbx` and
`SK_BaseBody_Female.fbx` (for Unreal) plus the editable `BaseBody_Male.blend` / `BaseBody_Female.blend`
(the texture tools open these). Built from MPFB (MakeHuman for Blender, CC0 mesh) by
`Tools/Blender/build_base_body.py`; rebuild with `blender -b -P Tools/Blender/build_base_body.py`
(add `-- Female` or `-- Male` for one body). Both are in T-pose, feet on the floor, about 27.7k triangles,
and share one 56-bone skeleton with Unreal/Manny bone names plus `jaw`, `eye_l`, `eye_r` under `head`.
Eyeballs, upper and lower teeth and the tongue are part of the mesh (teeth and tongue follow the jaw), and
the mouth cavity is closed so an open jaw does not show the inside of the head. 51 face slider morphs for a
character creator (`Nose_Narrow`/`Nose_Wide`, `Jaw_Wide`, `Head_Square`...), 34 `Exp_*` expression morphs
(blink, brows, smile, frown, pucker, funnel, press, stretch, nose, neck), a `HideMask` vertex colour for
hiding skin under clothes (R torso, G arms, B legs, A feet; underwear never hidden) and three material
slots `M_Skin`, `M_Eyes`, `M_Mouth`. Full spec and Unreal import/animation notes:
`Art/Characters/Base/README.md`. Status: done. The female was rebuilt last (new mouth UVs, see below);
the male FBX/.blend predate that change and need one rebuild (section 3).

**Texture pipeline.** Three stages, all under `Tools/Blender/`, outputs in `Art/Characters/Base/Textures/`.
(1) `paint_base_body.py` bakes the body's shape into images and paints a procedural comic skin
(`T_Body_<Body>_BaseColor_BL.png` Borderlands, `_TT.png` Telltale, `T_Body_<Body>_Masks.png`,
`T_Eyes_BaseColor.png`, `T_Eyes_Masks.png`); rules live in `comic_paint.py`, numbers in
`Docs/Art/Borderlands_Style_Guide.md`. Run `blender -b -P Tools/Blender/paint_base_body.py -- Male`
(`PAINT_RES=2048` in the environment for a quick pass; default 4096). (2) `project_reference.py` takes
the comic drawings in `Art/Characters/Base/Reference/` (`<Body>_Front.png`, `_Back.png`, `_FaceFront.png`,
`_FaceSide.png`, black background, T-pose), matches them to the mesh by silhouette (body) and facial
landmarks and eyelid openings (face), strips hair, beard and brows so the head is plain, grades the colours
to the skin palette and writes `T_Body_<Body>_BaseColor_Drawn.png` (the texture to use), the matching
`_Masks.png`, the swappable layers `T_Body_Male_Beard.png` / `T_Body_Male_Brows.png` (white = hair),
`_DrawnCoverage.png` (debug) and `Reference/*_HairMask.png` (what was stripped). Run
`blender -b -P Tools/Blender/project_reference.py -- Male`. (3) Layers: in Unreal, the skin material tints
by the mask channels and blends the beard/brow layers over the skin by their own colour and strength.
Status: procedural paint done for the male; male drawn texture produced and still being polished (work on
it was in flight, uncommitted, when these notes were written); female not run yet. How to prepare drawings
for a new character: root `README.md`, "Reference drawings for a new character".

**Emote library.** `Art/Characters/Base/Emotes.json`: 29 named face poses (Neutral, Happy, Laugh, Smirk,
Sad, Cry, Angry, Rage, Fear, Surprise, Disgust, Contempt, Pain, Confused, Tired, Wink, Determined, Flirty,
Bored, Talking_A/E/O/M, Blink, Eyes_Left/Right/Up/Down, Asleep), each a combination of `Exp_*` weights plus
a jaw angle and an eye direction, plus the `borderlands_default_face` slider preset every new character
starts from. Contact sheet: `Tools/Blender/render_emotes.py -- Female` writes
`Previews/Female_Emotes.png`; `-- Male --emote Rage` renders one emote large. `Docs/Art/Emotes.md` says
how to drive them in Unreal (Pose Asset or Data Table, `SetEmote`, blink timer, Look At) and which emotes
players, NPCs and zombies use. Status: done and reviewed on the female; male sheet not rendered.

**Mouth texture.** `T_Mouth_BaseColor.png` and `T_Mouth_Masks.png` (1024, R teeth, G tongue), painted by
`Tools/Blender/paint_mouth.py -- Female` after `build_base_body.py` gave the teeth and tongue their own UV
square. Shared by both bodies. Preview `Previews/Female_Mouth.png`. Status: done for the female; the male
mesh has to be rebuilt before the texture fits it.

**Unreal scripts.** `Tools/Unreal/setup_comic_look.py` builds, inside the editor, the Sobel outline
post-process `PP_ComicOutline`, the banded-lighting materials `M_Skin` / `M_Eyes` / `M_Mouth`, the style
switch `MPC_ComicStyle` and the material instances under `/Game/ZombieMMO/Comic/`, importing the textures
on the way. `Tools/Unreal/import_characters.py` imports both bodies (and any `Art/Characters/Test` or
`Clothing` FBX) onto one shared skeleton, assigns the instances, makes LODs and checks 56 bones / 85 morphs.
Both run from Tools > Execute Python Script. `Tools/Unreal/README.md` lists the manual editor steps and the
runtime parameters; `Docs/Art/Character_Assembly.md` is the Blueprint design (components with Leader Pose,
appearance struct, equip and hide logic, replication, save JSON, face AnimBP, character creator).
Status: written, never executed in an editor; names the author could not verify are marked `UNVERIFIED`.

**Clothes and hair fitting tools.** `Tools/Blender/fit_garment.py` (align, strip inner shell, shrinkwrap,
copy skin weights and the 51 sliders, export, verify) and `fit_hair.py` (scale to scalp, shrinkwrap the
underside, rigid `head` weights, copy the four `Head_*` sliders). `make_test_attachments.py` makes
procedural stand-ins in `Art/Characters/Test/` and (`--sheet`) the check render
`Art/Characters/Test/Previews/Fit_Test.png`. Manual: `Docs/Art/Attachment_Pipeline.md`. Status: tools
done and passing on the four test assets; no real Tripo asset has been through them yet.

**Research docs.** `Docs/Art/Studio_Pipelines_Research.md` (how Gearbox and Telltale really make the look,
with sources), `Borderlands_Style_Guide.md` (measured ink, hatching and colour rules),
`UE5_Comic_Look_Design.md` (outline and banding design, what Python can build), `Next_Steps_Research.md`
(hair/beard/brows, clothes fitting, lip sync, with recommendations). Index: `Docs/README.md`. Status: done.

## 2. Decisions made and why

- **Plain head, swappable hair, brows and beard.** Hair, brows and beard are stripped from the drawings so
  the skin texture is a bald, clean-shaven head. Hair is a separate mesh; brows and beard are single-channel
  layers the material tints. This way one skin texture serves every hairstyle, facial-hair choice and hair
  colour in the character creator instead of one texture per combination.
- **Drawings instead of procedural or Tripo texturing.** The procedural painter (`_BL`/`_TT`) was built
  first and looks tidy but generic; a Tripo texturing test (the GLBs in `ForTripo/`) did not give the inked
  look. The research (`Studio_Pipelines_Research.md`) found Gearbox saying repeatedly that every line is
  hand-drawn in the texture and "no procedural solution would provide the same quality of line work", so
  the pipeline was turned around: hand-inked (or AI-drawn) reference drawings are projected onto the mesh,
  and the procedural paint is the fallback and the shading base.
- **Borderlands look = inked textures + engine Sobel outline + banded lighting.** Again from the research:
  Borderlands is not a cel shader; the only procedural part is a screen-space edge outline. So the ink is in
  the texture, `PP_ComicOutline` adds only the silhouette, and the banding is computed in the surface
  material from a sun direction in `MPC_ComicStyle` (stable under Lumen and TSR) rather than by quantising
  the lit image or forking the engine. Borderlands/Telltale is one runtime switch.
- **Sliders (morph targets), not face bones, for customisation.** Morphs come free from MPFB, work with the
  51-slider creator, copy onto clothes, and never fight retargeted Manny animations. Only `jaw` and the eyes
  are bones, because big mouth opening and eye aiming are easier as bones (Transform Bone, Look At).
- **T-pose.** Matches the pose the reference drawings are made in (silhouette matching needs it) and the
  pose Tripo clothes are asked for; Manny animations retarget by bone name regardless.
- **2K in game, 4K master.** Textures are painted at 4096 so lines stay crisp when edited; import at 2048
  (or let Unreal downsize) for an MMO crowd.
- **Emotes as combinations.** No single morph is a "face"; each emote mixes several `Exp_*` at partial
  weights plus a small jaw angle and eye direction, so emotes blend with visemes and with each other, and
  the mouth is never opened twice (jaw bone and `Exp_MouthOpen` at full strength together).
- **Teeth and tongue get their own UV square.** In the body layout the mouth had about 0.5 texels/mm, so
  teeth were a smear. Packing the three mouth islands into their own 1024 texture gives about 6.6 texels/mm
  and one shared mouth texture for both bodies, like the eyes.

## 3. Still in progress or not done

- **Male texture polish.** `T_Body_Male_BaseColor_Drawn.png` exists but the projection (eye pins, lid ink,
  tone matching, brow/beard masks) was still being tuned; uncommitted edits to `project_reference.py`,
  `paint_base_body.py`, `comic_paint.py`, the male `.blend` and the male textures were in the working tree
  when these notes were written. Expect the male texture to change; check `git status` first.
- **Male rebuild.** The mouth-UV change and the mouth texture were only applied to the female. Run
  `build_base_body.py -- Male`, then `paint_base_body.py -- Male`, `project_reference.py -- Male`,
  `paint_mouth.py -- Male --no-preview` and `render_emotes.py -- Male` so the male gets the new mouth UVs,
  textures that match, and his own emote sheet.
- **Female drawn texture.** All four female drawings are in (`Female_Front.png` was the last one added),
  but her `FACE_LANDMARKS`, `EYE_OPENINGS`, `FACE_REGIONS` and `STRIP_REGIONS` entries in
  `project_reference.py` are not written, so the projector has not run for her; there are no
  `T_Body_Female_*` textures yet. Until then the Unreal script falls back to the procedural `_BL`.
- **Clothes and hair are test geometry only.** `Art/Characters/Test/` holds procedural stand-ins; no Tripo
  asset exists and `Art/Characters/Clothing/` does not exist yet. Brow/beard are layers; no hair mesh yet.
- **Unreal scripts never executed in an editor.** Both scripts and the Blueprint design are unverified
  against a real UE 5.3-5.5. Expect a few enum or property names to need fixing on first run.
- **Original gameplay issues not touched.** Your Unreal project is not in this repo, so nothing was done
  about: the Brute not hearing gunshots, zombies reacting to loud landings, cover-camera aiming, and the
  folder tidy-up. Those remain on your list exactly as before.


**Male status (10 October 2026).** The male is finished end to end. The `jaw` bone bug is fixed:
since the chin and lower lip are weighted to `jaw`, "the head" is now the `head` + `jaw` groups
everywhere (`paint_base_body.HEAD_GROUPS`; the chin landmark is the front of the chin ball below the
mentolabial sulcus, the profile neck pins are measured on the mesh), and the mouth cavity is painted
the mouth-texture dark and kept out of the skin mask (`comic_paint.mouth_cavity`, used by both the
procedural paint and the projection). The male was rebuilt (`build_base_body.py -- Male`: 56 bones,
85 morphs, M_Skin/M_Eyes/M_Mouth, 0 unweighted, eyes/teeth/tongue on one bone each, jaw +20 opens
cleanly), then `paint_base_body.py`, `project_reference.py` and `paint_mouth.py -- Male`.
`project_reference.py` went through two polish passes (nine render-and-measure cycles at 2048,
then the 4096 master): body outline / shoulder black masses / rim light stripped from the drawings,
HSV grading, 3D gap fill for the head composite, 3D eye exclusion for the profile, beard-zone tone
evening, no core shadow band under the chin, nape hair stripped; then, from the lead review:
*back tone* (the back's projected texels get the front's lit-skin HSV by `match_skin_tone`, the
front/back crossover is a 35 degree normal blend with a soft self-occlusion gate instead of a hard
visibility cut, and `even_skin` pulls the drawings' dark side shading and the back's hatched tone
towards the chest's lit tone at 3 cm scale, strength 0.75, ink keeps its contrast), *boxers*
(`strip_outline` no longer eats the grey garment as outline ink, the back drawing's auto hair strip
is limited to the nape polygon instead of also painting a skin blob over the dark crotch, which was
the swirl on the buttocks, `clean_cloth` reduces the garment to one flat grey plus its thin seam
lines and `even_cloth` flattens it on the texels, and the silhouette armpit anchor is found from
the hand-to-hip line so the back drawing no longer skews and the waistband is level all round),
*face ink* (`strengthen_ink` is a levels curve: gamma 2.5 below luminance 0.35, darkest pixels on
the warm black, plus a 1 px widening ring). Measured on the texels: back/front lit-skin luminance
ratio 0.998 (torso 1.009, legs 0.995, arms 0.994), hue difference 0.001, side-of-torso dip 6 %
(was 14 %), garment low-frequency std 0.004 with 94 % of the 3D briefs mask grey, shoulder front
1.2 % dark texels, neck sides 0 %, cavity leak 0. Checked on renders: eyes clean, lips natural,
teeth off-white in Rage/Laugh, no dark blotches on neck, trapezius or shoulders, under-chin band
about 1 cm, stripped hair/brow/beard invisible, lid wedge / nostrils / mouth line / nasolabial read
as ink. Textures: `T_Body_Male_*` (drawn master at 4096, procedural `_BL`/`_TT` fallbacks, masks and
layers at 2048), `T_Mouth_*`, `T_Eyes_*`. Previews: `Male_FaceDrawn.png`, `Male_BodyDrawn.png`
(front, 3/4, 3/4 back, back; the preview lights follow the camera so the back is lit like the
front), `Male_FaceLayers.png`, `Male_FaceAnim.png`, `Male_Emotes.png`. Known leftovers: a faint
stroke of the stripped sideburn at the temple in the 3/4 view, the ears come from the profile's
hair-strip fill (plain, no ear ink), the drawing's lower-lip centre tick reads as a chin cleft, the
trapezius top keeps about 7 % dark texels from the drawings' own ink, and the drawn boxers reach
about 3 cm above the 3D briefs mask top (the Masks texture follows the drawing, not the mask).
The Female rebuild, mouth texture and emote sheet from earlier still stand; her drawn texture is
still not run (item above).

## 4. What to do next on your PC

1. **Set up.** Install Blender 4.2+ with the MPFB extension, then
   `<blender>/python/bin/python -m pip install numpy Pillow`. Clone the repo and check out
   `claude/happy-ride-xnk3cu`. Put your `.uproject` folder in the repo root next to `Art/` (the Unreal
   scripts look for textures at `<project>/../Art/Characters/Base/Textures`; otherwise change
   `TEXTURE_DIR_RELATIVE` at the top of `setup_comic_look.py`).
2. **Rebuild the male** (section 3) so both bodies match, from the repo root:
   `blender -b -P Tools/Blender/build_base_body.py -- Male`
   `blender -b -P Tools/Blender/paint_base_body.py -- Male`
   `blender -b -P Tools/Blender/project_reference.py -- Male`
   `blender -b -P Tools/Blender/paint_mouth.py -- Male --no-preview`
   Optional check: `blender -b -P Tools/Blender/render_emotes.py -- Male` and look at
   `Art/Characters/Base/Previews/Male_Emotes.png`.
3. **Unreal plugins.** Edit > Plugins: enable Python Editor Script Plugin and Editor Scripting Utilities,
   restart. Project Settings > Rendering > Postprocessing: Custom Depth-Stencil Pass = Enabled with Stencil
   (or `r.CustomDepth=3` in `Config/DefaultEngine.ini` under `[/Script/Engine.RendererSettings]`), restart.
4. **Build the look.** Tools > Execute Python Script > `Tools/Unreal/setup_comic_look.py`. Open Window >
   Output Log, filter `LogPython`, read every warning. Open `PP_ComicOutline` and `M_Skin` in the Material
   Editor and confirm they compile; fix any `UNVERIFIED` name the log complains about in the script (not in
   the asset: the script rebuilds `/Game/ZombieMMO/Comic/` from scratch each run).
5. **Import the bodies.** Tools > Execute Python Script > `Tools/Unreal/import_characters.py`. The report
   must say 56 bones and 85 morph targets per body, one skeleton `SK_BaseBody_Male_Skeleton`. If an import
   is asynchronous (Interchange), run it a second time. By hand instead: import the male FBX with Skeleton
   None, Import Morph Targets on, Vertex Color Import = Replace; import the female onto his skeleton.
6. **Manual scene steps** (from `Tools/Unreal/README.md`): add a PostProcessVolume, Infinite Extent, with
   `PP_ComicOutline` weight 1 and exposure locked (Min = Max EV100); on each character mesh set Render
   CustomDepth Pass on and CustomDepth Stencil Value 1 (players), 2 (zombies), 3 (props); assign
   `MI_Skin_Male`/`MI_Skin_Female`, `MI_Eyes`, `MI_Mouth` if the import script did not; make
   `BP_ComicStyleManager` that writes the sun direction and colour into `MPC_ComicStyle` each tick.
7. **Test the look.** Drop a body in a level, check Buffer Visualization > Custom Stencil shows it, that the
   outline appears, and that `stat gpu` shows the post-process material under 1 ms. Flip the
   `MPC_ComicStyle.Style` parameter 0/1 to compare Borderlands and Telltale. Try
   `SetVectorParameterValueOnMaterials("SkinTone", ...)` and `("HairColor", ...)`, and `BeardAmount`.
8. **Animations and face.** Retarget Manny animations (right-click > Retarget Animations, target
   `SK_BaseBody_Male`; names match). Build the face layer from `Art/Characters/Base/README.md` and
   `Docs/Art/Emotes.md`: blink timer on `Exp_BlinkL/R`, two Look At nodes on `eye_l`/`eye_r` (axis +Y, clamp
   25 degrees), Transform Bone on `jaw` (about 20 degrees local X = fully open), emotes from a Data Table
   filled from `Emotes.json`. Then the character Blueprint per `Docs/Art/Character_Assembly.md`.
9. **Clothes and hair.** Generate in Tripo with the prompts in `Docs/Art/Attachment_Pipeline.md` section 1
   (T-pose, standalone item, no body, no auto-rig), export OBJ/GLB, keep under the triangle budget, then:
   `python3 Tools/Blender/fit_garment.py tripo/jacket.glb Male --slot torso --name SK_Jacket --offset 0.010 --out Art/Characters/Clothing`
   `python3 Tools/Blender/fit_garment.py tripo/jeans.glb Female --slot legs --name SK_Jeans --out Art/Characters/Clothing`
   `python3 Tools/Blender/fit_hair.py tripo/hair.glb Male --name SK_Hair_Short --out Art/Characters/Clothing`
   Each prints a verification and the `Hide*` flags for the item row; open the written `.blend` to check
   armpits, crotch and cuffs. Re-run `import_characters.py` to bring the FBX files in on the shared skeleton,
   then add rows to `DT_Items`. Set each follower's Leader Pose Component to the body mesh.
10. **Female drawn texture.** Measure her landmarks on `Reference/Female_FaceFront.png` / `_FaceSide.png`
    and add `Female` entries to the four tables in `project_reference.py` (the male entries show the
    format), then `blender -b -P Tools/Blender/project_reference.py -- Female` and re-run step 4.
11. **Lip sync.** Start with the amplitude-driven jaw (`Exp_MouthOpen` + jaw bone from the voice audio
    envelope, client-side) described in `Character_Assembly.md` section 6; bake NPC lines offline with
    Audio2Face-3D using the mapping table in `Next_Steps_Research.md` section 3.

## 5. Glossary

- **MPFB**: MakeHuman for Blender, the add-on that supplies the base human mesh and its shape library (CC0).
- **Morph target / shape key**: a stored deformation of the mesh, driven 0..1. Blender calls it a shape
  key, Unreal a morph target. Sliders and `Exp_*` expressions are both morph targets.
- **Slider**: a character-creator control; here each `Narrow/Wide`-style morph pair is one slider.
- **UV**: the 2D layout that maps the mesh surface onto a texture image. "Own UV square" = a part uses the
  whole 0..1 image for itself (eyes, mouth).
- **Texel**: one pixel of a texture; texels/mm says how sharp a painted line can be on the body.
- **Vertex colour / HideMask**: a colour stored per vertex, used here as four on/off regions for hiding
  skin under clothes.
- **Skeletal mesh / skeleton**: a mesh with bones; both bodies and every garment share one skeleton asset.
- **Leader Pose Component**: Unreal setting that makes a clothing or hair mesh copy the body's bone poses
  and morph curves instead of animating itself.
- **Retargeting**: reusing an animation made for one skeleton (Manny) on another with matching bone names.
- **AnimBP**: Animation Blueprint, where the face layer (blink, jaw, Look At, emotes) sits after the body
  animation.
- **Look At**: AnimBP node that aims a bone (eye, head) at a world point with a clamp.
- **Material instance (MI) / dynamic material instance (MID)**: a material with parameters changed per
  character (skin tone, hair colour, hide flags) without recompiling.
- **MPC (Material Parameter Collection)**: global values every material can read; `MPC_ComicStyle`
  holds the sun direction and the Borderlands/Telltale switch.
- **Post-process material**: a full-screen effect; `PP_ComicOutline` draws the black outline.
- **Sobel**: an edge-detection filter; run on depth and normals it finds silhouettes and creases.
- **Stencil (Custom Depth-Stencil)**: a per-mesh integer written to a screen buffer; the outline only draws
  where it is 1-3, so sky and terrain never get ink.
- **Banded lighting / cel shading**: light reduced to two or three flat steps instead of a smooth gradient.
- **Viseme**: a mouth shape for a speech sound; here `Talking_A/E/O/M` and the `Exp_Mouth*` morphs.
- **Shrinkwrap / Data Transfer**: Blender modifiers that pull a garment onto the body and copy the body's
  skin weights to it.
- **Tripo**: the AI text/image-to-3D service used to generate clothes and hair meshes.
- **LOD**: lower-detail copies of a mesh used at distance (50 / 25 / 10 % here).
