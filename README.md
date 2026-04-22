# STL_DAMOS

DAMOS is a CARLA/Scenic simulation workspace for decentralized M2X cooperative
autonomous mobility. The project target is to let `ego` and custom mobility
observers share local information so that abnormal Scenic situations can be
handled even when the risk is outside ego's direct visible area.

This repository is the source overlay for DAMOS work and the canonical GitHub
view of the active vvu workspace. It is not a full CARLA, Unreal Engine, or
Scenic vendor checkout.

## Current Direction

| Area | Decision |
|---|---|
| Source of truth | Notion `SDV/DAMOS` project direction |
| Runtime workspace | `vvu:/home/vvu/vv/DAMOS/Carla-0.9.16-source` |
| Track 1 | Real-time M2X integration simulation, 18-second loop target |
| Track 2 | Offline 5-second trajectory prediction using CARLA GT |
| Custom walkers | Static observer nodes by default, not moving actors |
| Scenic abnormal events | Spawned through `Carla-0.9.16-source/_DAMOS/_scenarios/S_.scenic` |

## Repository Layout

| Path | Purpose |
|---|---|
| `Carla-0.9.16-source/_DAMOS/` | Active DAMOS scripts, Scenic scenario set, custom walker runtime docs |
| `Carla-0.9.16-source/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/` | DAMOS Unreal source overlay for custom walker support |
| `Carla-0.9.16-source/Scenic/Maps/` | OpenDRIVE maps used by the Scenic wrappers |
| `UnrealEngine_4.26/README.md` | Placeholder for the external Unreal Engine dependency |
| `docs/` | Project direction, vvu workspace notes, observer-mode design |
| `archive/` | Legacy Scenic scripts, old scenario copies, and old run records |

## Main Runtime Command

Run this from the vvu CARLA source-build repository root:

```bash
Carla-0.9.16-source/_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh --restart --headless
```

The default mode is observer mode:

| Mode | Command | Meaning |
|---|---|---|
| Observer mode | default, or `--observer-mode` | Custom walkers stay near Scenic abnormal anchors and face the event |
| Walker smoke test | `--walker-mode` | Custom walkers are routed and must move, used only to test assets/controllers |

## Observer Mode

In Track 1, the custom walkers do not need to travel through the map. They are
used as extra observer nodes around the abnormal situation. The integration
wrapper records:

- observer-to-anchor distance
- observer yaw error toward the anchor
- ego-to-anchor distance
- anchor assignment and cooperation metadata

The latest vvu validation passed three Scenic observer runs on ports `2195`,
`2196`, and `2197`; all observer yaw errors were `0.0` degrees.

See [docs/observer_mode.md](docs/observer_mode.md) for details.

## What This Repo Does Not Store

| Excluded | Reason |
|---|---|
| Full `Carla-0.9.16-source` checkout | Vendor/source build dependency, too large and not DAMOS-specific |
| Full `UnrealEngine_4.26` checkout | External engine dependency |
| Full Scenic source tree | External dependency; only maps/scenarios relevant to DAMOS are tracked |
| `Carla-0.9.16-source/_DAMOS/logs` and `reports` | Generated runtime artifacts |
| `Carla-0.9.16-source/_DAMOS/3d_model` source assets | Large model/import assets, kept on vvu unless explicitly packaged |

## Documentation

| Document | Content |
|---|---|
| [docs/project_direction.md](docs/project_direction.md) | Notion-based DAMOS direction and Kiwoong's responsibility |
| [docs/observer_mode.md](docs/observer_mode.md) | Why custom walkers are static observers and how validation works |
| [docs/vvu_workspace.md](docs/vvu_workspace.md) | vvu folder structure and what each external folder is for |
| [docs/repository_layout.md](docs/repository_layout.md) | GitHub repository structure and archive policy |
