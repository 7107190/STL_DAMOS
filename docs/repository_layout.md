# Repository Layout

이 저장소는 전체 CARLA/Unreal/Scenic vendor checkout이 아니라 DAMOS source overlay입니다.

## Active Layout

| Path | Status | Notes |
|---|---|---|
| `README.md` | Active | 프로젝트 기준, 실행 명령, 운영 규칙 |
| `Carla-0.9.16-source/_DAMOS/` | Active | DAMOS runtime scripts, Scenic scenarios, custom observer logic |
| `Carla-0.9.16-source/Scenic/Maps/` | Active asset | Scenic wrapper 실행에 필요한 OpenDRIVE maps |
| `Carla-0.9.16-source/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/` | Active source overlay | DAMOS custom walker Unreal code |
| `UnrealEngine_4.26/README.md` | Placeholder | 외부 Unreal Engine dependency 위치 문서화 |
| `docs/` | Active docs | 프로젝트 방향, observer mode, vvu workspace 문서 |
| `archive/` | Placeholder | GitHub에는 대용량/구식 실험 파일을 올리지 않고 archive 정책만 문서화 |

`AGENTS.md`의 운영 규칙은 루트 `README.md`로 통합했습니다. 새 작업 기준 문서는 `README.md`입니다.

## Archive Policy

GitHub의 `archive/`에는 현재 `README.md`만 둡니다. 과거 Scenic 실험 파일, run records, capture 결과물은 active runtime source가 아니므로 GitHub mirror에서 제거합니다.

| 제외한 legacy 범위 | 대체 위치 |
|---|---|
| old standalone CARLA/Scenic Python scripts | `Carla-0.9.16-source/_DAMOS/scripts` |
| old Scenic run records | Git에 올리지 않음 |
| old root `Scenic/_scenarios` copy | `Carla-0.9.16-source/_DAMOS/_scenarios` |

New implementation should not import or run from `archive/`.

## Upload Scope

vvu에서 GitHub로 동기화할 때 포함할 범위:

| Include | Reason |
|---|---|
| `README.md` | 현재 프로젝트 기준 문서 |
| `sensor_config.txt` | ego/observer 6방향 camera mount 기준 |
| `docs/` | 설계/작업 문서 |
| `Carla-0.9.16-source/_DAMOS/README.md` | runtime wrapper 상세 |
| `Carla-0.9.16-source/_DAMOS/scripts` | 실행 스크립트 |
| `Carla-0.9.16-source/_DAMOS/_scenarios` | Scenic S1~S9 시나리오 |
| `Carla-0.9.16-source/_DAMOS/custom_walkers` | custom observer 관련 asset/runtime metadata |
| `Carla-0.9.16-source/_DAMOS/carla_import` | CARLA import package helper |
| `Carla-0.9.16-source/_DAMOS/unreal` | custom walker Unreal import helper |
| `Carla-0.9.16-source/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos` | Unreal custom walker source overlay |

제외할 범위:

| Exclude | Reason |
|---|---|
| full CARLA source/build tree | 대용량 vendor/build dependency |
| full Unreal Engine checkout | 외부 엔진 dependency |
| full Scenic checkout | 외부 dependency |
| `_DAMOS/logs` | 실행 로그 |
| `_DAMOS/reports` | 검증 이미지/JSON |
| `_DAMOS/_tmp_sync`, `_DAMOS/_tmp_upload` | 임시 동기화 파일 |
| `_DAMOS/backups`, `_DAMOS/legacy_stl_damos` | 로컬 백업/구식 mirror |
| `_DAMOS/3d_model` | 대용량 모델 원본 |
| `__pycache__`, `*.pyc` | Python 생성물 |
