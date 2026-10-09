# Borderlands-Style Skin Texture: Procedural Painting Guide

Scope: human skin albedo painted in numpy onto the base body (1.8 m), using baked position,
normal, pointiness, AO (12 mm and 150 mm radius) and face landmarks. Silhouette outlines come from
the engine post-process and are NOT painted. "mm" = millimetres on the 1.8 m body.
"lit" = base skin tone before any painted shading. "BL" = Borderlands 2/3/4, "TT" = Telltale.

Reference reading: BL2 render `14.png` (face at ~1.26 px/mm), BL3 cutscene `25.webp` (~2.05 px/mm),
BL4 `28.webp`, our drawing `24.webp`. Pixel colours below were sampled from these files.

What the artists say (confirmed by the images):
- Every line is hand-drawn in the texture; the only procedural step is the Sobel outline, and BL3
  also tried a "hatch-based shading" pass for hand-ink shadows ([Cook & Becker, Kester](https://www.cookandbecker.com/en/article/224/borderlands-3-art-director-scott-kester-on-communicating-an-attitude.html), [ArtStation BL3 Art Blast](https://magazine.artstation.com/2019/09/gearbox-software-borderlands-3-art-blast/), [exp-points Q&A](https://www.exp-points.com/borderlands-3-qa)).
- "Big open spaces of colour to contrast against the drawn-on lines"; structural lines thick, hatch
  lines thin and tapered, on separate layers; cross-hatch only under wrinkles; shadows on a Soft
  Light layer at ~60% ([methodj](https://www.methodj.com/borderlands-style-texture-creation/2/), [DeviantArt 7he1ndigo](https://www.deviantart.com/7he1ndigo/art/How-to-Create-Textures-for-Borderlands-556725103)).
- Vary line size and avoid long straight lines ([80.lv Gearbox artist](https://80.lv/articles/making-a-compact-gun-in-the-borderlands-style)).
- Telltale: comic ink, shadows as flat darkened shapes, almost no hatching; after the move to PBS
  (Batman onward) painted shadows left the albedo and lighting took over ([tightenupthegraphics](https://tightenupthegraphics.com/2017/11/28/telltales-move-to-pbs-shifting-technologies-and-practices/), [Telltale community](https://community.telltale.com/discussion/57236/how-to-draw-telltale-games-style)).

## 1. Ink lines

Ink colour: NOT pure black on skin. Use a warm black `#1A100C` (BL3 in-engine samples reach
`#040101` only inside the lid wedge). Thick lines at 95-100% opacity read as black; thin lines at
45-65% opacity read as the dark brown seen on BL2 cheeks (`#5F433B`, `#382722`). Never tint ink
toward the skin hue by more than this; a brown mid-tone line (our drawing: `#8B411C`) looks like
a pencil sketch, not ink.

Classes: THICK 2.5-5 mm, MEDIUM 1.5-2.5 mm, THIN 0.7-1.2 mm. All lines taper at both ends over
the last 15-25% of their length (thick to ~40% width, thin to zero). Line edges are hard (<0.5 mm
blur) on thick lines, slightly soft (0.5-1 mm) on thin. Short lines are straight-ish with a slight
hand wobble (0.3 mm noise); nothing is perfectly straight or perfectly circular.

Rule for which side: ink sits on the SHADOW/occluded side of a crease or the underside of a form
(under the brow, under the lower lip, under the jaw, under the pec). The lit top edge of a form is
clean. Never close a form with ink on both sides.

| Feature | Class | Width mm | Continuous? | Notes |
|---|---|---|---|---|
| Brow ridge (underside of brow) | medium | 2 | broken, 2 segments | only under the brow hair mass, inner 2/3 |
| Upper eyelid + lash line | THICK | 3 inner -> 5 outer | continuous | widest wedge at outer third, tapers to 1 mm past outer corner; the single heaviest line on the face |
| Lower eyelid | thin | 0.8 | broken, outer half only | plus 2-3 lash ticks 1.5 mm long at outer corner |
| Eye socket / crow's feet | thin | 0.8 | 2-3 broken strokes 6-12 mm | radiating from outer corner, only in BL; none under 30-year-old TT faces |
| Upper lid crease | thin | 1 | broken | 6-8 mm above lash line, inner half; shadow side only |
| Nose bridge sides | thin | 0.8-1 | one broken scratch 10-15 mm | shadow side only, plus 1-2 ticks at the root (frown) |
| Nostril (ala outline + nostril hole) | THICK | 2.5 | continuous | hole is a solid ink blob ~4x3 mm; ala crease ends 3 mm short of cheek |
| Nasolabial fold | medium | 1.5 | continuous, shadow side; thin broken 0.8 lit side | from ala to 8 mm past mouth corner, taper both ends |
| Upper lip line (mouth closure) | medium | 2 (centre) -> 1 (ends) | continuous | sits in the closure, pure black; cupid's bow thin 0.8 |
| Mouth corners | THICK | 2.5-3 | 4-6 mm bracket | plus 2 short thin strokes outward (BL3) |
| Lower lip underside / chin crease | medium | 1.5 | broken, centre 60% | thin ticks on chin ball shadow side |
| Jaw line | medium | 1.5-2 | broken, 2-3 segments | shadow side only, from ear to chin-side; do not run under the whole jaw |
| Ear | medium/thin | 1.5 helix inner edge, 0.8 antihelix, 2.5 ear canal blob | broken | tragus shadow side ink 1.5 |
| Neck tendons (SCM) | thin | 0.8-1 | 3-5 strokes 8-20 mm | along the tendon, on its shadow side; pit of neck gets a 2 mm U |
| Collarbones | medium | 1.5 | broken | underside only; clean on top |
| Pec line | medium | 1.5 -> 0.8 | continuous under pec, fades toward sternum | top of pec clean; sternum line thin 0.8 centre 50% |
| Abs | medium | 1.5 linea alba, 1.2 transverse | broken, 60-80% of crease | shadow side only (lower edge of each pack); avoid grid look by offsetting pairs 3-5 mm |
| Biceps / triceps | medium | 1.2 | broken | groove between bicep and tricep, lower side; deltoid-bicep gap 1.5 |
| Forearm tendons | thin | 0.8 | 2-3 strokes 15-30 mm | shadow side of the brachioradialis; wrist crease 1.2 |
| Knuckles | medium | 1.2 | 3-5 mm arcs | one arc per knuckle on the finger side, plus 2 thin creases |
| Knees | medium | 1.2 | broken arcs | under the patella (shadow), 1-2 thin ticks above |
| Shin | thin | 0.8 | one broken 60-100 mm stroke | along tibia shadow edge only |
| Ankle | medium | 1.2 | 2 short arcs | under the malleolus, Achilles groove thin |
| Nipple (male) | medium | 1.2 | crescent on shadow side | areola not outlined |

Clean zones (no ink, no hatch): forehead centre, cheek plateau, nose bridge top, chin ball top,
top of shoulders, top of pecs, outer thigh and calf surfaces, back of hand top. These open fields
are what make the lines read ([methodj](https://www.methodj.com/borderlands-style-texture-creation/2/)).

Telltale differences: lines are uniform-width (2-2.5 mm, no thick/thin hierarchy), pure black
`#000000` at 100% opacity, fewer of them (lid, nostril, mouth, jaw, nasolabial only), no crow's feet
or body hatching, and shadows are painted as flat solid shapes instead of lines. Existing
`STYLES["TT"]` (ink 0.6, hatch 0, bands 0.55) is the right direction.

## 2. Hatching

- Where: inside shadow bands only, and only within 6-15 mm of an ink crease or form underside:
  under jaw, side of neck, under pec, under deltoid, inner thigh near groin, back of knee, under
  brow at the temple, side of nose, under lower lip. Never on the lit side, never as a field texture
  over a whole muscle. BL4 (`28.webp`) has almost no visible hatching; BL3 (`25.webp` neck) has
  groups of 3-5 strokes. Budget: at most 5% of skin area.
- Direction: along the form's long axis (down the neck, along the tendon, around the limb for
  joints), i.e. following the muscle, NOT perpendicular to the terminator.
- Stroke: length 4-8 mm (face) / 10-25 mm (body), width 0.6-1.0 mm, tapered to zero both ends,
  slight curve, 0.3 mm wobble. Opacity 55-75% of ink.
- Spacing: 2.5-3.5 mm at the deepest point; density gradient: spacing grows to 5-6 mm and stroke
  length shrinks 50% over the 8-12 mm approaching the terminator, then stops. No stroke crosses the
  terminator into light.
- Cross-hatching: no on faces. Body: allowed only in the two darkest pockets (armpit, groin/under
  glute) as a second set at 60 degrees, half the density, 40% opacity.
- Groups: strokes come in clusters of 3-6 with 6-10 mm gaps between clusters, not an even comb.

## 3. Shading (painted into albedo)

- Three values: lit, half-tone, core. Measured BL2: lit cheek L0.65, shadow cheek L0.36 (x0.55),
  socket L0.30 (x0.46). BL4: lit L0.35 -> jaw shadow L0.19 (x0.54).
  - Half-tone band: luminance x0.72 of lit. Starts where large AO shadow > 0.18. Edge softness 6-8 mm.
  - Core band: x0.55 of lit. Starts where shadow > 0.36. Edge softness 2-4 mm (reads as a cut).
  - Occlusion core (ao_s < 0.6, creases): x0.42, edge 1-2 mm, blends into ink.
- Hue shift in shadow: -8 to -12 degrees (toward red): BL2 H25 -> H15, BL4 H23 -> H13. Saturation
  multiplier 1.2-1.4 in HSL (shadows are richer, not greyer; BL4 S0.27 -> S0.41). Never cool
  shadows on skin; cool (blue) only appears from the engine's rim/fill light.
- Painted highlights: nose bridge stripe (6-8 mm wide, full bridge length, the most visible painted
  light in `14.png`: `#D8B99E` vs cheek `#CE9E7C`, +12% L, -4 sat), brow ridge top, cheekbone top
  (ellipse 20x12 mm, soft 8 mm), upper lip ridge, chin ball, collarbone top, deltoid cap, knuckles,
  shin front edge. Colour = lit x1.12 L, hue +3 degrees toward yellow. Keep opacity 60%.
- Lower lip gets a 1.5 mm pale catch along its lower-centre ridge (existing `catch` is right).
- Ratio painted vs engine: albedo carries ~45% of the form read; even on the lit side of `25.webp`
  the jaw band stays visible. Keep lit skin albedo L 0.55-0.68 so engine light does not blow out,
  and keep total painted range lit->core at 0.42 so the engine can add the rest.
- Soft-light style: the band darkening is multiplicative with the hue/sat shift applied, no grey.
- Mottle/brush noise: keep existing fbm mottle but cap at +/-6% luminance; BL skin is smooth with
  a faint grit, not blotchy. Add a 2-3% "coffee stain" low-frequency warm/cool patching at 10 cm scale.

## 4. Skin palette (sRGB, lit base and matching core shadow at x0.55 L, hue -10, sat x1.3)

| Tone | Lit | Half-tone | Core shadow | Lips (lit / shadow) | Under-eye | Warmth (cheek/nose/knuckle) |
|---|---|---|---|---|---|---|
| Light | `#D5A585` | `#A77659` | `#7E4F3B` | `#C68A7C` / `#7E4F48` | `#A8795F` (skin x0.85, hue -6) | `#C97C64` |
| Medium | `#C28E6A` | `#95654A` | `#6E4332` | `#B5786A` / `#6E423C` | `#966650` | `#BE6E58` |
| Olive / tan | `#A6774E` | `#7E5436` | `#5C3827` | `#9E6557` / `#5A3730` | `#7E5640` | `#A85F49` |
| Dark | `#6E4A36` | `#523323` | `#3B2218` | `#6E4443` / `#3B2626` | `#54372A` | `#7A4536` |

- Saturation: lit skin HSL S 0.40-0.50 (BL2 `#CE9E7C` S0.46, BL4 S0.27-0.40). Our drawing's
  `#F29D5C` (S0.85) is twice too saturated and too orange: hue 26 is fine, drop S to 0.45 and
  lower V 10%.
- Under-eye: multiply band 8 mm tall by x0.85 with hue -6 (slightly purple-brown), soft 4 mm.
- Warmth zones at 25-35% blend of the warmth colour: cheeks (ellipse under eye, 45x30 mm), nose
  tip/ala, ears, knuckles, elbows, knees, heels, fingertips.
- Lips: 85% blend; upper lip x0.85 of lower; no saturated red/pink on male lips (BL3 lip L0.42 is
  barely different from skin; BL4 female wears painted dark lipstick `#5C3120`, treat as makeup).
- Current `SKIN (0.77,0.58,0.47)` = `#C4947A` matches "Light/Medium"; current `BLUSH` is too pink,
  use the warmth column (more orange-brown).

## 5. Eyes

- Iris diameter = 0.42 x eye opening width = ~0.90 x eye opening height; the upper lid covers the
  top 15-20% of the iris, the lower lid just touches it. Pupil = 0.40-0.45 of iris.
- Thick ink on the UPPER lid only: 3 mm inner -> 5 mm at the outer third, as one wedge that
  includes the lashes (no individual upper lashes). Lower lid: thin 0.8 mm broken, outer half, plus
  2-3 lash ticks at the outer corner.
- Limbal ring: 0.8 mm, ink at 80%. Iris has 6-8 radial darker spokes at 30% and a lighter inner
  ring at 0.6 radius (BL2 grey-blue `#2F3233` ring / pale centre; BL3 amber `#BE7D38`).
- Highlight: one off-white `#F2EEE8` dot at 11 o'clock, diameter 0.15 x iris, overlapping iris
  edge; optional thin 0.4 mm rim light along the bottom of the iris (BL3).
- Sclera: never white. Lit `#D9D2C6`, shadowed under the upper lid x0.6 for the top 35% of the
  opening, inner corner caruncle warm `#B0705C` blob 2 mm.
- Socket: multiply x0.85 inside 1.3 x eye_radius, no ink in the socket except lid lines.

## 6. Hair, beard, brows (male)

- Scalp hair: 6-12 clumps per side, each a flat 2-value shape (base x1.0, shadow x0.6) separated
  by ink lines 1.5 mm, with one lighter streak (x1.35, 2-3 mm wide) along each clump's ridge
  (`25.webp`). No per-strand noise. Hairline edge: 3-5 ink spikes 5-10 mm long, not a soft fade.
- Beard: solid mass, tone = hair colour at 70% blend with soft 4 mm edge, plus 5-8 ink strokes
  (1 mm, 6-10 mm long) along the lower edge and at the moustache corners (`25.webp` goatee is
  entirely ink strokes). Stubble (BL4 `28.webp`): a flat x0.80 multiply zone with hue -5, no dots.
- Brows: angled wedge, inner body 6-8 mm tall tapering to 2 mm at the tail, length 1.4 x eye
  width, tail ends 5 mm past the outer eye corner. Underside sits 1.0-1.2 x eye_radius above the
  eye centre (BL2: 13 mm). Inner end angled 10-15 degrees down toward the nose (resting frown). Fill
  = hair colour 85%, then 3-5 thin ink hair strokes on the top edge and a medium 2 mm ink line on
  the underside. Not a thin straight line (our drawing).

## 7. Our drawing (`24.webp`) vs Borderlands

Right: ink hierarchy exists on the face (lid, nostril, mouth corners); ab and pec creases are
tapered medium lines; beard is a solid mass; nose bridge highlight present; limb muscle lines
follow the form; feet/hands creased sparingly.

Wrong, in priority order:
1. Hatching is everywhere, including the lit side (lateral chest, outer arms, outer thighs, shins)
   and used as a texture, not as shadow. Should be confined to shadow pockets (<5% of area).
2. No painted shading bands at all: one flat tone plus lines. Needs half-tone x0.72 and core x0.55.
3. Skin is `#F29D5C`, S0.85: twice as saturated as BL, no hue shift anywhere. Use palette above.
4. Ink is mid-brown `#8B411C` at even width; thick lines are missing (upper lid should be 3-5 mm,
   nostril blob, mouth corner bracket). Ink should be `#1A100C`.
5. Forms are outlined on both sides (pec top edge, bicep top, thigh outer edge). Lit edges clean.
6. Brows are straight thin lines; hair is a flat black cap with no clumps or highlight streaks;
   beard has no edge strokes; eyes are ringed all round instead of a top-heavy lid wedge.
7. No painted highlights except the nose; no warmth zones; nipples are flat red discs (BL: skin
   warmth + a shadow-side ink crescent).

## 8. Parameter table for code

Conventions: `ao_s` = 12 mm AO, `ao_l` = 150 mm AO, `shadow = 1 - ao_l`, `point` = pointiness
(0.5 flat, <0.5 concave). Widths in m for `P`-space distance tests.

| Parameter | Value | Where |
|---|---|---|
| INK colour | `(0.10, 0.063, 0.047)` = `#1A100C` | all ink; thin lines alpha 0.45-0.65, thick 0.95-1.0 |
| Thick ink (AO) | `smoothstep(0.62, 0.50, ao_s)` | nostril, ear canal, mouth corner, armpit, groin |
| Medium ink (AO) | `smoothstep(0.76, 0.64, ao_s) * 0.9` | pec, abs, jaw, joints |
| Thin ink (AO) | `smoothstep(0.84, 0.76, ao_s) * 0.55` | tendons, minor creases |
| Concave-edge ink | `smoothstep(0.46, 0.42, point) * 0.7` | sharp creases; cap combined ink at 1.0 |
| Shadow-side gate | `ink *= 0.35 + 0.65 * smoothstep(-0.1, 0.4, shadow + 0.3*(1-ao_s))` | drop ink on lit side |
| Line width mm (landmark-drawn) | thick 3.0-5.0, medium 1.5-2.0, thin 0.8 (= 0.0008 m) | lid wedge: `w = 0.003 + 0.002*smoothstep(0, 1, t_outer)` |
| Taper | multiply width by `smoothstep(0, 0.2, t) * smoothstep(1, 0.8, t)` | t = 0..1 along line |
| Break pattern | `noise(P, 90) > 0.35` gate on medium/thin lines | broken segments 60-80% coverage |
| Wobble | offset line by `(noise(P, 200)-0.5) * 0.0006` m | hand feel |
| Hatch zone | `(shadow > 0.28) & (ao_s < 0.90)` within 0.015 m of an ink crease | shadow pockets only |
| Hatch spacing | 0.003 m at `shadow >= 0.45`, growing to 0.0055 m at `shadow = 0.28` | `spacing = lerp(0.0055, 0.003, smoothstep(0.28, 0.45, shadow))` |
| Hatch width / length / alpha | 0.0008 m / face 0.006, body 0.018 m / 0.65 | stroke length x0.5 near terminator |
| Hatch direction | project body-part long axis onto tangent plane | neck: Z, limbs: bone axis, torso: Z |
| Cross-hatch | second set at 60 degrees, only `shadow > 0.6 & ao_s < 0.7`, alpha 0.4 | armpit/groin only |
| Hatch area cap | <= 5% of skin texels | assert in paint step |
| Half-tone band | `smoothstep(0.14, 0.22, shadow)` -> luminance x0.72 | edge 6-8 mm |
| Core band | `smoothstep(0.32, 0.40, shadow)` -> x0.55 | edge 2-4 mm |
| Occlusion core | `smoothstep(0.70, 0.55, ao_s)` -> x0.42 | creases |
| Shadow hue / sat | hue -10 degrees, saturation x1.3 (HSL) per full band | implement as RGB multiplier `(0.78, 0.52, 0.42)` on lit for core instead of grey x0.55 |
| Highlight | lit x1.12 L, hue +3, alpha 0.6 | nose bridge 0.007 m wide, cheekbone 0.020x0.012 m, 0.008 m soft |
| Top light (`light`) | 0.04 on `N.z` | keep small; highlights are placed, not global |
| Mottle | +/-0.06 (fbm 45) + 0.03 stain (fbm 10) | reduce current 0.12 |
| Under-eye | x0.85, hue -6, ellipse 0.018x0.008 m, soft 0.004 m | existing shape OK, deepen from 0.07 to 0.15 |
| Warmth | blend 0.30 with warmth colour (`#C97C64` light tone) | cheeks, nose, ears, joints |
| Lip | 0.85 blend; upper x0.85; catch 1.5 mm +0.12 | lip colour per palette table |
| Iris | diameter 0.42 x eye width; pupil 0.42 x iris; limbal 0.0008 m at 0.8 ink | highlight 0.15 x iris at 11 o'clock `#F2EEE8` |
| Sclera | `#D9D2C6`, top 35% x0.6 | not white |
| Brow | underside at 1.1 x eye_radius above eye centre, height 0.007 -> 0.002 m, length 1.4 x eye width | underside ink 0.002 m |
| TT style | ink alpha 1.0 pure `#000000`, widths all 0.0022 m, hatch 0, bands: core only x0.6 hard 2 mm edge, warmth 0.15, sat x0.9 | `STYLES["TT"]` |
