# vvu Workspace

The active runtime environment is on `vvu`.

## Canonical Paths

| Path | Role | Commit to GitHub? |
|---|---|---|
| `/home/vvu/vv/DAMOS` | Top-level runtime workspace | No |
| `/home/vvu/vv/DAMOS/Carla-0.9.16-source` | Active CARLA source-build repo and DAMOS runtime root | No, only selected overlay files |
| `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS` | Active DAMOS scripts/scenarios/docs | Yes, selected files |
| `/home/vvu/vv/DAMOS/Carla-0.9.16-source/Scenic` | Scenic checkout/path used by wrappers | Only selected maps/scenarios |
| `/home/vvu/vv/DAMOS/UnrealEngine_4.26` | Unreal Engine dependency | No |
| `/home/vvu/vv/DAMOS/_archive` | Old packaged/runtime folders | No |

Compatibility links may exist under `/home/vvu/vv`, but new work should use the
canonical paths above.

## Folder Roles

| Folder | Why It Exists |
|---|---|
| `Carla-0.9.16-source` | The source-build CARLA tree where custom walker Unreal code and runtime scripts are tested |
| `UnrealEngine_4.26` | Engine dependency required by the CARLA source build |
| `Scenic` | Scenic package/maps used to generate abnormal CARLA scenes |
| `_DAMOS` | Project-specific wrapper scripts, custom walker runtime logic, and active scenario set |

## Do Not Upload Directly

Do not push these runtime directories wholesale to GitHub:

- full `Carla-0.9.16-source`
- full `UnrealEngine_4.26`
- full Scenic source checkout
- `_DAMOS/logs`
- `_DAMOS/reports`
- `_DAMOS/3d_model`

Instead, sync only the source overlay that belongs in this repository.
