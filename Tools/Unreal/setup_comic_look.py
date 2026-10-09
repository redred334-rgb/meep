"""
Builds the comic-look assets (outline post-process, toon skin/eye/mouth materials, style
collection, material instances) described in Docs/Art/UE5_Comic_Look_Design.md.

Run inside the Unreal Editor (5.3-5.5, Python Editor Script Plugin enabled):
    Tools > Execute Python Script > Tools/Unreal/setup_comic_look.py

Rebuild-from-scratch: everything under ASSET_ROOT is deleted and recreated, because the Python
API cannot read an existing material graph back. README.md next to this file lists the manual
steps that cannot be scripted (project settings, PostProcessVolume, stencil flags on Blueprints).

API notes: written against the 5.3-5.5 docs but never executed here. A few enum members and
property names could not be verified online; they use fallbacks (_first_attr) and are marked
"UNVERIFIED" in comments. Check the Output Log and open each material after the first run.
"""

import os

import unreal

# --------------------------------------------------------------------------------------------
# Settings you may want to edit

# Texture folder relative to the .uproject folder. Repo layout is <repo>/Art/... with the Unreal
# project expected at <repo>/<ProjectName>/, hence the "..".
TEXTURE_DIR_RELATIVE = os.path.join("..", "Art", "Characters", "Base", "Textures")
ASSET_ROOT = "/Game/ZombieMMO/Comic"
TEX_ROOT = ASSET_ROOT + "/Textures"
BODIES = ("Male", "Female")

# Default paint colours (sRGB hex) copied from Tools/Blender/comic_paint.py SKIN/HAIR/UNDERWEAR/IRIS.
# The textures are painted with these; the materials divide by them before tinting, so a tint
# parameter equal to the default leaves the painting untouched.
DEFAULT_SKIN_SRGB = "#C4947A"
DEFAULT_HAIR_SRGB = "#17110E"
DEFAULT_UNDERWEAR_SRGB = "#333338"
DEFAULT_IRIS_SRGB = "#4D2B17"

MPC_SCALARS = {  # Style 0 = Borderlands defaults (design doc section 5)
    "Style": 0.0, "OutlineThicknessMul": 1.0, "Bands": 2.0, "BandSoftness": 0.0, "HatchStrength": 1.0}
MPC_VECTORS = {
    "LightDir": (0.35, -0.45, 0.82, 0.0),      # BP_ComicStyleManager writes -SunDirection here
    "LightColor": (1.0, 0.96, 0.9, 1.0),
    "AmbientColor": (0.3, 0.32, 0.4, 1.0),
    "ShadowTint": (0.45, 0.4, 0.55, 1.0)}      # Telltale: 0.6, 0.58, 0.62

# --------------------------------------------------------------------------------------------
# Small helpers

LOG = unreal.log
MEL = unreal.MaterialEditingLibrary
EAL = unreal.EditorAssetLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
_warnings = []


def warn(msg):
    _warnings.append(msg)
    unreal.log_warning(msg)


def _first_attr(obj, *names):
    """First attribute of obj that exists, for enum members renamed between 5.3 and 5.5."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    raise AttributeError("%s has none of %s" % (obj, names))


def srgb_to_linear(hex_col):
    out = []
    for i in (1, 3, 5):
        c = int(hex_col[i:i + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


def lin_color(rgb, a=1.0):
    return unreal.LinearColor(rgb[0], rgb[1], rgb[2], a)


def hlsl3(rgb):
    return "float3(%.5f, %.5f, %.5f)" % rgb


def create_asset(name, cls, factory, folder=ASSET_ROOT):
    asset = TOOLS.create_asset(asset_name=name, package_path=folder, asset_class=cls, factory=factory)
    if asset is None:
        raise RuntimeError("Could not create %s/%s" % (folder, name))
    return asset


# --------------------------------------------------------------------------------------------
# Material graph helpers (MaterialEditingLibrary)


def node(mat, cls, x, y, **props):
    expr = MEL.create_material_expression(mat, cls, x, y)
    for k, v in props.items():
        expr.set_editor_property(k, v)
    return expr


def link(src, out_name, dst, in_name):
    """Connect an expression output ("" = first output, or "RGB"/"R"/"A"...) to a named input."""
    if not MEL.connect_material_expressions(src, out_name, dst, in_name):
        warn("Could not connect %s.%s -> %s.%s" % (src.get_name(), out_name, dst.get_name(), in_name))


def to_property(src, prop):
    if not MEL.connect_material_property(src, "", prop):
        warn("Could not connect %s to %s" % (src.get_name(), prop))


def scalar_param(mat, name, value, x, y):
    return node(mat, unreal.MaterialExpressionScalarParameter, x, y, parameter_name=name, default_value=value)


def vector_param(mat, name, rgb, x, y):
    return node(mat, unreal.MaterialExpressionVectorParameter, x, y, parameter_name=name,
                default_value=lin_color(rgb))


def texture_param(mat, name, texture, linear, x, y):
    """TextureSampleParameter2D. The sampler type must match the texture's sRGB flag or the
    material will not compile (LinearColor for masks, Color for sRGB base colour)."""
    st = unreal.MaterialSamplerType
    expr = node(mat, unreal.MaterialExpressionTextureSampleParameter2D, x, y, parameter_name=name,
                sampler_type=st.SAMPLERTYPE_LINEAR_COLOR if linear else st.SAMPLERTYPE_COLOR)
    if texture is not None:
        expr.set_editor_property("texture", texture)
    else:
        warn("%s: no default texture for parameter %s; assign one in the instance" % (mat.get_name(), name))
    return expr


def collection_param(mat, mpc, name, x, y):
    # UNVERIFIED property names: C++ UMaterialExpressionCollectionParameter has Collection and
    # ParameterName (both EditAnywhere), so the Python names should be collection / parameter_name.
    return node(mat, unreal.MaterialExpressionCollectionParameter, x, y, collection=mpc, parameter_name=name)


def custom_hlsl(mat, title, code, input_names, out_type, x, y):
    """One MaterialExpressionCustom. Inputs are referenced by name inside the HLSL; the body is
    the inside of a function that must `return` a value of out_type."""
    inputs = []
    for n in input_names:
        ci = unreal.CustomInput()
        ci.set_editor_property("input_name", n)
        inputs.append(ci)
    return node(mat, unreal.MaterialExpressionCustom, x, y, description=title, code=code, inputs=inputs,
                output_type=out_type)


def wire_custom(mat, title, code, inputs, out_type, x, y):
    """custom_hlsl plus connections. inputs: list of (input name, expression, output name)."""
    expr = custom_hlsl(mat, title, code, [n for n, _, _ in inputs], out_type, x, y)
    for name, src, out in inputs:
        link(src, out, expr, name)
    return expr


def constant(mat, value, x, y):
    return node(mat, unreal.MaterialExpressionConstant, x, y, r=value)


def new_material(name, domain=None):
    mat = create_asset(name, unreal.Material, unreal.MaterialFactoryNew())
    if domain is not None:
        mat.set_editor_property("material_domain", domain)
    else:
        mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
        set_usage_flags(mat)
    return mat


def finish_material(mat):
    MEL.recompile_material(mat)
    EAL.save_loaded_asset(mat)
    LOG("Created %s" % mat.get_path_name())


def set_usage_flags(mat):
    """Use with Skeletal Mesh / Morph Targets. set_material_usage is the documented route; the
    MaterialUsage member spelling (MATUSAGE_*) is UNVERIFIED, hence the property fallback."""
    try:
        for member in ("MATUSAGE_SKELETAL_MESH", "MATUSAGE_MORPH_TARGETS"):
            MEL.set_material_usage(mat, getattr(unreal.MaterialUsage, member))
    except Exception as exc:  # noqa: BLE001
        LOG("set_material_usage unavailable (%s); setting the properties directly" % exc)
        mat.set_editor_property("used_with_skeletal_mesh", True)
        mat.set_editor_property("used_with_morph_targets", True)


# --------------------------------------------------------------------------------------------
# 1. Textures


def texture_specs(tex_dir):
    """(asset name, linear?) for every texture to import. Base colour prefers the _Drawn variant."""
    specs = []
    for body in BODIES:
        drawn = "T_Body_%s_BaseColor_Drawn" % body
        bl = "T_Body_%s_BaseColor_BL" % body
        specs.append((drawn if os.path.isfile(os.path.join(tex_dir, drawn + ".png")) else bl, False))
        specs += [("T_Body_%s_BaseColor_TT" % body, False), ("T_Body_%s_Masks" % body, True)]
    return specs + [("T_Body_Male_Beard", True), ("T_Eyes_Masks", True), ("T_Eyes_BaseColor", False)]


def import_texture(path, name, linear):
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", path)
    task.set_editor_property("destination_path", TEX_ROOT)
    task.set_editor_property("destination_name", name)  # ignored by Interchange; file stem == name anyway
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", False)
    TOOLS.import_asset_tasks([task])
    tex = unreal.load_asset("%s/%s" % (TEX_ROOT, name))
    if tex is None:
        return None
    tc = unreal.TextureCompressionSettings
    tex.set_editor_property("srgb", not linear)
    tex.set_editor_property("compression_settings", tc.TC_MASKS if linear else tc.TC_DEFAULT)
    # 4096 textures are auto-converted to virtual textures, which need a different sampler type.
    tex.set_editor_property("virtual_texture_streaming", False)
    EAL.save_loaded_asset(tex)
    return tex


def import_textures():
    project_dir = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    tex_dir = os.path.normpath(os.path.join(project_dir, TEXTURE_DIR_RELATIVE))
    LOG("Importing textures from %s" % tex_dir)
    textures = {}
    for name, linear in texture_specs(tex_dir):
        path = os.path.join(tex_dir, name + ".png")
        if not os.path.isfile(path):
            warn("Missing texture %s (skipped)" % path)
            continue
        tex = import_texture(path, name, linear)
        if tex is None:
            warn("Import failed for %s" % path)
            continue
        textures[name] = tex
        LOG("Imported %s (%s)" % (name, "linear" if linear else "sRGB"))
    return textures


def body_textures(textures, body):
    """Parameter name -> texture for one body (missing ones are None)."""
    return {"BaseColorBL": textures.get("T_Body_%s_BaseColor_Drawn" % body) or textures.get("T_Body_%s_BaseColor_BL" % body),
            "BaseColorTT": textures.get("T_Body_%s_BaseColor_TT" % body),
            "Masks": textures.get("T_Body_%s_Masks" % body),
            "Beard": textures.get("T_Body_Male_Beard")}


# --------------------------------------------------------------------------------------------
# 2. MPC_ComicStyle


def create_mpc():
    mpc = create_asset("MPC_ComicStyle", unreal.MaterialParameterCollection,
                       unreal.MaterialParameterCollectionFactoryNew())
    scalars, vectors = [], []
    for name, value in MPC_SCALARS.items():
        p = unreal.CollectionScalarParameter()
        p.set_editor_property("parameter_name", name)
        p.set_editor_property("default_value", value)
        scalars.append(p)
    for name, value in MPC_VECTORS.items():
        p = unreal.CollectionVectorParameter()
        p.set_editor_property("parameter_name", name)
        p.set_editor_property("default_value", unreal.LinearColor(*value))
        vectors.append(p)
    mpc.set_editor_property("scalar_parameters", scalars)
    mpc.set_editor_property("vector_parameters", vectors)
    EAL.save_loaded_asset(mpc)
    LOG("Created %s" % mpc.get_path_name())
    return mpc


# --------------------------------------------------------------------------------------------
# 3. PP_ComicOutline

# SceneTextureLookup IDs: 1 SceneDepth, 8 WorldNormal, 14 PostProcessInput0, 25 CustomStencil.
# GetDefaultSceneTextureUV and View.ViewSizeAndInvSize (texel size in .zw) are engine HLSL usable
# inside Custom nodes. The SceneTexture nodes wired to the otherwise unused *Tex inputs are
# required: the compiler only binds a scene texture when a SceneTexture expression with that ID
# is compiled; without them SceneTextureLookup fails to compile or reads black.
OUTLINE_HLSL = r"""
float2 uv = GetDefaultSceneTextureUV(Parameters, 14);
float2 texel = View.ViewSizeAndInvSize.zw;
float depthC = SceneTextureLookup(uv, 1, false).r;
float stencilC = SceneTextureLookup(uv, 25, false).r;
float3 sceneC = SceneTextureLookup(uv, 14, false).rgb;
float3 nC = normalize(SceneTextureLookup(uv, 8, false).rgb + 1e-5);

// Depth-scaled thickness, clamped to [0.5, 1.0] of Thickness: far lines thin but never vanish.
float px = Thickness * ThicknessMul * clamp(ReferenceDepth / max(depthC, 1.0), 0.5, 1.0);
float2 stp = texel * px;

const float2 offs[8] = { float2(-1,-1), float2(0,-1), float2(1,-1), float2(-1,0),
                         float2(1,0), float2(-1,1), float2(0,1), float2(1,1) };
const float kx[8] = { -1, 0, 1, -2, 2, -1, 0, 1 };
const float ky[8] = { -1, -2, -1, 0, 0, 1, 2, 1 };
float gx = 0.0, gy = 0.0, minDot = 1.0;
[unroll] for (int i = 0; i < 8; i++)
{
    float2 o = uv + offs[i] * stp;
    float d = SceneTextureLookup(o, 1, false).r;
    gx += d * kx[i];
    gy += d * ky[i];
    float3 n = normalize(SceneTextureLookup(o, 8, false).rgb + 1e-5);
    minDot = min(minDot, dot(nC, n));
}
// Divide by centre depth so the threshold is distance independent.
float depthEdge = smoothstep(0.02, 0.08, sqrt(gx * gx + gy * gy) / max(depthC, 1.0) * DepthSensitivity);
float normalEdge = smoothstep(0.1, 0.4, (1.0 - minDot) * NormalSensitivity);
float edge = max(depthEdge, normalEdge);
edge *= step(StencilMin, stencilC);                                   // centre-only stencil test
edge *= 1.0 - smoothstep(MaxDistance * 0.8, MaxDistance, depthC);     // distance fade
return lerp(sceneC, OutlineColor, saturate(edge));
"""


def create_pp_outline(mpc):
    mat = new_material("PP_ComicOutline", unreal.MaterialDomain.MD_POST_PROCESS)
    # 5.4 renamed the blendable locations; BL_BEFORE_TONEMAPPING survives in C++ as a deprecated
    # alias of BL_SCENE_COLOR_BEFORE_BLOOM but the Python enum may expose only one (UNVERIFIED).
    mat.set_editor_property("blendable_location", _first_attr(
        unreal.BlendableLocation, "BL_BEFORE_TONEMAPPING", "BL_SCENE_COLOR_BEFORE_BLOOM"))
    # Output Alpha stays at its default (off).

    inputs = [
        ("OutlineColor", vector_param(mat, "OutlineColor", (0.02, 0.02, 0.02), -900, -300), ""),
        ("Thickness", scalar_param(mat, "Thickness", 2.0, -900, -150), ""),
        ("DepthSensitivity", scalar_param(mat, "DepthSensitivity", 1.0, -900, -50), ""),
        ("NormalSensitivity", scalar_param(mat, "NormalSensitivity", 1.0, -900, 50), ""),
        ("MaxDistance", scalar_param(mat, "MaxDistance", 4000.0, -900, 150), ""),
        ("StencilMin", scalar_param(mat, "StencilMin", 1.0, -900, 250), ""),
        ("ReferenceDepth", scalar_param(mat, "ReferenceDepth", 300.0, -900, 350), ""),
        ("ThicknessMul", collection_param(mat, mpc, "OutlineThicknessMul", -900, 450), "")]
    st = unreal.SceneTextureId
    scene_ids = (("DepthTex", st.PPI_SCENE_DEPTH), ("NormalTex", st.PPI_WORLD_NORMAL),
                 ("SceneTex", st.PPI_POST_PROCESS_INPUT0), ("StencilTex", st.PPI_CUSTOM_STENCIL))
    for i, (name, sid) in enumerate(scene_ids):
        inputs.append((name, node(mat, unreal.MaterialExpressionSceneTexture, -900, 600 + i * 100,
                                  scene_texture_id=sid), ""))
    sobel = wire_custom(mat, "Sobel8 outline (depth + normal, stencil masked)", OUTLINE_HLSL, inputs,
                        unreal.CustomMaterialOutputType.CMOT_FLOAT3, -400, 0)
    to_property(sobel, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    finish_material(mat)
    return mat


# --------------------------------------------------------------------------------------------
# 4. M_Skin

SKIN_HLSL = r"""
float3 base = lerp(BaseBL, BaseTT, saturate(Style));
// Divide by the painted default, multiply by the tint: keeps mottling, hatching and shading.
float3 skinT  = base / max(DEF_SKIN, 0.02) * SkinTone;
float3 hairT  = base / max(DEF_HAIR, 0.02) * HairColor;
float3 underT = base / max(DEF_UNDER, 0.02) * UnderwearColor;
float3 col = lerp(base, skinT, Masks.r);
col = lerp(col, hairT, Masks.g);
col = lerp(col, underT, Masks.b);
col = lerp(col, HairColor * lerp(0.8, 1.0, Beard), saturate(Beard * BeardAmount));

// Toon bands (design doc section 3b). BandSoftness widens the transition at the top of each band.
float ndl = dot(normalize(N), normalize(LightDir.xyz));
float halfL = saturate(ndl * 0.5 + 0.5);
float bands = max(Bands, 2.0);
float scaled = halfL * bands;
float idx = floor(scaled);
float soft = clamp(BandSoftness, 0.001, 0.999);
float band = saturate((idx + smoothstep(1.0 - soft, 1.0, scaled - idx)) / (bands - 1.0));
float3 litCol = lerp(ShadowTint.rgb, float3(1.0, 1.0, 1.0), band);

// Ink = everything the painter left out of the masks. Ink is not lit.
float ink = saturate(1.0 - (Masks.r + Masks.g + Masks.b));
return lerp(col * litCol, col, ink * LineworkStrength);
"""

INK_SPECULAR_HLSL = "float ink = saturate(1.0 - (Masks.r + Masks.g + Masks.b));\nreturn lerp(0.2, 0.0, ink);\n"

HIDE_MASK_HLSL = r"""
float hidden = max(max(VC.r * HideTorso, VC.g * HideArms), max(VC.b * HideLegs, VCA * HideFeet));
return 1.0 - saturate(hidden);
"""


def create_skin_material(mpc, textures):
    mat = new_material("M_Skin")
    mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
    mat.set_editor_property("opacity_mask_clip_value", 0.5)
    mat.set_editor_property("two_sided", False)

    tex = body_textures(textures, "Male")  # defaults; the instances override per body
    base_bl = texture_param(mat, "BaseColorBL", tex["BaseColorBL"], False, -1300, -600)
    base_tt = texture_param(mat, "BaseColorTT", tex["BaseColorTT"], False, -1300, -400)
    masks = texture_param(mat, "Masks", tex["Masks"], True, -1300, -200)
    beard = texture_param(mat, "Beard", tex["Beard"], True, -1300, 0)
    normal = node(mat, unreal.MaterialExpressionPixelNormalWS, -1300, 200)
    skin_code = (SKIN_HLSL.replace("DEF_SKIN", hlsl3(srgb_to_linear(DEFAULT_SKIN_SRGB)))
                 .replace("DEF_HAIR", hlsl3(srgb_to_linear(DEFAULT_HAIR_SRGB)))
                 .replace("DEF_UNDER", hlsl3(srgb_to_linear(DEFAULT_UNDERWEAR_SRGB))))
    inputs = [
        ("BaseBL", base_bl, "RGB"), ("BaseTT", base_tt, "RGB"), ("Masks", masks, "RGB"), ("Beard", beard, "R"),
        ("N", normal, ""),
        ("SkinTone", vector_param(mat, "SkinTone", srgb_to_linear(DEFAULT_SKIN_SRGB), -900, -600), ""),
        ("HairColor", vector_param(mat, "HairColor", srgb_to_linear(DEFAULT_HAIR_SRGB), -900, -450), ""),
        ("UnderwearColor", vector_param(mat, "UnderwearColor", srgb_to_linear(DEFAULT_UNDERWEAR_SRGB), -900, -300), ""),
        ("LineworkStrength", scalar_param(mat, "LineworkStrength", 1.0, -900, -150), ""),
        ("BeardAmount", scalar_param(mat, "BeardAmount", 0.0, -900, -50), ""),
        ("Style", collection_param(mat, mpc, "Style", -900, 50), ""),
        ("Bands", collection_param(mat, mpc, "Bands", -900, 150), ""),
        ("BandSoftness", collection_param(mat, mpc, "BandSoftness", -900, 250), ""),
        ("ShadowTint", collection_param(mat, mpc, "ShadowTint", -900, 350), ""),
        ("LightDir", collection_param(mat, mpc, "LightDir", -900, 450), "")]
    color = wire_custom(mat, "Comic skin: tint, bands, ink", skin_code, inputs,
                        unreal.CustomMaterialOutputType.CMOT_FLOAT3, -400, -300)
    to_property(color, unreal.MaterialProperty.MP_BASE_COLOR)

    spec = wire_custom(mat, "Ink specular", INK_SPECULAR_HLSL, [("Masks", masks, "RGB")],
                       unreal.CustomMaterialOutputType.CMOT_FLOAT1, -400, 100)
    to_property(spec, unreal.MaterialProperty.MP_SPECULAR)
    to_property(constant(mat, 1.0, -400, 200), unreal.MaterialProperty.MP_ROUGHNESS)

    # Opacity mask from the HideMask vertex colour (R torso, G arms, B legs, A feet) x Hide* scalars.
    vc = node(mat, unreal.MaterialExpressionVertexColor, -900, 600)
    hide_inputs = [("VC", vc, ""), ("VCA", vc, "A")]
    for i, name in enumerate(("HideTorso", "HideArms", "HideLegs", "HideFeet")):
        hide_inputs.append((name, scalar_param(mat, name, 0.0, -900, 700 + i * 100), ""))
    hide = wire_custom(mat, "Hide regions", HIDE_MASK_HLSL, hide_inputs,
                       unreal.CustomMaterialOutputType.CMOT_FLOAT1, -400, 600)
    to_property(hide, unreal.MaterialProperty.MP_OPACITY_MASK)
    finish_material(mat)
    return mat


# --------------------------------------------------------------------------------------------
# 5. M_Eyes and M_Mouth

EYES_HLSL = "float3 iris = Base / max(DEF_IRIS, 0.02) * IrisColor;\nreturn lerp(Base, iris, IrisMask);\n"


def create_eyes_material(textures):
    mat = new_material("M_Eyes")
    iris_lin = srgb_to_linear(DEFAULT_IRIS_SRGB)
    inputs = [("Base", texture_param(mat, "BaseColor", textures.get("T_Eyes_BaseColor"), False, -1000, -200), "RGB"),
              ("IrisMask", texture_param(mat, "Masks", textures.get("T_Eyes_Masks"), True, -1000, 0), "R"),
              ("IrisColor", vector_param(mat, "IrisColor", iris_lin, -1000, 200), "")]
    tint = wire_custom(mat, "Iris tint", EYES_HLSL.replace("DEF_IRIS", hlsl3(iris_lin)), inputs,
                       unreal.CustomMaterialOutputType.CMOT_FLOAT3, -500, 0)
    to_property(tint, unreal.MaterialProperty.MP_BASE_COLOR)
    to_property(constant(mat, 0.2, -500, 200), unreal.MaterialProperty.MP_ROUGHNESS)
    finish_material(mat)
    return mat


def create_mouth_material():
    mat = new_material("M_Mouth")
    col = node(mat, unreal.MaterialExpressionConstant3Vector, -500, 0, constant=lin_color((0.45, 0.18, 0.16)))
    to_property(col, unreal.MaterialProperty.MP_BASE_COLOR)
    to_property(constant(mat, 0.6, -500, 200), unreal.MaterialProperty.MP_ROUGHNESS)
    finish_material(mat)
    return mat


# --------------------------------------------------------------------------------------------
# 6. Material instances


def create_instance(name, parent, textures=None, scalars=None):
    inst = create_asset(name, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
    inst.set_editor_property("parent", parent)
    for pname, tex in (textures or {}).items():
        if tex is None:
            warn("%s: texture for %s is missing, parameter left at the parent default" % (name, pname))
            continue
        MEL.set_material_instance_texture_parameter_value(inst, pname, tex)
    for pname, value in (scalars or {}).items():
        MEL.set_material_instance_scalar_parameter_value(inst, pname, value)
    MEL.update_material_instance(inst)
    EAL.save_loaded_asset(inst)
    LOG("Created %s" % inst.get_path_name())
    return inst


# --------------------------------------------------------------------------------------------
# Main

MANUAL_STEPS = """
Manual steps (cannot be scripted; details in Tools/Unreal/README.md):
 1. Project Settings > Rendering > Postprocessing > Custom Depth-Stencil Pass = Enabled with Stencil
    (or r.CustomDepth=3 in Config/DefaultEngine.ini, [/Script/Engine.RendererSettings]); restart.
 2. Project Settings > Rendering: Anti-Aliasing = TSR.
 3. Each level: PostProcessVolume, Infinite Extent (Unbound), Post Process Materials > PP_ComicOutline
    (weight 1), Exposure: Min EV100 = Max EV100 = 1 (or Metering Mode Manual).
 4. BP_PlayerCharacter / BP_Zombie Mesh: Render CustomDepth Pass = true, CustomDepth Stencil Value =
    1 (players) / 2 (zombies); props that need ink = 3; terrain, foliage, sky = 0.
 5. Assign MI_Skin_Male / MI_Skin_Female, MI_Eyes and MI_Mouth to the skeletal mesh slots.
 6. Add BP_ComicStyleManager (writes MPC_ComicStyle from the DirectionalLight, picks Style 0/1).
 7. Verify: Buffer Visualization > Custom Stencil shows characters; stat gpu PostProcessing < 1 ms;
    open each material in the Material Editor and confirm it compiled without errors.
"""


def main():
    if EAL.does_directory_exist(ASSET_ROOT):
        LOG("Deleting %s for a clean rebuild" % ASSET_ROOT)
        EAL.delete_directory(ASSET_ROOT)
    EAL.make_directory(TEX_ROOT)

    textures = import_textures()
    mpc = create_mpc()
    create_pp_outline(mpc)
    skin = create_skin_material(mpc, textures)
    eyes = create_eyes_material(textures)
    mouth = create_mouth_material()

    create_instance("MI_Skin_Male", skin, body_textures(textures, "Male"), {"BeardAmount": 1.0})
    female = body_textures(textures, "Female")
    female.pop("Beard")  # no female beard texture; the parent default stays and BeardAmount is 0
    create_instance("MI_Skin_Female", skin, female, {"BeardAmount": 0.0})
    create_instance("MI_Eyes", eyes, {"BaseColor": textures.get("T_Eyes_BaseColor"),
                                      "Masks": textures.get("T_Eyes_Masks")})
    create_instance("MI_Mouth", mouth)

    LOG("=" * 78)
    LOG("Comic look assets rebuilt under %s" % ASSET_ROOT)
    LOG("Textures imported: %s" % (", ".join(sorted(textures)) or "none"))
    LOG("Materials: PP_ComicOutline, M_Skin, M_Eyes, M_Mouth; MPC_ComicStyle;"
        " instances MI_Skin_Male, MI_Skin_Female, MI_Eyes, MI_Mouth")
    if _warnings:
        LOG("%d warning(s):" % len(_warnings))
        for w in _warnings:
            LOG("  - " + w)
    LOG(MANUAL_STEPS)


# "Execute Python Script" does not reliably set __name__ to "__main__", so always run.
main()
