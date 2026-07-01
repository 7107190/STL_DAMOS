import pathlib
import tempfile
import zipfile

import unreal


SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[2]
ASSET_ROOT = SOURCE_ROOT / "_DAMOS" / "3d_model"

DELIVERYBOT_FBX = pathlib.Path(
    ASSET_ROOT / "delivery-bot-by-glowbox" / "source" / "DeliveryBot.fbx"
)
HUMANOID_ZIP = pathlib.Path(ASSET_ROOT / "mixamo-bot-character-lowpoly.zip")

DELIVERYBOT_DEST = "/Game/Damos/Walkers/DeliveryBot"
HUMANOID_DEST = "/Game/Damos/Walkers/Humanoid"
HUMANOID_MATERIAL_NAME = "M_DamosHumanoidPBR"
HUMANOID_TEXTURE_NAMES = {
    "base_color": "CHR_R_maximRed_MAT_baseColor",
    "normal": "CHR_R_maximRed_MAT_normal",
    "roughness": "CHR_R_maximRed_MAT_roughness",
    "metallic": "CHR_R_maximRed_MAT_metallic",
}


def make_import_task(
    filename: str,
    destination_path: str,
    import_as_skeletal: bool,
    import_textures: bool = True,
    import_materials: bool = True,
) -> unreal.AssetImportTask:
    options = unreal.FbxImportUI()
    options.set_editor_property("automated_import_should_detect_type", False)
    options.set_editor_property(
        "mesh_type_to_import",
        unreal.FBXImportType.FBXIT_SKELETAL_MESH
        if import_as_skeletal
        else unreal.FBXImportType.FBXIT_STATIC_MESH,
    )
    options.set_editor_property("import_mesh", True)
    options.set_editor_property("import_textures", import_textures)
    options.set_editor_property("import_materials", import_materials)
    options.set_editor_property("import_as_skeletal", import_as_skeletal)
    if import_as_skeletal:
        options.set_editor_property("import_animations", False)
        options.set_editor_property("create_physics_asset", False)
    else:
        options.static_mesh_import_data.set_editor_property("combine_meshes", True)

    task = unreal.AssetImportTask()
    task.set_editor_property("filename", filename)
    task.set_editor_property("destination_path", destination_path)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("save", True)
    task.set_editor_property("options", options)
    return task


def ensure_directory(path: str) -> None:
    if not unreal.EditorAssetLibrary.does_directory_exist(path):
        unreal.EditorAssetLibrary.make_directory(path)


def import_asset(task: unreal.AssetImportTask) -> None:
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    if task.imported_object_paths:
        unreal.log(f"Imported assets into {task.destination_path}: {task.imported_object_paths}")
    else:
        unreal.log_warning(f"No assets reported for import task {task.filename}")


def import_file(filename: pathlib.Path, destination_path: str) -> None:
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", str(filename))
    task.set_editor_property("destination_path", destination_path)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("save", True)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    if task.imported_object_paths:
        unreal.log(f"Imported helper asset into {destination_path}: {task.imported_object_paths}")
    else:
        unreal.log_warning(f"No helper asset reported for import task {filename}")


def load_asset(asset_path: str):
    asset = unreal.load_asset(asset_path)
    if asset is None:
        raise RuntimeError(f"Failed to load asset {asset_path}")
    return asset


def texture_path(texture_name: str) -> str:
    return f"{HUMANOID_DEST}/{texture_name}"


def configure_texture(texture, *, normal_map: bool = False, srgb: bool = True) -> None:
    texture.set_editor_property("srgb", srgb)
    if normal_map:
        texture.set_editor_property(
            "compression_settings",
            unreal.TextureCompressionSettings.TC_NORMALMAP,
        )
    unreal.EditorAssetLibrary.save_loaded_asset(texture)


def connect_texture(
    material: unreal.Material,
    texture_path_name: str,
    property_name: unreal.MaterialProperty,
    x_pos: int,
    y_pos: int,
    output_name: str = "RGB",
    *,
    sampler_type=None,
) -> None:
    expression = unreal.MaterialEditingLibrary.create_material_expression(
        material, unreal.MaterialExpressionTextureSample, x_pos, y_pos
    )
    expression.set_editor_property("texture", load_asset(texture_path_name))
    if sampler_type is not None:
        expression.set_editor_property("sampler_type", sampler_type)
    unreal.MaterialEditingLibrary.connect_material_property(expression, output_name, property_name)


def connect_constant(
    material: unreal.Material,
    value: float,
    property_name: unreal.MaterialProperty,
    x_pos: int,
    y_pos: int,
) -> None:
    expression = unreal.MaterialEditingLibrary.create_material_expression(
        material, unreal.MaterialExpressionConstant, x_pos, y_pos
    )
    expression.set_editor_property("r", value)
    unreal.MaterialEditingLibrary.connect_material_property(expression, "", property_name)


def rebuild_humanoid_material() -> None:
    material_path = f"{HUMANOID_DEST}/{HUMANOID_MATERIAL_NAME}"
    if unreal.EditorAssetLibrary.does_asset_exist(material_path):
        unreal.EditorAssetLibrary.delete_asset(material_path)

    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        HUMANOID_MATERIAL_NAME,
        HUMANOID_DEST,
        unreal.Material,
        unreal.MaterialFactoryNew(),
    )
    if material is None:
        raise RuntimeError("Failed to create humanoid material asset")

    connect_texture(
        material,
        texture_path(HUMANOID_TEXTURE_NAMES["base_color"]),
        unreal.MaterialProperty.MP_BASE_COLOR,
        -600,
        -250,
    )
    connect_texture(
        material,
        texture_path(HUMANOID_TEXTURE_NAMES["normal"]),
        unreal.MaterialProperty.MP_NORMAL,
        -600,
        -50,
        sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL,
    )
    connect_texture(
        material,
        texture_path(HUMANOID_TEXTURE_NAMES["roughness"]),
        unreal.MaterialProperty.MP_ROUGHNESS,
        -600,
        150,
        output_name="R",
    )
    connect_texture(
        material,
        texture_path(HUMANOID_TEXTURE_NAMES["metallic"]),
        unreal.MaterialProperty.MP_METALLIC,
        -600,
        350,
        output_name="R",
    )
    connect_constant(material, 0.3, unreal.MaterialProperty.MP_SPECULAR, -400, 500)

    needs_recompile = unreal.MaterialEditingLibrary.set_material_usage(
        material,
        unreal.MaterialUsage.MATUSAGE_SKELETAL_MESH,
    )
    unreal.log(f"Enabled skeletal mesh usage for {material_path}: needs_recompile={needs_recompile}")

    unreal.MaterialEditingLibrary.layout_material_expressions(material)
    unreal.MaterialEditingLibrary.recompile_material(material)
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    unreal.log(f"Rebuilt humanoid material: {material_path}")


def main() -> None:
    ensure_directory("/Game/Damos")
    ensure_directory("/Game/Damos/Walkers")
    ensure_directory(DELIVERYBOT_DEST)
    ensure_directory(HUMANOID_DEST)

    import_asset(make_import_task(str(DELIVERYBOT_FBX), DELIVERYBOT_DEST, False))

    with tempfile.TemporaryDirectory(prefix="damos_humanoid_") as temp_dir:
        temp_root = pathlib.Path(temp_dir)
        with zipfile.ZipFile(HUMANOID_ZIP) as archive:
            archive.extractall(temp_root)
        humanoid_fbx = next(temp_root.rglob("CHR_R_Maxim.fbx"))
        import_asset(
            make_import_task(
                str(humanoid_fbx),
                HUMANOID_DEST,
                True,
                import_textures=False,
                import_materials=False,
            )
        )

        for texture_file in sorted((temp_root / "textures").iterdir()):
            if texture_file.is_file():
                import_file(texture_file, HUMANOID_DEST)

    configure_texture(load_asset(texture_path(HUMANOID_TEXTURE_NAMES["base_color"])), srgb=True)
    configure_texture(load_asset(texture_path(HUMANOID_TEXTURE_NAMES["normal"])), normal_map=True, srgb=False)
    configure_texture(load_asset(texture_path(HUMANOID_TEXTURE_NAMES["roughness"])), srgb=False)
    configure_texture(load_asset(texture_path(HUMANOID_TEXTURE_NAMES["metallic"])), srgb=False)
    rebuild_humanoid_material()

    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    unreal.log("DAMOS walker import finished.")


if __name__ == "__main__":
    main()
