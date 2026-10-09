# Player base bodies

| File | What it is |
|---|---|
| `SK_BaseBody_Male.fbx` / `SK_BaseBody_Female.fbx` | Skeletal meshes to import into Unreal |
| `BaseBody_Male.blend` / `BaseBody_Female.blend` | Editable Blender scenes |
| `Previews/` | Renders: front, 3/4, bend test, hide-mask regions, face sliders, face animation |

Built by `Tools/Blender/build_base_body.py` from MPFB (MakeHuman for Blender). The MPFB
base mesh and targets are CC0, so they're free to use commercially. Rebuild with:

```
blender -b -P Tools/Blender/build_base_body.py            # both bodies
blender -b -P Tools/Blender/build_base_body.py -- Female  # just one
```

## Specs

- T-pose, feet at 0. Male about 1.79 m tall, female about 1.59 m.
- About 27.7k triangles each. Set up LODs in Unreal (LOD1 50%, LOD2 25%, LOD3 10%).
- 56 bones with Unreal-style names (`root`, `pelvis`, `spine_01..03`, `neck_01`, `head`,
  `clavicle_l`, `upperarm_l`, `thigh_l`, fingers...) plus three face bones parented to `head`:
  `jaw` (talking), `eye_l` and `eye_r` (eye direction). Both bodies have identical bones.
- Eyeballs, upper teeth, lower teeth and tongue are part of the mesh, each weighted 100% to one
  bone: eyeballs to `eye_l` / `eye_r`, upper teeth to `head`, lower teeth and tongue to `jaw`.
  The mouth cavity is modelled, so the jaw can open without showing the inside of the head.
- Materials: `M_Skin` (body, underwear is painted into the skin texture), `M_Eyes` (eyeballs,
  own UV square), `M_Mouth` (teeth and tongue). Placeholders until the comic-style textures are done.
- 51 face morph targets for the character creator, e.g. `Nose_Narrow` / `Nose_Wide`,
  `Jaw_Narrow` / `Jaw_Wide`, `Eyes_Small` / `Eyes_Big`, `Ears_Pointed`, `Head_Square`.
  Each pair drives one slider: negative values use the first morph, positive values the second.
- 34 expression morph targets, all named `Exp_*`, for blinking, talking and emotes (list below).
- Vertex colour `HideMask` for hiding skin under clothes: R = torso, G = arms, B = legs,
  A = feet. Underwear areas are 0 everywhere, so they're never hidden.
- The `.blend` files also keep `mask_*` vertex groups (lips, ears, scalp, nails, nipple) for the
  texture painter. They aren't bones, so they don't go into the FBX.

### Expression morphs (`Exp_*`)

Drive them 0..1. They are MPFB's "face units", so they combine well with each other and
with the customisation sliders.

| Morphs | What they do |
|---|---|
| `Exp_BlinkL`, `Exp_BlinkR` | Close the eyelids over the eyeball (blink, sleep) |
| `Exp_EyeWideL`, `Exp_EyeWideR` | Eyes wide open (surprise, fear) |
| `Exp_SquintL`, `Exp_SquintR` | Narrow the eyes (suspicion, bright light) |
| `Exp_BrowDownL/R`, `Exp_BrowUpL/R` | Whole brow down (anger) / up (surprise) |
| `Exp_BrowInnerUpL/R`, `Exp_BrowOuterUpL/R` | Inner brow up (sad, worried) / outer brow up (sceptical) |
| `Exp_MouthOpen` | Drop the lower lip, teeth and tongue; the shape-key way to open the mouth |
| `Exp_LipsPart`, `Exp_LipsPartSide` | Lips slightly apart, centre / to one side |
| `Exp_MouthSmile`, `Exp_MouthSmileWide` | Corners up / corners up and back (grin) |
| `Exp_MouthFrown`, `Exp_MouthFrownStretch` | Corners down / corners down and stretched (disgust, crying) |
| `Exp_MouthPucker`, `Exp_MouthFunnel` | Lips pursed (kiss, "oo") / lips pushed out in a funnel ("w") |
| `Exp_MouthPress`, `Exp_MouthStretch` | Lips pressed together ("m", "b", "p") / stretched wide ("ee") |
| `Exp_MouthUpperUp`, `Exp_MouthRollOut` | Upper lip raised (snarl, "f") / lips rolled outward |
| `Exp_NoseScrunch`, `Exp_NoseDown` | Nose scrunched up (disgust) / nostrils pulled down |
| `Exp_NoseFlareL/R`, `Exp_NoseSneerL/R` | Nostril flare (anger, effort) / nostril raised (sneer) |
| `Exp_NeckTense` | Neck tendons out (strain, scream) |

## Importing into Unreal

1. Import `SK_BaseBody_Male.fbx` with **Skeleton** set to *None* (it creates a new skeleton),
   with **Import Morph Targets** on, and with **Vertex Color Import Option** set to *Replace*.
2. Import `SK_BaseBody_Female.fbx` with **Skeleton** set to the skeleton the male import created.
3. To use UE5 Manny/Quinn animations, right-click an animation, choose **Retarget Animations**,
   set the source to Manny and the target to `SK_BaseBody_Male`. The bone names match, so
   auto-mapping lines them up. Manny has no `jaw` / `eye_*` bones, so retargeted animations leave
   the face alone and the face layers below sit on top.

## Talking and blinking in Unreal

Everything below is an Animation Blueprint layer applied after the body animation.

- **Jaw**: rotate the `jaw` bone with *Transform (Modify) Bone* in bone space; about 20 degrees
  around its local X axis (positive opens, negative clenches) is a fully open mouth. The chin, lower lip, lower
  teeth and tongue follow, the upper lip stays. Use the bone for "big" mouth movement (shouting,
  hit reactions, zombie bites) and the morphs for speech shapes, or just drive `Exp_MouthOpen`
  and leave the bone alone; don't do both at full strength or the mouth opens twice.
- **Blinking**: in the AnimBP, set `Exp_BlinkL` and `Exp_BlinkR` from a curve or a timeline
  (0 -> 1 -> 0 over about 0.15 s) on a timer that fires every 2-6 s with a random delay. Add
  small random `Exp_EyeWideL/R` or `Exp_SquintL/R` for liveliness.
- **Lip sync**: map visemes to the `Exp_Mouth*` morphs, e.g. "aa/ah" -> `Exp_MouthOpen`,
  "oh/oo" -> `Exp_MouthFunnel` or `Exp_MouthPucker`, "ee" -> `Exp_MouthStretch`,
  "m/b/p" -> `Exp_MouthPress`, "f/v" -> `Exp_MouthUpperUp`, rest -> `Exp_LipsPart`. Drive the
  curves with *Modify Curve* or from an audio-amplitude envelope for cheap NPC chatter
  (amplitude -> `Exp_MouthOpen`, plus a little `Exp_MouthStretch`).
- **Emotes**: blend the brow / nose / mouth morphs into poses (angry = `Exp_BrowDownL/R` +
  `Exp_NoseFlareL/R` + `Exp_MouthFrown`; happy = `Exp_MouthSmileWide` + `Exp_SquintL/R` a bit).
- **Eyes**: add two *Look At* nodes on `eye_l` and `eye_r` with the *Look at Axis* set to the
  bone's Y axis (the bones point out of the eye along their local Y; local Z is up, so a
  positive local Z rotation looks to the character's left and a positive local X rotation
  looks up) and a clamp of about 25 degrees so the eyeballs never leave the sockets. Point them at the player, the enemy being
  attacked, or a target socket in front of the head for idle wandering. The head bone can be
  given its own Look At with a smaller weight so the head turns a little after the eyes.
