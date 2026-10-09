"""
Imports the player base bodies (and any clothes / test meshes) into Unreal and sets them up:

    Art/Characters/Base/SK_BaseBody_Male.fbx    -> /Game/ZombieMMO/Characters/SK_BaseBody_Male
    Art/Characters/Base/SK_BaseBody_Female.fbx  -> /Game/ZombieMMO/Characters/SK_BaseBody_Female
    Art/Characters/Test/*.fbx                   -> /Game/ZombieMMO/Characters/Test/
    Art/Characters/Clothing/*.fbx               -> /Game/ZombieMMO/Characters/Clothing/

- One skeleton, SK_BaseBody_Male_Skeleton, created by the male import and reused by everything else.
- Import Morph Targets on, vertex colours Replace (the HideMask lives in vertex colour), no
  materials or textures from the FBX, no physics asset (CREATE_PHYSICS_ASSET flips that).
- Assigns MI_Skin_Male / MI_Skin_Female, MI_Eyes, MI_Mouth (from setup_comic_look.py) to the
  M_Skin / M_Eyes / M_Mouth slots by slot name.
- Generates LOD1-3 at 50 / 25 / 10 % of the triangles through the skeletal mesh reduction settings.
- Verifies 85 morph targets (51 face sliders + 34 Exp_*) and 56 bones and prints a report.

Run inside the Unreal Editor (5.3-5.5, Python Editor Script Plugin enabled):
    Tools > Execute Python Script > Tools/Unreal/import_characters.py

Idempotent: meshes that already exist are not re-imported unless REIMPORT is True; the material,
LOD and verification steps run every time, so a second run after a failed or asynchronous import
finishes the job.

Importer choice (5.3 vs 5.5): the script uses the legacy FBX importer (AssetImportTask +
FbxImportUI) on every version, because it is synchronous and its options are documented. UE 5.5
routes FBX through Interchange by default; the script switches that off for the duration of the
import with the Interchange.FeatureFlags.Import.FBX console variable. Set USE_INTERCHANGE = True
to use the Interchange path instead (InterchangeGenericAssetsPipeline); it is asynchronous, so
run the script twice (the second run does the materials / LODs / report).

API notes: written against the 5.3-5.5 Python docs but never executed here. Names that could not
be verified online are marked UNVERIFIED and wrapped in fallbacks.
"""

import os

import unreal

# --------------------------------------------------------------------------------------------
# Settings you may want to edit

# Art folder relative to the .uproject folder. Repo layout is <repo>/Art/... with the Unreal
# project expected at <repo>/<ProjectName>/, hence the "..".
ART_DIR_RELATIVE = os.path.join("..", "Art", "Characters")
ASSET_ROOT = "/Game/ZombieMMO/Characters"
COMIC_ROOT = "/Game/ZombieMMO/Comic"          # where setup_comic_look.py put the instances

BODIES = ("Male", "Female")                  # male first: it creates the shared skeleton
EXTRA_FOLDERS = ("Test", "Clothing")         # Art/Characters/<folder>/*.fbx, imported onto the skeleton

REIMPORT = False                             # True: re-import meshes that already exist
USE_INTERCHANGE = False                      # see module docstring
CREATE_PHYSICS_ASSET = False

EXPECTED_MORPHS = 85                         # 51 face sliders + 34 Exp_*
EXPECTED_EXPRESSIONS = 34
EXPECTED_BONES = 56
FACE_BONES = ("jaw", "eye_l", "eye_r")

# LOD1..3 as a fraction of LOD0 triangles, and the screen size each LOD switches in at.
LOD_PERCENTS = (0.5, 0.25, 0.1)
LOD_SCREEN_SIZES = (1.0, 0.4, 0.2, 0.1)

# Material slot name -> material instance path, per mesh name prefix. Slots not listed keep
# whatever they have (clothes get their own instances later).
SLOT_MATERIALS = {
    "SK_BaseBody_Male": {"M_Skin": COMIC_ROOT + "/MI_Skin_Male", "M_Eyes": COMIC_ROOT + "/MI_Eyes",
                         "M_Mouth": COMIC_ROOT + "/MI_Mouth"},
    "SK_BaseBody_Female": {"M_Skin": COMIC_ROOT + "/MI_Skin_Female", "M_Eyes": COMIC_ROOT + "/MI_Eyes",
                           "M_Mouth": COMIC_ROOT + "/MI_Mouth"},
}

# --------------------------------------------------------------------------------------------
# Helpers

LOG = unreal.log
EAL = unreal.EditorAssetLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
_warnings = []
_report = []


def warn(msg):
    _warnings.append(msg)
    unreal.log_warning(msg)


def report(msg):
    _report.append(msg)
    LOG(msg)


def _first_attr(obj, *names):
    """First attribute of obj that exists, for enum members renamed between 5.3 and 5.5."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    raise AttributeError("%s has none of %s" % (obj, names))


def _set(obj, name, value):
    """set_editor_property that only warns when a property is missing on this engine version."""
    try:
        obj.set_editor_property(name, value)
        return True
    except Exception as exc:  # noqa: BLE001
        warn("%s: could not set %s (%s)" % (obj.get_class().get_name() if hasattr(obj, "get_class") else obj,
                                            name, exc))
        return False


def art_dir():
    project_dir = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    return os.path.normpath(os.path.join(project_dir, ART_DIR_RELATIVE))


def console(cmd):
    try:
        unreal.SystemLibrary.execute_console_command(None, cmd)
    except Exception as exc:  # noqa: BLE001
        warn("Console command '%s' failed (%s)" % (cmd, exc))


def skeleton_path():
    return "%s/SK_BaseBody_Male_Skeleton" % ASSET_ROOT


# --------------------------------------------------------------------------------------------
# 1. Import (legacy FBX importer)


def fbx_options(skeleton):
    """FbxImportUI for a skeletal mesh. skeleton None = create a new one (the male body)."""
    ui = unreal.FbxImportUI()
    ui.set_editor_property("import_mesh", True)
    ui.set_editor_property("import_as_skeletal", True)
    ui.set_editor_property("import_animations", False)
    ui.set_editor_property("import_materials", False)
    ui.set_editor_property("import_textures", False)
    ui.set_editor_property("create_physics_asset", CREATE_PHYSICS_ASSET)
    ui.set_editor_property("automated_import_should_detect_type", False)
    ui.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_SKELETAL_MESH)
    ui.set_editor_property("skeleton", skeleton)

    data = ui.get_editor_property("skeletal_mesh_import_data")
    data.set_editor_property("import_morph_targets", True)
    data.set_editor_property("vertex_color_import_option", unreal.VertexColorImportOption.REPLACE)
    data.set_editor_property("update_skeleton_reference_pose", False)
    data.set_editor_property("use_t0_as_ref_pose", False)
    _set(data, "import_mesh_lo_ds", False)                        # LODs are generated below
    _set(data, "normal_import_method", unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS)
    _set(data, "convert_scene", True)
    _set(data, "import_uniform_scale", 1.0)
    # Keep every morph vertex, even tiny slider deltas (default 0.015 cm drops subtle ones).
    _set(data, "threshold_position", 0.001)
    return ui


def import_fbx_legacy(path, dest_folder, name, skeleton):
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", path)
    task.set_editor_property("destination_path", dest_folder)
    task.set_editor_property("destination_name", name)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)
    task.set_editor_property("options", fbx_options(skeleton))

    # 5.5 sends FBX through Interchange by default, which ignores FbxImportUI; force the legacy
    # importer for this task and restore the default afterwards.
    console("Interchange.FeatureFlags.Import.FBX 0")
    try:
        TOOLS.import_asset_tasks([task])
    finally:
        console("Interchange.FeatureFlags.Import.FBX 1")

    paths = list(task.get_editor_property("imported_object_paths") or [])
    LOG("Imported %s -> %s" % (os.path.basename(path), paths))
    return unreal.load_asset("%s/%s" % (dest_folder, name))


# --------------------------------------------------------------------------------------------
# 1b. Import (Interchange, 5.3+; opt-in, asynchronous)


def import_fbx_interchange(path, dest_folder, name, skeleton):
    pipeline = unreal.InterchangeGenericAssetsPipeline()
    mesh_p = pipeline.get_editor_property("mesh_pipeline")
    _set(mesh_p, "import_skeletal_meshes", True)
    _set(mesh_p, "import_static_meshes", False)
    _set(mesh_p, "import_morph_targets", True)
    _set(mesh_p, "create_physics_asset", CREATE_PHYSICS_ASSET)
    _set(mesh_p, "import_vertex_attributes", True)
    # Vertex colour option moved to common_meshes_properties in 5.5 (5.0 had it on the assets
    # pipeline). Enum member spelling UNVERIFIED: C++ is EInterchangeVertexColorImportOption::IVCIO_Replace.
    common = None
    try:
        common = pipeline.get_editor_property("common_meshes_properties")
    except Exception:  # noqa: BLE001
        common = pipeline
    try:
        enum = unreal.InterchangeVertexColorImportOption
        _set(common, "vertex_color_import_option", _first_attr(enum, "IVCIO_REPLACE", "REPLACE"))
    except Exception as exc:  # noqa: BLE001
        warn("Interchange vertex colour option not set (%s); HideMask will be missing" % exc)
    try:
        skel_props = pipeline.get_editor_property("common_skeletal_meshes_and_animations_properties")
        _set(skel_props, "skeleton", skeleton)
        _set(skel_props, "import_only_animations", False)
    except Exception as exc:  # noqa: BLE001
        warn("Interchange skeleton option not set (%s)" % exc)
    try:
        mat_p = pipeline.get_editor_property("material_pipeline")
        _set(mat_p, "import_materials", False)                    # UNVERIFIED property name
        tex_p = mat_p.get_editor_property("texture_pipeline")
        _set(tex_p, "import_textures", False)
    except Exception as exc:  # noqa: BLE001
        warn("Interchange material option not set (%s); delete the generated materials by hand" % exc)

    manager = unreal.InterchangeManager.get_interchange_manager_scripted()
    source = unreal.InterchangeManager.create_source_data(path)
    params = unreal.ImportAssetParameters()
    params.set_editor_property("is_automated", True)
    _set(params, "replace_existing", True)                        # 5.4+
    _set(params, "destination_name", name)                        # 5.4+
    # 5.3+: override_pipelines is Array[SoftObjectPath]; a transient pipeline is referenced by path.
    params.get_editor_property("override_pipelines").append(unreal.SoftObjectPath(pipeline.get_path_name()))
    ok = manager.import_asset(dest_folder, source, params)
    LOG("Interchange import of %s started: %s (asynchronous; run the script again to finish)" % (path, ok))
    return unreal.load_asset("%s/%s" % (dest_folder, name))


def import_mesh(path, dest_folder, name, skeleton):
    existing = unreal.load_asset("%s/%s" % (dest_folder, name)) if EAL.does_asset_exist("%s/%s" % (dest_folder, name)) else None
    if existing is not None and not REIMPORT:
        LOG("%s already exists, skipping import (REIMPORT = False)" % name)
        return existing
    if not os.path.isfile(path):
        warn("Missing FBX %s" % path)
        return existing
    if USE_INTERCHANGE:
        return import_fbx_interchange(path, dest_folder, name, skeleton)
    return import_fbx_legacy(path, dest_folder, name, skeleton)


# --------------------------------------------------------------------------------------------
# 2. Materials by slot name


def assign_materials(mesh, slot_to_path):
    mats = list(mesh.get_editor_property("materials"))
    found = set()
    for i, sm in enumerate(mats):
        slot = str(sm.get_editor_property("material_slot_name"))
        if slot not in slot_to_path:
            continue
        mi = unreal.load_asset(slot_to_path[slot])
        if mi is None:
            warn("%s: %s not found; run setup_comic_look.py first" % (mesh.get_name(), slot_to_path[slot]))
            continue
        new = unreal.SkeletalMaterial()
        new.set_editor_property("material_interface", mi)
        new.set_editor_property("material_slot_name", slot)
        try:
            new.set_editor_property("uv_channel_data", sm.get_editor_property("uv_channel_data"))
        except Exception:  # noqa: BLE001
            pass
        mats[i] = new
        found.add(slot)
    missing = set(slot_to_path) - found
    if missing:
        warn("%s: slots %s not found in the mesh (slots are %s)" % (
            mesh.get_name(), sorted(missing), [str(m.get_editor_property("material_slot_name")) for m in mats]))
    mesh.set_editor_property("materials", mats)
    report("%s materials: %s" % (mesh.get_name(), ", ".join(
        "%s=%s" % (str(m.get_editor_property("material_slot_name")),
                   m.get_editor_property("material_interface").get_name()
                   if m.get_editor_property("material_interface") else "None") for m in mats)))


# --------------------------------------------------------------------------------------------
# 3. LODs


def _regenerate(mesh, count):
    """SkeletalMeshEditorSubsystem.regenerate_lod (5.0+), EditorSkeletalMeshLibrary before that."""
    try:
        sub = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
        return sub.regenerate_lod(mesh, new_lod_count=count, regenerate_even_if_imported=False,
                                  generate_base_lod=False)
    except Exception as exc:  # noqa: BLE001
        LOG("SkeletalMeshEditorSubsystem.regenerate_lod unavailable (%s); trying EditorSkeletalMeshLibrary" % exc)
        return unreal.EditorSkeletalMeshLibrary.regenerate_lod(mesh, count, False, False)


def setup_lods(mesh):
    """LOD0 + len(LOD_PERCENTS) generated LODs. Reduction settings live in
    SkeletalMesh.lod_info[i].reduction_settings (SkeletalMeshOptimizationSettings)."""
    count = 1 + len(LOD_PERCENTS)
    try:
        if not _regenerate(mesh, count):          # first pass creates the LODInfo entries
            warn("%s: regenerate_lod returned False (mesh reduction module missing?)" % mesh.get_name())
            return
        infos = list(mesh.get_editor_property("lod_info"))
        if len(infos) < count:
            warn("%s: expected %d LOD entries, found %d" % (mesh.get_name(), count, len(infos)))
            return
        crit = _first_attr(unreal.SkeletalMeshTerminationCriterion, "SMTC_NUM_OF_TRIANGLES",
                           "SMTC_NUMOF_TRIANGLES")  # UNVERIFIED spelling
        for i, pct in enumerate(LOD_PERCENTS, start=1):
            info = infos[i]
            rs = info.get_editor_property("reduction_settings")
            rs.set_editor_property("termination_criterion", crit)
            rs.set_editor_property("num_of_triangles_percentage", pct)
            _set(rs, "base_lod", 0)
            info.set_editor_property("reduction_settings", rs)      # structs are copies: write back
            _set(info, "screen_size", unreal.PerPlatformFloat(LOD_SCREEN_SIZES[i]))
            infos[i] = info
        _set(infos[0], "screen_size", unreal.PerPlatformFloat(LOD_SCREEN_SIZES[0]))
        mesh.set_editor_property("lod_info", infos)
        _regenerate(mesh, count)                   # second pass builds with our percentages
        report("%s LODs: %d (LOD1-3 at %s %% of triangles)" % (
            mesh.get_name(), count, "/".join(str(int(p * 100)) for p in LOD_PERCENTS)))
    except Exception as exc:  # noqa: BLE001
        warn("%s: LOD generation failed (%s). Do it by hand: Asset Details > LOD Settings > "
             "Number of LODs = 4, Apply Changes, then per LOD set Percent of Triangles 0.5 / 0.25 / 0.1"
             % (mesh.get_name(), exc))


# --------------------------------------------------------------------------------------------
# 4. Verification


def morph_names(mesh):
    try:
        return [str(n) for n in mesh.get_all_morph_target_names()]
    except Exception:  # noqa: BLE001
        return [m.get_name() for m in (mesh.get_editor_property("morph_targets") or [])]


def verify_body(mesh):
    names = morph_names(mesh)
    exp = [n for n in names if n.startswith("Exp_")]
    sliders = [n for n in names if not n.startswith("Exp_")]
    ok_m = len(names) == EXPECTED_MORPHS and len(exp) == EXPECTED_EXPRESSIONS
    report("%s morph targets: %d (%d sliders + %d Exp_*) %s" % (
        mesh.get_name(), len(names), len(sliders), len(exp), "OK" if ok_m else "EXPECTED %d" % EXPECTED_MORPHS))
    if not ok_m:
        warn("%s: morph target count wrong; was Import Morph Targets on? Names: %s" % (mesh.get_name(), names))

    skel = mesh.get_editor_property("skeleton")
    if skel is None:
        warn("%s has no skeleton" % mesh.get_name())
        return
    bones = list(skel.get_editor_property("bone_tree"))
    bone_names = []
    for b in bones:
        try:
            bone_names.append(str(b.get_editor_property("name")))
        except Exception:  # noqa: BLE001
            pass
    ok_b = len(bones) == EXPECTED_BONES
    report("%s skeleton: %s, %d bones %s" % (mesh.get_name(), skel.get_path_name(), len(bones),
                                             "OK" if ok_b else "EXPECTED %d" % EXPECTED_BONES))
    if skel.get_path_name().split(".")[0] != skeleton_path():
        warn("%s does not use the shared skeleton %s" % (mesh.get_name(), skeleton_path()))
    if bone_names:
        missing = [b for b in FACE_BONES if b not in bone_names]
        if missing:
            warn("%s: face bones missing from the skeleton: %s" % (mesh.get_name(), missing))
    # Vertex colours: the legacy importer stores the option it used in the asset import data.
    try:
        data = mesh.get_editor_property("asset_import_data")
        opt = data.get_editor_property("vertex_color_import_option")
        report("%s vertex colour import option: %s" % (mesh.get_name(), opt))
    except Exception:  # noqa: BLE001
        pass
    try:
        report("%s LOD count: %d" % (mesh.get_name(), len(mesh.get_editor_property("lod_info"))))
    except Exception:  # noqa: BLE001
        pass


def verify_extra(mesh):
    names = morph_names(mesh)
    skel = mesh.get_editor_property("skeleton")
    report("%s: %d morph targets, skeleton %s" % (mesh.get_name(), len(names),
                                                 skel.get_path_name() if skel else "None"))
    if skel is None or skel.get_path_name().split(".")[0] != skeleton_path():
        warn("%s does not use the shared skeleton; re-export it with the base body armature" % mesh.get_name())


# --------------------------------------------------------------------------------------------
# Main


def main():
    base = art_dir()
    LOG("Importing characters from %s into %s" % (base, ASSET_ROOT))
    if not EAL.does_directory_exist(ASSET_ROOT):
        EAL.make_directory(ASSET_ROOT)

    skeleton = unreal.load_asset(skeleton_path()) if EAL.does_asset_exist(skeleton_path()) else None
    bodies = {}
    for body in BODIES:
        name = "SK_BaseBody_%s" % body
        mesh = import_mesh(os.path.join(base, "Base", name + ".fbx"), ASSET_ROOT, name, skeleton)
        if mesh is None:
            warn("%s was not imported; stopping (with Interchange, run the script again)" % name)
            return finish()
        bodies[body] = mesh
        if skeleton is None:
            skeleton = mesh.get_editor_property("skeleton")
            if skeleton is None:
                warn("%s has no skeleton; stopping" % name)
                return finish()
            # Keep the shared skeleton at a predictable path for the other imports.
            if skeleton.get_path_name().split(".")[0] != skeleton_path():
                EAL.rename_asset(skeleton.get_path_name().split(".")[0], skeleton_path())
                skeleton = unreal.load_asset(skeleton_path())
            report("Shared skeleton: %s" % skeleton.get_path_name())

    for body, mesh in bodies.items():
        assign_materials(mesh, SLOT_MATERIALS["SK_BaseBody_%s" % body])
        setup_lods(mesh)
        verify_body(mesh)
        EAL.save_loaded_asset(mesh)

    for folder in EXTRA_FOLDERS:
        src = os.path.join(base, folder)
        if not os.path.isdir(src):
            continue
        dest = "%s/%s" % (ASSET_ROOT, folder)
        if not EAL.does_directory_exist(dest):
            EAL.make_directory(dest)
        for fname in sorted(os.listdir(src)):
            if not fname.lower().endswith(".fbx"):
                continue
            name = os.path.splitext(fname)[0]
            mesh = import_mesh(os.path.join(src, fname), dest, name, skeleton)
            if mesh is None:
                continue
            assign_materials(mesh, SLOT_MATERIALS.get(name, {}))
            setup_lods(mesh)
            verify_extra(mesh)
            EAL.save_loaded_asset(mesh)

    EAL.save_loaded_asset(skeleton)
    return finish()


def finish():
    LOG("---- import_characters report ----")
    for line in _report:
        LOG("  " + line)
    if _warnings:
        LOG("---- %d warning(s) ----" % len(_warnings))
        for w in _warnings:
            unreal.log_warning("  " + w)
    LOG("Next: Docs/Art/Character_Assembly.md (BP_PlayerCharacter, character creator, face AnimBP layer).")


# "Execute Python Script" does not reliably set __name__ to "__main__", so always run.
main()
