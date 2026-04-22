# Custom Walker Observer Mode

## Why Observer Mode

The current DAMOS idea is not that custom walkers must physically travel to
resolve every abnormal situation. For Track 1, the useful behavior is:

1. Scenic creates an abnormal situation.
2. DAMOS selects Scenic support actors as abnormal anchors.
3. Each selected anchor receives one humanoid observer and one deliverybot observer.
4. Each observer faces the anchor.
5. Six RGB cameras are attached to each observer using the walker mount positions
   from `/home/vvu/vv/DAMOS/sensor_config.txt`.
6. Observer metadata and future sensor buffers can be shared with `ego`.
7. `ego` can react to risk that is outside its direct visible area.

Movement is still useful as an asset/controller smoke test, but it is not the
main Track 1 behavior.

## Runtime Modes

| Mode | Default | Validation |
|---|---|---|
| `observer-mode` | Yes | Observer placement distance and yaw toward anchor |
| `walker-mode` | No | Custom walker movement distance |

Run observer mode:

```bash
Carla-0.9.16-source/_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh --restart --headless
```

Run movement smoke test:

```bash
Carla-0.9.16-source/_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh --restart --headless --walker-mode
```

## Observer Metrics

The Scenic injector writes observer metadata into the JSON report:

| Field | Meaning |
|---|---|
| `custom_walker_mode` | `observer` or `walker` |
| `observer_to_anchor_distance` | Distance from observer actor to Scenic anchor |
| `observer_yaw_degrees` | Current observer yaw |
| `target_yaw_degrees` | Yaw needed to face the anchor |
| `facing_error_degrees` | Absolute yaw error |
| `ego_to_anchor_distance` | Distance from ego to the same abnormal anchor |
| `anchor_index` | Pair index for the selected Scenic anchor |
| `observer_role` | `humanoid` or `deliverybot` for the anchor pair |
| `attached_sensor_count` | Number of camera sensors attached to the observer |

The report also includes camera metadata:

| Field | Meaning |
|---|---|
| `observer_camera_specs` | Six camera mount transforms loaded from `sensor_config.txt` |
| `observer_camera_attachments` | CARLA sensor actor ids attached to each observer |

Default observer requirements:

| Requirement | Default |
|---|---:|
| Max observer-anchor distance | 22.0 m |
| Max yaw error toward anchor | 35.0 deg |

Default camera layout per observer:

| Camera | Relative location | Relative rotation |
|---|---|---|
| `cam_front` | `[0.0, 0.0, 1.5]` | `[0, 0, 0]` |
| `cam_front_left` | `[0.0, -0.1, 1.5]` | `[0, -55, 0]` |
| `cam_front_right` | `[0.0, 0.1, 1.5]` | `[0, 55, 0]` |
| `cam_back` | `[0.0, 0.0, 1.5]` | `[0, 180, 0]` |
| `cam_back_left` | `[0.0, -0.1, 1.5]` | `[0, -110, 0]` |
| `cam_back_right` | `[0.0, 0.1, 1.5]` | `[0, 110, 0]` |

## Latest vvu Validation

Validated on `vvu:/home/vvu/vv/DAMOS/Carla-0.9.16-source`.

| Run | Port | Observer-anchor distances | Yaw errors | Result |
|---|---:|---|---|---|
| 1 | 2195 | 5.516 m, 6.397 m | 0.0 deg, 0.0 deg | Passed |
| 2 | 2196 | 6.381 m, 8.485 m | 0.0 deg, 0.0 deg | Passed |
| 3 | 2197 | 6.806 m, 11.401 m | 0.0 deg, 0.0 deg | Passed |

The observer actors did not move in these runs; that is expected.

Additional anchor-pair and camera attachment checks:

| Run | Port | Anchor pairs | Observer cameras | Result |
|---|---:|---:|---:|---|
| 4 | 2201 | 1 | 12 | Passed |
| 5 | 2203 | 1 | 12 | Passed |
| 6 | 2204 | 1 | 12 | Passed |
