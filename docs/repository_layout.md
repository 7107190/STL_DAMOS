# Repository Layout

This repository is organized as a DAMOS source overlay rather than a full
CARLA/Unreal/Scenic vendor checkout.

## Active Layout

| Path | Status | Notes |
|---|---|---|
| `README.md` | Active | Top-level orientation and run command |
| `CLAUDE.md` | Active | AI-assisted coding guardrails |
| `Carla-0.9.16-source/_DAMOS/` | Active | Project runtime scripts and Scenic scenarios |
| `Carla-0.9.16-source/Scenic/Maps/` | Active asset | OpenDRIVE maps required by Scenic wrappers |
| `Carla-0.9.16-source/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/` | Active source overlay | DAMOS custom walker Unreal code |
| `UnrealEngine_4.26/README.md` | Placeholder | Documents the external engine dependency |
| `docs/` | Active docs | Design and workspace documentation |
| `archive/` | Historical | Old Scenic experiments and run records |

## Archive Policy

| Archive Path | Contents | Why Archived |
|---|---|---|
| `archive/legacy_scenic_python/` | Old standalone CARLA/Scenic Python scripts | Superseded by `Carla-0.9.16-source/_DAMOS/scripts` |
| `archive/legacy_scenic_records/` | Old Scenic run record files | Generated/reference artifacts, not runtime source |
| `archive/legacy_scenic_scenarios/` | Old root `Scenic/_scenarios` copy | Superseded by `Carla-0.9.16-source/_DAMOS/_scenarios` with active path handling |

Archived files are kept for traceability. New implementation should not import
or run from `archive/`.

## Upload Scope

When syncing from vvu to GitHub, include:

- `Carla-0.9.16-source/_DAMOS/README.md`
- `Carla-0.9.16-source/_DAMOS/scripts`
- `Carla-0.9.16-source/_DAMOS/_scenarios`
- `Carla-0.9.16-source/_DAMOS/custom_walkers`
- `Carla-0.9.16-source/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos`
- docs that explain the current direction

Exclude generated outputs and full vendor trees.
