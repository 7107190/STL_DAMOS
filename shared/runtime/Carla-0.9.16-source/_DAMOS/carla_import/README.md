# DAMOS CARLA Import Prep

This folder prepares a CARLA-compatible import package from the models stored in
`_DAMOS/3d_model`.

What is ready now:

- A manifest classifying each model as either a static-prop candidate or a
  manual-review asset.
- A generator script which stages a CARLA import package under `Import/`.

What is blocked in this workspace:

- Actual Unreal import is not possible here because this CARLA package does not
  include Unreal Editor or the source-build `make import` workflow.

Current static-prop package:

- `Import/DAMOSProps/DAMOSProps.json`
- `Import/DAMOSProps/Props/...`

Manual-review assets were intentionally excluded from the generated package:

- `SpotHPRIG.fbx`: likely rigged/skeletal.
- `CHR_R_Maxim.fbx`: likely walker/android pipeline.
- `2.blend`
- `robot_riged_model_1.blend`

Suggested next step on a CARLA source-build machine with Unreal import tooling:

1. Copy the generated `Import/DAMOSProps` package into that workspace.
2. Run the CARLA import workflow documented in the official CARLA content authoring docs.
3. Verify the imported prop blueprint IDs inside CARLA and then wire them into Scenic.

Likely blueprint IDs after import are inferred to follow the usual CARLA naming
pattern:

- `static.prop.deliverybot`
- `static.prop.sweeper`
- `static.prop.spacewalker4`
- `static.prop.toonspaceship`

Those IDs are an inference from existing CARLA prop naming and still need to be
confirmed after the actual Unreal-side import.
