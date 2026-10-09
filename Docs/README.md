# Docs

Everything here is under `Art/`. The tools these notes drive live in `Tools/`, and the repo root
`README.md` has the pipeline order and commands.

- `Art/Studio_Pipelines_Research.md`: how Gearbox (Borderlands) and Telltale (The Walking Dead)
  actually make their looks, from developer statements, press and artist tutorials, with each
  claim tagged by source quality. Read it first if you want to know why the project paints ink
  into the textures and only uses a Sobel outline in the engine, or to settle an argument about
  what is and is not a cel shader.
- `Art/Borderlands_Style_Guide.md`: the measured rules the procedural painter follows (ink
  colours and widths, hatching, shading bands, skin palette, eyes, hair and beard), plus a
  parameter table for code. Read it before changing anything in `Tools/Blender/comic_paint.py`
  or when judging whether a painted or projected texture looks right.
- `Art/UE5_Comic_Look_Design.md`: the design of the Unreal side: stencil-masked depth + normal
  Sobel outline, banded lighting options, the `M_Skin` material spec, the `MPC_ComicStyle` style
  switch and what the editor Python API can and cannot build. Read it before touching
  `Tools/Unreal/setup_comic_look.py` or when setting the look up in a project by hand.
- `Art/Next_Steps_Research.md`: research for the next pieces of work: Borderlands-style hair,
  beard and brow assets (Tripo to Blender to Unreal), fitting generated clothes to the bodies
  with Shrinkwrap and Data Transfer, and talking / blinking in UE5 with the custom `Exp_*`
  morphs, with a recommendation at the end of each section. Read it when starting any of those
  three tasks; claims it could not confirm are marked unverified.
