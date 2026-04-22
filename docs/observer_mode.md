# Custom Walker Observer Mode

## Why Observer Mode

The current DAMOS idea is not that custom walkers must physically travel to
resolve every abnormal situation. For Track 1, the useful behavior is:

1. Scenic creates an abnormal situation.
2. DAMOS spawns custom observers near the abnormal anchor.
3. Each observer faces the anchor.
4. Observer metadata and future sensor buffers can be shared with `ego`.
5. `ego` can react to risk that is outside its direct visible area.

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

Default observer requirements:

| Requirement | Default |
|---|---:|
| Max observer-anchor distance | 22.0 m |
| Max yaw error toward anchor | 35.0 deg |

## Latest vvu Validation

Validated on `vvu:/home/vvu/vv/DAMOS/Carla-0.9.16-source`.

| Run | Port | Observer-anchor distances | Yaw errors | Result |
|---|---:|---|---|---|
| 1 | 2195 | 5.516 m, 6.397 m | 0.0 deg, 0.0 deg | Passed |
| 2 | 2196 | 6.381 m, 8.485 m | 0.0 deg, 0.0 deg | Passed |
| 3 | 2197 | 6.806 m, 11.401 m | 0.0 deg, 0.0 deg | Passed |

The observer actors did not move in these runs; that is expected.
