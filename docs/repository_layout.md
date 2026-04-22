# Repository Layout

This repository is organized as a DAMOS source overlay rather than a full
CARLA/Unreal/Scenic vendor checkout.

## Active Layout

| Path | Status | Notes |
|---|---|---|
| `README.md` | Active | Top-level orientation and run command |
| `CLAUDE.md` | Active | AI-assisted coding guardrails |
| `_DAMOS/` | Active | Project runtime scripts and Scenic scenarios |
| `Scenic/Maps/` | Active asset | OpenDRIVE maps required by Scenic wrappers |
| `Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/` | Active source overlay | DAMOS custom walker Unreal code |
| `docs/` | Active docs | Design and workspace documentation |
| `archive/` | Historical | Old Scenic experiments and run records |

## Archive Policy

| Archive Path | Contents | Why Archived |
|---|---|---|
| `archive/legacy_scenic_python/` | Old standalone CARLA/Scenic Python scripts | Superseded by `_DAMOS/scripts` |
| `archive/legacy_scenic_records/` | Old Scenic run record files | Generated/reference artifacts, not runtime source |
| `archive/legacy_scenic_scenarios/` | Old root `Scenic/_scenarios` copy | Superseded by `_DAMOS/_scenarios` with active path handling |

Archived files are kept for traceability. New implementation should not import
or run from `archive/`.

## Upload Scope

When syncing from vvu to GitHub, include:

- `_DAMOS/README.md`
- `_DAMOS/scripts`
- `_DAMOS/_scenarios`
- `_DAMOS/custom_walkers`
- `Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos`
- docs that explain the current direction

Exclude generated outputs and full vendor trees.
