# DAMOS Project Direction

This document summarizes the current implementation direction from Notion
`SDV/DAMOS` and the vvu workspace state.

## Main Idea

`ego` should not be the only actor responsible for detecting a Scenic abnormal
situation. Custom mobility actors such as `damos_deliverybot` and
`damos_humanoid` can be placed near abnormal-event anchors and share local
coverage data with `ego`. This lets the system recover from risk in ego's
non-visible area.

The custom walkers do not need to walk for this Track 1 goal. Their primary role
is to behave as cooperative observer nodes.

## Tracks

| Track | Goal | Runtime Relationship |
|---|---|---|
| Track 1 | Real-time M2X integration simulation within the 18-second target | CARLA + Scenic + observer nodes + M2X exchange + rule-based control |
| Track 2 | 5-second future trajectory prediction from CARLA GT | Offline evaluation, separated from Track 1 runtime |

## Track 1 Pipeline

| Step | Owner Area | Output |
|---|---|---|
| Scenic abnormal situation | Scenario/runtime | Anchor point and abnormal actor context |
| Ego/custom observer capture | Main CARLA loop | Six-frame image buffers and local metadata |
| Local occupancy prediction | Sub PC model | 5-second occupancy estimate |
| M2X/ZK exchange | Communication layer | Shared compressed features / mobility state |
| Global fusion | Fusion layer | No-blind-spot occupancy map |
| Rule-based control | Main CARLA loop | Brake or straight command |

## Kiwoong Scope

| Responsibility | Current Repo Surface |
|---|---|
| CARLA main sync loop | To be attached to `Carla-0.9.16-source/_DAMOS/scripts` or a future main loop module |
| Actor spawning | `Carla-0.9.16-source/_DAMOS/scripts/custom_walker_runtime.py` |
| Scenic abnormal scenario execution | `Carla-0.9.16-source/_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh` |
| Six-frame image buffering | Future integration work |
| Track 2 GT support | Future extraction/export work |
| Track 1 final rule-based control | Future main loop integration |

## Current Implementation State

| Item | Status |
|---|---|
| vvu folder cleanup | Done |
| Scenic editable install in `carla4` | Done |
| Custom walker movement smoke test | Passed previously; now optional with `--walker-mode` |
| Custom observer mode | Implemented and validated 3 times |
| GitHub source overlay | This repository |
