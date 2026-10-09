# Facial emotes

Source of truth: `Art/Characters/Base/Emotes.json`. Preview sheets:
`Art/Characters/Base/Previews/<Body>_Emotes.png`, rendered by
`Tools/Blender/render_emotes.py` (see the docstring for the single-emote check render).
The morphs and bones themselves are described in `Art/Characters/Base/README.md`.

## What is in the JSON

```json
{
  "borderlands_default_face": { "sliders": { "Eyes_Apart": 0.3, "Jaw_AngleStrong": 0.4, ... } },
  "emotes": [
    { "name": "Rage",
      "description": "...",
      "morphs":  { "Exp_BrowDownL": 0.95, "Exp_EyeWideL": 0.6, "Exp_NeckTense": 0.9, ... },
      "jaw_deg": 18,
      "eyes":    { "converge": 0.6 } }
  ]
}
```

| Field | Meaning | Unreal target |
|---|---|---|
| `morphs` | `Exp_*` morph target name -> weight 0..1. Always a combination; no single shape at 1.0 except the two lids in `Blink` / `Asleep` / `Wink`. | Morph target curves (same names) |
| `jaw_deg` | Rotation of the `jaw` bone about its local X in degrees. Positive opens, 20 is fully open, slightly negative clenches. | *Transform (Modify) Bone* on `jaw`, bone space, Add Rotation |
| `eyes` | `{"yaw", "pitch"}` in degrees for both eyes (yaw > 0 = character's left, pitch > 0 = up; local Z then local X of `eye_l` / `eye_r`), or `{"converge": metres}` = both eyes aimed at a point that far in front of the face. | Look At on `eye_l` / `eye_r`, or a fixed rotation when no look target is active |
| `borderlands_default_face` | Not an emote. Character-creator slider preset for the base look; applied underneath every emote. | Morph targets set once by the character creator |

The sheets are rendered with the default-face sliders applied, so what you see is what a
default player character looks like. `--plain` renders the untouched mesh.

## Bringing it into Unreal

### One Pose Asset (or a curve set) per emote

The simplest thing that works: a **Pose Asset** on the body skeleton with one pose per emote.
Each pose stores the `Exp_*` curve values, the `jaw` bone rotation and, for the emotes that
have a fixed gaze, the `eye_l` / `eye_r` rotations. In the AnimBP an *Evaluate Pose* node
(or *Pose by Name* / *Pose Blender*) drives the pose weight, and that is the whole emote.

Build it with an editor script rather than by hand so it stays in sync with the JSON:

1. Import `Emotes.json` (Python editor script or a Data Table with a row struct of
   `Name`, `Morphs` (map of name -> float), `JawDeg`, `EyeYaw`, `EyePitch`, `Converge`).
2. For every row, make an animation sequence frame with the morph curves set to the weights,
   the `jaw` bone rotated `JawDeg` about its local X and the eye bones rotated, then
   *Create Pose Asset* from it. The Data Table alone is enough if you prefer to set curves
   from Blueprint (below) and keep the Pose Asset for the bones only.

Keep curve names identical to the morph names: `Exp_MouthSmileWide` in the JSON is
`Exp_MouthSmileWide` in Unreal. The FBX import with *Import Morph Targets* on already creates
these curves on the skeleton.

### Blueprint: `SetEmote(Name, BlendTime)`

A function on the character (or an `UFaceComponent`) that

1. looks up the emote row (Data Table or the loaded JSON);
2. for every `Exp_*` curve on the skeleton, sets a target weight (the row's value, or 0 when
   the emote doesn't list it) and lerps the current value toward it over `BlendTime`, 0.15 s for
   snappy emotes (Blink, visemes, Pain, Rage) and 0.25-0.3 s for mood changes (Happy, Sad,
   Bored). The AnimBP reads the lerped values with *Modify Curve* in `Blend` mode or via the
   *Property Access* `GetCurveValue` route;
3. lerps the jaw angle the same way and feeds it to a *Transform (Modify) Bone* node on `jaw`
   (bone space, rotation = `(JawDeg, 0, 0)` about its local X, *Add to Existing*);
4. lerps the eye yaw/pitch, or switches the Look At target (below).

Use an ease-in-out (`FMath::InterpEaseInOut` or a curve asset) rather than a linear lerp;
faces snap unpleasantly on a linear blend. When an emote is finished, `SetEmote("Neutral")`
returns everything to rest, including `Exp_MouthOpen` and the jaw, with the same blend.

Mouth rule from the README still applies: an emote opens the mouth with **either** the jaw bone
or `Exp_MouthOpen`, never both at full strength. The library follows this (visemes use a small
jaw angle plus a small morph; Rage / Laugh / Surprise use the bone only).

### Blink timer

Blinks run on top of whatever emote is active, so they're a separate layer:

- A timer fires every 2-6 s (random). On fire, play a 0 -> 1 -> 0 timeline over 0.15 s into
  `Exp_BlinkL` / `Exp_BlinkR`, **added** to the emote's lid values and clamped to 1.
- Skip the timer while an emote already closes the eyes (Blink, Asleep, Laugh, Cry, Pain) or
  closes one (Wink: blink only the open eye).
- Add a little life: a random 0-0.15 of `Exp_EyeWideL/R` or `Exp_SquintL/R` held for a few
  seconds, re-rolled with each blink.

### Look At

Two *Look At* nodes in the AnimBP, one for `eye_l`, one for `eye_r`, *Look at Axis* = the
bone's +Y (the bones point out of the eye along their local Y), clamp 25 degrees so the
eyeballs never leave the sockets.

- An emote with `converge` means "look at the conversation target": set the Look At target to
  the player's camera (NPCs), the current enemy (players in combat) or a socket 0.6 m in front
  of the head (idle). The eyes converge automatically because both nodes aim at the same point.
- An emote with `yaw` / `pitch` and no target (Eyes_Left, Bored, Flirty, Contempt, Tired, Sad)
  sets the Look At weight to 0 and applies the fixed rotation instead (bone space, local Z for
  yaw then local X for pitch). Lerp between the two over the same `BlendTime`.
- The `head` bone can get its own Look At at 0.3 weight so the head follows the eyes a little
  for NPC dialogue; leave it off for players (the camera is the head).

## Who uses which emote

| Group | Emotes | Notes |
|---|---|---|
| **Player emote wheel** | Happy, Laugh, Smirk, Sad, Angry, Surprise, Disgust, Confused, Wink, Determined, Flirty, Bored | Fire with `SetEmote`, hold 2-3 s, blend back to Neutral. Pair with the body emote animation of the same name; the face plays over any body animation. |
| **Player reactive** | Pain, Rage (melee/heavy attack shout), Fear (low health / grabbed), Tired (sprint exhausted), Blink | Triggered by gameplay, not the wheel. Pain blends in at 0.1 s and out over 0.5 s. |
| **NPC dialogue** | Neutral, Happy, Smirk, Sad, Cry, Angry, Fear, Surprise, Disgust, Contempt, Confused, Tired, Bored, Determined, Flirty + the four visemes | A dialogue line is `mood` (held for the line) + visemes on top. The mood supplies brows, lids and the mouth-corner shape; the viseme layer drives `Exp_MouthOpen` / `Stretch` / `Funnel` / `Press` and a small jaw angle from the audio envelope and overrides the mood's mouth morphs with an additive blend of 0.7. `Eyes_*` / `converge` come from the Look At target, not from the row. |
| **Zombies / infected** | Rage (attack, bite), Pain (hit reaction, dismember), Asleep (dormant, lying in wait; wake up = Asleep -> Rage over 0.3 s), Blink (rare, 8-15 s, for the "not quite dead" feel) | Zombies never use converge: give the Look At a target that lags the player by 0.5 s so the eyes look dead. Rage with `Exp_NeckTense` 0.9 is the signature shape; add a random 0-0.3 of `Exp_NoseSneerL/R` per zombie for variety. |
| **Shared** | Neutral, Blink, Eyes_Left/Right/Up/Down, Asleep (sleeping NPCs, downed players) | Eyes_* are reference poses for the clamp and the sheet; in game the Look At produces them. |

## Reading the sheet

Each cell of `Previews/<Body>_Emotes.png` is one emote at 300x360 with the jaw / eye settings
and the first morphs in the label. Check for: both irises moving the same way for Eyes_*,
lids not cutting through the eyeball, lips not tearing at the corners when `MouthStretch`
and `MouthUpperUp` combine, and the mouth not opening twice (jaw + `Exp_MouthOpen`). Render one
emote big with `--emote NAME` when something looks off; tune the weights in the JSON, never
in the .blend.
