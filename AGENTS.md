# DAMOS Agent Guidelines

These notes keep AI-assisted edits aligned with the current DAMOS repository
layout and project direction.

## Project Rules

| Rule | Rationale |
|---|---|
| Treat Notion `SDV/DAMOS` as the project direction source | The repo mirrors implementation state, not every planning decision |
| Keep Track 1 lightweight | The target is an 18-second integration loop, so avoid heavy planning or model inference inside final control |
| Keep Track 2 offline | 5-second trajectory prediction uses CARLA GT and must not slow the Track 1 loop |
| Use custom walkers as observers by default | The current idea is coverage sharing near abnormal Scenic anchors, not walker navigation |
| Keep full CARLA/UE/Scenic vendor trees out of git | This repo is a DAMOS source overlay |

## Active Workspace

Use vvu as the runtime source of truth:

```text
/home/vvu/vv/DAMOS/Carla-0.9.16-source
```

The active DAMOS folder is:

```text
/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS
```

In this GitHub repository, that path is mirrored as:

```text
Carla-0.9.16-source/_DAMOS
```

## Validation Before Publishing

Run the checks that match the touched files:

```bash
python3 -m py_compile Carla-0.9.16-source/_DAMOS/scripts/*.py
bash -n Carla-0.9.16-source/_DAMOS/scripts/*.sh
git diff --check
```

For observer-mode runtime changes, validate on vvu with:

```bash
Carla-0.9.16-source/_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh --restart --headless
```

Use `--walker-mode` only when checking that the custom walker assets/controllers
can still move.

## Commit Scope

Do not commit generated runtime artifacts:

- `Carla-0.9.16-source/_DAMOS/logs`
- `Carla-0.9.16-source/_DAMOS/reports`
- `__pycache__`
- `*.pyc`
- full CARLA/Unreal/Scenic vendor checkouts
- large model source assets unless explicitly packaged for release
