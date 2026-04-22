# DAMOS Unreal Source Overlay

This folder contains the CARLA Unreal-side source overlay for DAMOS custom
walkers.

| File | Purpose |
|---|---|
| `DamosWalkerFactory.*` | Registers/spawns DAMOS custom walker blueprints |
| `DamosBoneMapPoseComponent.*` | Synchronizes visible custom mesh pose from the hidden CARLA walker base |
| `DamosVisibilityLockComponent.*` | Keeps DAMOS custom walker visibility stable at runtime |

This is not a standalone Unreal project. Copy or keep this folder under the
CARLA source-build tree:

```text
Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos
```

The active runtime scripts that exercise these walkers are in `_DAMOS/scripts`.
