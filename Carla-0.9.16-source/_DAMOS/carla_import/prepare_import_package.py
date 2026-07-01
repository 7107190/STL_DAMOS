#!/usr/bin/env python3
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = Path(__file__).resolve().with_name("manifest.json")


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    package_name = manifest["package_name"]
    package_root = ROOT / "Import" / package_name
    props_root = package_root / "Props"

    if package_root.exists():
        shutil.rmtree(package_root)

    props_root.mkdir(parents=True, exist_ok=True)

    props = []

    for asset in manifest["assets"]:
        if not asset["include_in_import_package"]:
            continue

        asset_dir = props_root / asset["display_name"]
        asset_dir.mkdir(parents=True, exist_ok=True)

        source_fbx = ROOT / asset["source_fbx"]
        if not source_fbx.is_file():
            raise FileNotFoundError(f"Missing FBX source: {source_fbx}")

        target_fbx = asset_dir / asset["target_fbx_name"]
        shutil.copy2(source_fbx, target_fbx)

        for texture in asset["source_textures"]:
            source_texture = ROOT / texture
            if not source_texture.is_file():
                raise FileNotFoundError(f"Missing texture source: {source_texture}")
            shutil.copy2(source_texture, asset_dir / source_texture.name)

        props.append(
            {
                "name": asset["display_name"],
                "tag": asset["tag"],
                "size": asset["size"],
                "source": str(Path("Props") / asset["display_name"] / asset["target_fbx_name"]),
            }
        )

    package_spec = {"maps": [], "props": props}
    spec_path = package_root / f"{package_name}.json"
    spec_path.write_text(json.dumps(package_spec, indent=2) + "\n")

    print(f"Prepared {package_name} import package at {package_root}")
    for prop in props:
        print(f"- {prop['name']} -> {prop['source']}")


if __name__ == "__main__":
    main()
