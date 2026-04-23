# DAMOS Custom Walkers

This folder contains the DAMOS-specific runtime scripts, custom walker helpers,
Scenic integration launchers, and active scenario definitions.

## Active Workspace

All ongoing work should be treated as running on `Host vvu`.

Canonical workspace root:

- `/home/vvu/vv/DAMOS`

Canonical DAMOS workspace inside the source tree:

- `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS`

Compatibility links still exist for older commands:

- `/home/vvu/vv/Carla-0.9.16`
- `/home/vvu/vv/Carla-0.9.16-source`
- `/home/vvu/vv/UnrealEngine_4.26`
- `/home/vvu/vv/Carla-0.9.16/_DAMOS`

`Scenic/` inside the source tree is the Scenic checkout/path used by the wrappers so
map assets and scenario-relative paths still work.

## Implemented Custom Walkers

- `walker.pedestrian.damos_deliverybot`
- `walker.pedestrian.damos_humanoid`

Current behavior:

- `damos_deliverybot` uses wheelchair-capable walker behavior so it can travel on sidewalks.
- `damos_humanoid` uses pedestrian walker behavior with runtime pose sync from the hidden CARLA base walker.
- Town01 demo mode uses fixed spawn points so both walkers are easy to find visually.
- Scenic integration injects custom walkers near Scenic-created anchors instead of random map positions.
- Scenic integration now treats those custom walkers as fixed DAMOS observer nodes by default:
  they spawn near abnormal-situation anchors, face the anchor, and provide coverage metadata
  for ego/custom-walker cooperation.

## One-command Town01 Demo

From the source-build repository root:

```bash
_DAMOS/scripts/run_custom_walkers_demo.sh
```

This launcher:

- starts or reuses the Town01 source-build server on port `2000`
- waits for the server to become ready
- spawns the DAMOS walkers
- moves the spectator to a fixed demo view

## Scenic Integration Check

Without editing `S_.scenic`, you can run the Town10HD Scenic scenario family and inject
custom walkers from the outside:

```bash
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh --restart --headless
```

For a GUI-free pass/fail check, run:

```bash
_DAMOS/scripts/check_scenic_observers_headless.sh --port 2212 --scenic-time 8
```

This starts the same headless integration, then prints a table summary from the
latest JSON report for that port.

Use `--max-anchor-pairs N` only when you intentionally want to cap observer pairs.
When omitted, every semantic anchor candidate is covered.

This launcher:

- starts or reuses the source-build server on `Town10HD_Opt`
- runs `_DAMOS/_scenarios/S_.scenic`
- waits for Scenic to spawn the `ego`
- waits for Scenic support actors to appear
- reduces Scenic support actors into semantic abnormal anchors and places one
  `damos_humanoid` plus one `damos_deliverybot` at every semantic anchor by default
- attaches six RGB observer cameras to each custom observer using the walker
  camera mount positions from `/home/vvu/vv/DAMOS/sensor_config.txt`
- validates observer placement and yaw toward each Scenic anchor
- saves trajectory PNG, focus PNG, and JSON reports under `_DAMOS/reports`

The default mode matches the current DAMOS Track 1 idea: the custom walkers do not
need to travel through the scene. They act as extra observer nodes near the Scenic
abnormal situation and share their local view/coverage metadata with `ego`.

Movement is still available as a smoke test for walker assets:

```bash
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh --restart --headless --walker-mode
```

The launcher keeps `S_.scenic` unchanged and passes the active map through CLI overrides:

- `carla_map=Town10HD_Opt`
- `map=Scenic/Maps/Town10HD_Opt.xodr`
- `reload_world=0`

## Reports

The saved trajectory reports include:

- light gray road-network waypoint backdrop
- Scenic `ego` path
- `damos_deliverybot` path
- `damos_humanoid` path
- selected Scenic anchor markers
- Scenic-created pedestrian, bicycle, and vehicle paths when available
- start/end labels for `ego`, `deliverybot`, and `humanoid`
- cooperation metadata in JSON form
- observer-mode metadata: observer-to-anchor distance, yaw error toward the anchor,
  and ego-to-anchor distance
- observer camera metadata: six camera names, relative transforms, and attached
  CARLA sensor actor ids for each observer

Recent validated headless report example:

- `_DAMOS/reports/scenic_custom_walkers_Town10HD_Opt_S7_S4_S5_S1_port2204_20260422-203628.json`

## Default Root Resolution

By default the launchers resolve their roots from the script location:

- Unreal server: `/home/vvu/vv/DAMOS/UnrealEngine_4.26`
- CARLA source tree: `/home/vvu/vv/DAMOS/Carla-0.9.16-source`
- Python: `/home/vvu/anaconda3/envs/carla4/bin/python`

You can still override Python when needed:

```bash
CARLA_PYTHON_BIN=/path/to/python _DAMOS/scripts/run_custom_walkers_demo.sh
```

## Important Files

- `_DAMOS/scripts/run_custom_walkers_demo.sh`
- `_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh`
- `_DAMOS/scripts/check_scenic_observers_headless.sh`
- `_DAMOS/scripts/verify_observer_report.py`
- `_DAMOS/scripts/run_scenic_with_custom_walkers.py`
- `_DAMOS/scripts/custom_walker_runtime.py`
- `_DAMOS/scripts/scenic_custom_walker_injector.py`
- `_DAMOS/unreal/import_damos_walkers.py`
- `_DAMOS/custom_walkers/ue4_env.sh`
- `Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/DamosWalkerFactory.cpp`
- `Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/DamosBoneMapPoseComponent.cpp`

## Reusable Entry Points

Runtime helper:

1. `connect_to_world(...)`
2. `spawn_all_custom_walkers(...)`
3. `orient_spawned_walkers_to_anchors(...)`
4. `stop_spawned_walker_controllers(...)`
5. `initialize_custom_walker_movement(...)`
6. `run_custom_walker_movement(...)`
7. `destroy_spawned_walkers(...)`

Scenic-side injector:

1. Build a `ScenicCustomWalkerConfig`
2. Call `run_scenic_custom_walker_integration(config)`
3. Reuse the returned `ScenicCustomWalkerResult` for logging or higher-level control

## Current Development Direction

The workspace is already past the environment-setup stage.
The main remaining work is DAMOS behavior integration, for example:

- ego/custom-observer cooperation data flow
- mapping observer metadata into 6-frame image buffering / occupancy inputs
- connecting attached observer camera streams to the 6-frame image buffer
- attaching the injector to the real DAMOS execution pipeline
- turning observer JSON/PNG output into experiment-facing reports
