# Character assembly in Unreal: BP_PlayerCharacter and the character creator

Blueprint-level design for putting the imported bodies (`Tools/Unreal/import_characters.py`),
the comic materials (`Tools/Unreal/setup_comic_look.py`) and the clothes together. Everything
here is buildable in Blueprint; no C++ required. Asset paths assume `/Game/ZombieMMO/`.

## 1. Component layout (BP_PlayerCharacter, parent: Character)

| Component | Type | Notes |
|---|---|---|
| `Mesh` (body) | SkeletalMesh | `SK_BaseBody_Male` or `_Female`, AnimBP `ABP_Player`. Render CustomDepth on, stencil 1 |
| `Hair`, `Head` (hat/mask/glasses) | SkeletalMeshComponent | Child of `Mesh` |
| `Torso`, `Legs`, `Feet`, `Hands` | SkeletalMeshComponent | Child of `Mesh` |

Construction Script (or BeginPlay), for every slot component: **Set Leader Pose Component**
(target = slot, new leader = `Mesh`), **Set Skeletal Mesh** to None (empty until equipped),
*Render CustomDepth Pass* on with the same stencil value as the body, and *Use Attach Parent
Bound* on. Leader Pose means only the body runs the AnimBP; followers copy its bones **and its
morph-target curves**, so a garment that carries the same slider / `Exp_*` shape keys deforms
with the face and body for free. Only switch a piece to *Copy Pose From Mesh* if it needs its
own AnimBP (cape / ponytail physics). Every follower must use the shared skeleton
`SK_BaseBody_Male_Skeleton`; the import script warns if one does not.

## 2. Data

### `S_CharacterAppearance` (Blueprint struct)

| Field | Type | Default |
|---|---|---|
| `Body` | Enum `E_BodyType` {Male, Female} | Male |
| `SkinTone` | LinearColor | 0.77, 0.58, 0.47 (= "as painted") |
| `HairColor` | LinearColor | 0.09, 0.07, 0.06 |
| `EyeColor` | LinearColor | 0.33, 0.14, 0.05 |
| `BrowPreset` | Name | `None` (row in `DT_Brows`, see 3) |
| `BeardPreset` | Name | `None` (row in `DT_Beards`; `BeardAmount` 0..1 lives on the row) |
| `Sliders` | Map Name -> Float | the 51 morph names -> 0..1 (see below) |
| `Equipped` | Map `E_EquipSlot` -> Name | slot -> item id (row in `DT_Items`) |

`Sliders` is keyed by **morph target name**, one entry per morph (0..1), and only non-zero
entries are stored. The creator UI shows each `Narrow/Wide`-style pair as one bipolar slider
(-1..1): negative writes the first morph, positive the second, the other one is removed from the
map. Single morphs (`Ears_Pointed`, `Head_Square`, `Head_Round`) are plain 0..1 sliders. The
expression morphs (`Exp_*`) are never stored; the AnimBP owns them.

**Borderlands default face** (the preset every new character starts from):
`Eyes_Apart 0.3`, `Jaw_AngleStrong 0.4`, `Cheekbones_High 0.3`, `Head_Square 0.2`,
`Chin_Forward 0.2`. Keep it as `DA_DefaultAppearance` (a Data Asset holding one
`S_CharacterAppearance`) so the creator's *Reset* button and new accounts read the same values.

### `E_EquipSlot`: Hair, Head, Torso, Legs, Feet, Hands.

### `DT_Items` (row struct `S_ItemRow`)

| Field | Type | Meaning |
|---|---|---|
| `Slot` | `E_EquipSlot` | which component it goes on |
| `MeshMale`, `MeshFemale` | Soft SkeletalMesh | one per body (Female may be None = unisex) |
| `Material` | Soft MaterialInterface | optional override for slot 0 |
| `HideTorso`, `HideArms`, `HideLegs`, `HideFeet` | bool | skin regions the garment covers |
| `TintsWithHair` | bool | hair / beard pieces get `HairColor` pushed in |

The body's `HideMask` vertex colour (R torso, G arms, B legs, A feet) is multiplied by the
`Hide*` scalars in `M_Skin`, so "hide skin under clothes" is just the OR of the covered regions
of everything equipped. Underwear has HideMask 0 and never disappears.

## 3. Applying an appearance (`ApplyAppearance`, in BP_PlayerCharacter)

Runs on every machine that has the struct (owner, server, simulated proxies). Order matters.

1. **Body**: `Set Skeletal Mesh` on `Mesh` to `SK_BaseBody_Male/Female` if `Body` changed
   (keep the AnimBP: *Set Anim Instance Class* after a mesh swap).
2. **Body MID**: on first run `Create Dynamic Material Instance` for the `M_Skin`, `M_Eyes`
   slots (store `SkinMID`, `EyesMID`). Then
   `SkinMID.SetVectorParameterValue("SkinTone")`, `("HairColor")`, `EyesMID.("IrisColor")`,
   `SkinMID.SetScalarParameterValue("BeardAmount", BeardRow.Amount)` (0 on Female).
3. **Sliders**: first zero the previous ones (`Clear Morph Targets`), then `ForEach Sliders:
   Set Morph Target(Name, Value)` on `Mesh`. Leader Pose forwards the curve values, so clothes
   with matching shape keys follow; no per-garment call is needed.
4. **Brow / beard presets**: rows in `DT_Brows` / `DT_Beards` are just items whose `Slot` is
   `Head` (brow card) or a texture choice; for the painted beard set `BeardAmount` only.
5. **Equipment**: for each slot component, look up `Equipped[slot]` in `DT_Items`, `Set
   Skeletal Mesh` (`MeshMale` or `MeshFemale`), re-call `Set Leader Pose Component` (a mesh
   swap keeps it, but it is cheap and safe), and if `TintsWithHair` create a MID on it and push
   `HairColor`. Empty slot = `Set Skeletal Mesh(None)`.
6. **Hide regions**: `HideTorso = OR(row.HideTorso for all equipped)`, same for Arms / Legs /
   Feet; `SkinMID.SetScalarParameterValue("HideTorso", 1 or 0)` etc.
7. Outline: body and every slot component `Set Render Custom Depth(true)`, stencil 1.

Changing one thing at runtime (creator sliders, equipping a jacket) calls the same function;
it is idempotent and a few dozen nodes, so there is no need for partial updates.

## 4. Replication (MMO-lite)

- `Appearance` (`S_CharacterAppearance`) is a **replicated variable with RepNotify** on
  BP_PlayerCharacter. The server sets it once when the pawn is possessed (from the save, see 5),
  so joining clients receive it with the actor; `OnRep_Appearance` calls `ApplyAppearance`.
  The server also calls `ApplyAppearance` itself (listen server / server-side hit boxes).
- Equip/unequip: client sends `Server_Equip(slot, item id)` (RPC, Run on Server, Reliable);
  the server validates against inventory, writes `Appearance.Equipped`, and the change goes out
  through the same RepNotify. Nothing else replicates garments.
- Morph targets and material parameters are **never replicated**; every client derives them
  from the struct. Net cost per character is the struct once plus a few bytes per equip.
- Character-creator edits are local only (a preview pawn in the creator level). On *Accept*
  the client sends `Server_SetAppearance(struct)`; the server clamps values (sliders 0..1,
  colours 0..1, item ids that exist in `DT_Items`) before storing.
- `Exp_*` and look-at are cosmetic and client-side; emotes replicate as an emote id
  (`Multicast_PlayEmote(Name)`, Unreliable), never as curve values.

## 5. Save / load (JSON string)

Serialise `S_CharacterAppearance` to one JSON string with the engine's *Json Blueprint
Utilities* plugin (`Struct to Json String` / `Json String to Struct`, UE 5.3+), or a
`JsonObjectWrapper` if you prefer. Shape:

```json
{"Body":"Male","SkinTone":[0.77,0.58,0.47,1],"HairColor":[0.09,0.07,0.06,1],
 "EyeColor":[0.33,0.14,0.05,1],"BrowPreset":"Brow_Flat","BeardPreset":"Beard_Stubble",
 "Sliders":{"Eyes_Apart":0.3,"Jaw_AngleStrong":0.4,"Cheekbones_High":0.3,"Head_Square":0.2,"Chin_Forward":0.2},
 "Equipped":{"Torso":"Jacket_Denim","Legs":"Jeans_Blue","Feet":"Boots_Work"}}
```

Store the string in the account record on the backend (or `SaveGame` for local testing).
Unknown slider or item names are ignored on load so old saves survive asset renames; missing
fields fall back to `DA_DefaultAppearance`. Version the string with a top-level `"v": 1`.

## 6. AnimBP face layer (`ABP_Player`, after the body animation)

Everything below sits at the end of the AnimGraph, after the locomotion / retargeted Manny
animations (which never touch `jaw` / `eye_*` or the `Exp_*` curves).

- **Sliders**: the 51 slider curves are set once by `Set Morph Target` in `ApplyAppearance`
  (section 3) and left alone; curves from animation are additive on top. Do not drive sliders
  in the AnimGraph.
- **Blink**: Event Graph timer, random 2-6 s (10 % chance of a double blink). On fire, play a
  Timeline 0 -> 1 (80 ms), hold 40 ms, 1 -> 0 (120 ms) into a float `Blink`; the AnimGraph
  `Modify Curve` node writes `Exp_BlinkL` and `Exp_BlinkR` from it. Add a little random
  `Exp_SquintL/R` (0..0.15) that changes every few seconds for liveliness.
- **Jaw / talking**: amplitude-driven. Bind *On Audio Single Envelope Value* on the voice
  Audio Component, `FInterpTo` it into `MouthOpen` (0..1). AnimGraph: `Modify Curve
  Exp_MouthOpen = MouthOpen * 0.6` plus `Transform (Modify) Bone` on `jaw`, bone space,
  rotation X = `MouthOpen * 12` degrees (20 degrees is fully open; morph and bone together must
  not both be at full strength). Add `Exp_MouthStretch = MouthOpen * 0.2`. NPC lines use baked
  curves (Audio2Face, see `Next_Steps_Research.md` section 3) and skip this branch.
- **Look At**: two `Look At` nodes, bones `eye_l` and `eye_r`, *Look at Axis* = +Y, *Up Axis*
  = Z, *Look at Clamp* 25 degrees, interpolation 0.1 s, target = `LookTarget` (world location
  set by the character BP: nearest player in conversation range, the enemy being attacked, or a
  socket 2 m in front of the head that wanders +-0.3 m every 1-3 s). A third `Look At` on
  `head` with the same target, clamp 30 (pitch) / 70 (yaw) and alpha 0.4 makes the head follow.
- **Emotes**: `DT_Emotes` is a Data Table (row struct `S_EmoteRow`) filled from the `emotes`
  array of `Art/Characters/Base/Emotes.json` (29 rows: Neutral, Happy, Laugh, Angry, Rage,
  Fear, Surprise, Wink, Talking_A/E/O/M, Blink, Eyes_Left/Right/Up/Down, Asleep...). Row
  fields mirror the JSON: `Morphs` Map Name -> Float (the `morphs` object, `Exp_*` weights
  0..1), `JawDeg` float (`jaw_deg`, jaw bone local X in degrees, 20 = fully open), `EyeYaw` /
  `EyePitch` floats and `EyeConverge` float (from `eyes`: either `{yaw, pitch}` or `{converge:
  metres}`; unused ones 0), plus `BlendIn` / `Hold` / `BlendOut` seconds (not in the JSON;
  default 0.15 / 1.0 / 0.3) and `Loop` bool. The JSON top level also holds
  `borderlands_default_face.sliders`, which is where `DA_DefaultAppearance` (section 2) comes
  from. Import: Content Browser > Import, pick the JSON, choose `S_EmoteRow` as the row struct
  (the engine wants a flat array of rows, so either strip the `_about` /
  `borderlands_default_face` keys into a second file or keep a small Editor Utility that copies
  `emotes` into the table). `PlayEmote(Name)` copies the row into a Map `ActiveEmote` and an
  alpha driven by a Timeline (BlendIn / Hold / BlendOut); the AnimGraph `Modify Curve` (Blend
  mode) applies `ActiveEmote` scaled by the alpha, `Transform (Modify) Bone` adds `JawDeg *
  alpha` to the jaw, and `EyeYaw` / `EyePitch` (or a `LookTarget` placed `EyeConverge` m in
  front of the face) override the look-at target while the emote is active. A full-body emote
  animation (wave, dance) plays through a montage slot in the same graph and references the
  face row by name in its notifies.
- Order in the AnimGraph: locomotion -> montage slot -> Modify Curve (blink, squint, mouth)
  -> Modify Curve (emote) -> Transform Bone jaw -> Look At eye_l -> Look At eye_r -> Look At
  head -> output.

## 7. Character creator (`WBP_CharacterCreator` + `BP_CreatorPawn`)

- The creator level has a `BP_CreatorPawn` (same components as section 1, no replication,
  camera on the face) and `PP_ComicOutline` in a PostProcessVolume so the preview matches.
- The widget owns one local `S_CharacterAppearance`. Every slider / colour / item change writes
  the struct and calls `ApplyAppearance` on the preview pawn (cheap enough to run per tick while
  dragging). Tabs: Body (Male / Female), Face (bipolar sliders grouped Head / Brows / Eyes /
  Nose / Mouth / Jaw / Chin / Ears / Neck), Skin / Hair / Eyes (colour swatches: 8 skin
  tones, 12 hair colours, 6 eye colours; store the LinearColor, not an index), Hair / Beard /
  Brows (item lists filtered by slot), Clothes (starter outfits).
- *Randomise*: each slider pair gets a value in -0.6..0.6, colours from the swatch lists.
  *Reset*: copy `DA_DefaultAppearance`. *Accept*: JSON string (section 5) -> `Server_SetAppearance`.
- Camera presets per tab (full body / head) and a 360 turntable drag on the pawn.

## 8. Checklist after building it

1. Equip a torso item: the skin under it vanishes, underwear and hands stay.
2. Slide `Jaw_Wide` to 1 while wearing a hood with shape keys: hood follows (Leader Pose curves).
3. Second client joins: sees the first player's face, hair colour and clothes without any RPC.
4. `stat anim`: followers show no AnimBP cost; only the body ticks one.
5. Blink every few seconds, eyes track the camera within 25 degrees, mouth moves with voice.
