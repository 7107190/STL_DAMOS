# STL_DAMOS

DAMOS는 `Decentralized Autonomous Mobility Simulation`의 약자이며, 여러 모빌리티가 중앙 서버 없이 M2X 방식으로 정보를 주고받아 ego의 비가시영역에서 발생한 Scenic 비정상 상황까지 대응하는 협력 자율주행 시뮬레이션 프로젝트입니다.

이 저장소는 vvu 작업 폴더를 그대로 압축해 올린 백업이 아니라, DAMOS에 필요한 소스와 문서만 추적하는 GitHub 기준 저장소입니다. 대용량 CARLA/Unreal/Scenic 원본은 제외하고, 실제 작업 구조와 최대한 비슷하게 `Carla-0.9.16-source/` 아래에 DAMOS overlay를 배치했습니다.

## 프로젝트 기준

| 항목 | 내용 |
|---|---|
| 기준 문서 | Notion `SDV / DAMOS` |
| 핵심 아이디어 | ego와 custom observer가 정보를 공유해 비가시영역의 비정상 상황을 극복 |
| 런타임 기준 서버 | `vvu` |
| 실제 작업 루트 | `/home/vvu/vv/DAMOS` |
| CARLA 작업 루트 | `/home/vvu/vv/DAMOS/Carla-0.9.16-source` |
| GitHub mirror 구조 | `STL_DAMOS/Carla-0.9.16-source` |

## Track 구성

| Track | 목표 | 현재 해석 |
|---|---|---|
| Track 1 | M2X 실시간 통합 시뮬레이션, 18초 지표 방어 | CARLA + Scenic 비정상 상황 + custom observer + M2X 공유 + rule-based 제어 |
| Track 2 | CARLA GT 기반 5초 미래 궤적 예측 | Track 1 루프와 분리된 오프라인 정량 평가 |

## Track 1 파이프라인

| 단계 | 내용 | 담당 영역 |
|---|---|---|
| 1 | CARLA + Scenic으로 비정상 상황과 결함 상황 생성 | 메인 PC / Scenic |
| 2 | 각 모빌리티 시야에서 과거 6프레임 이미지를 추출 | 메인 PC |
| 3 | 6프레임 입력으로 미래 10프레임, 즉 5초 점유 영역 예측 | 서브 PC |
| 4 | 로컬 예측 결과와 이동 정보를 ZK 기반 M2X 네트워크로 공유 | 통신망 |
| 5 | 수신 데이터와 로컬 데이터를 융합해 사각지대 없는 글로벌 점유 지도 생성 | 융합 |
| 6 | 충돌 위험이면 브레이크, 아니면 직진하는 rule-based 제어 실행 | 메인 PC |

## 김기웅 담당 범위

Notion 기준 김기웅의 역할은 메인 시스템 통제와 통합입니다.

| 담당 | 상태 |
|---|---|
| CARLA 메인 동기화 루프 개발 | 다음 핵심 작업 |
| actor spawning 구조 | custom walker/runtime 기반 구축 완료 |
| 6프레임 이미지 버퍼링 | 다음 작업 |
| Track 1 rule-based 최종 제어 | 다음 작업 |
| Track 2용 GT 데이터 추출 지원 | 다음 작업 |
| 휴머노이드/배달로봇 생성 및 구동 | 3월 진행 내용 반영, 기본 검증 완료 |
| Scenic 비정상 상황 anchor 기반 custom walker 배치 | observer mode로 방향 확정 및 검증 완료 |

## 현재 진행상황

| 구분 | 상태 | 근거 |
|---|---|---|
| vvu 폴더 정리 | 완료 | `Carla-0.9.16-source`, `UnrealEngine_4.26`, `_archive` 기준으로 정리 |
| Scenic 실행 환경 | 완료 | `carla4` 환경에서 Scenic 3.1.0a1 확인 |
| custom walker asset/runtime | 완료 | `damos_deliverybot`, `damos_humanoid` 생성 및 이동 smoke test 통과 |
| observer mode | 완료 | custom walker를 이동체가 아니라 비정상 상황 anchor 주변 observer node로 사용 |
| observer 검증 | 완료 | Town10HD_Opt에서 3회 실행 통과, yaw error 0.0도 |
| GitHub 구조 정리 | 완료 | vvu 구조에 맞춰 `Carla-0.9.16-source/` 아래로 mirror |
| vvu 문서 동기화 | 완료 | root `README.md`, `AGENTS.md`, `docs/` 동기화 |

## Observer Mode 결정

초기에는 custom walker가 실제로 이동해야 하는지 확인했지만, 현재 DAMOS Track 1 아이디어에서는 반드시 이동할 필요가 없습니다.

핵심은 다음과 같습니다.

| 기존 관점 | 현재 결정 |
|---|---|
| custom walker가 Scenic 비정상 상황까지 이동해야 함 | 비정상 상황 anchor 주변에 고정 observer로 배치 |
| 이동거리 검증이 중요 | anchor 거리, anchor를 바라보는 yaw error, ego와의 공유 가능성이 중요 |
| walker navigation 중심 | ego/custom observer 간 데이터 공유 중심 |

현재 wrapper 기본값은 observer mode입니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh --restart --headless
```

walker asset/controller 이동성만 확인할 때는 다음처럼 실행합니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh --restart --headless --walker-mode
```

## 저장소 구조

GitHub 구조는 vvu의 `/home/vvu/vv/DAMOS`를 기준으로 맞췄습니다.

```text
STL_DAMOS/
├── AGENTS.md
├── README.md
├── Carla-0.9.16-source/
│   ├── _DAMOS/
│   ├── Scenic/
│   │   └── Maps/
│   └── Unreal/
│       └── CarlaUE4/Plugins/Carla/Source/Carla/Damos/
├── UnrealEngine_4.26/
│   └── README.md
├── archive/
└── docs/
```

| GitHub 경로 | vvu 실제 경로 | 설명 |
|---|---|---|
| `Carla-0.9.16-source/_DAMOS` | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS` | DAMOS 실행 스크립트와 시나리오 |
| `Carla-0.9.16-source/Scenic/Maps` | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/Scenic/Maps` | Scenic OpenDRIVE map |
| `Carla-0.9.16-source/Unreal/.../Damos` | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/Unreal/.../Damos` | Unreal custom walker source overlay |
| `UnrealEngine_4.26/README.md` | `/home/vvu/vv/DAMOS/UnrealEngine_4.26` | 실제 UE 전체는 추적하지 않고 위치만 표시 |
| `docs/` | `/home/vvu/vv/DAMOS/docs` | 프로젝트 방향과 작업 문서 |

## Git에 올리지 않는 것

| 제외 대상 | 이유 |
|---|---|
| 전체 `Carla-0.9.16-source` vendor/build tree | 대용량이며 DAMOS 고유 소스가 아님 |
| 전체 `UnrealEngine_4.26` | 100GB 이상 외부 엔진 dependency |
| 전체 Scenic source | 외부 dependency, 필요한 map만 추적 |
| `Carla-0.9.16-source/_DAMOS/logs` | 실행 로그 |
| `Carla-0.9.16-source/_DAMOS/reports` | 검증 리포트 이미지/JSON |
| `Carla-0.9.16-source/_DAMOS/3d_model` | 대용량 모델 원본 |

## 다음 작업

| 우선순위 | 작업 |
|---|---|
| 1 | CARLA 메인 동기화 루프와 Scenic observer wrapper 연결 |
| 2 | ego/custom observer별 6프레임 이미지 버퍼링 구현 |
| 3 | observer metadata를 occupancy 입력 또는 M2X 공유 payload로 정리 |
| 4 | rule-based brake/straight 제어 루프 작성 |
| 5 | Track 2용 GT 추출 경로 정리 |

## 참고 문서

| 문서 | 내용 |
|---|---|
| `docs/project_direction.md` | Notion 기반 프로젝트 방향 |
| `docs/observer_mode.md` | custom walker observer mode 설계와 검증 |
| `docs/vvu_workspace.md` | vvu 폴더 구조 |
| `docs/repository_layout.md` | GitHub 저장소 구조와 archive 정책 |
| `Carla-0.9.16-source/_DAMOS/README.md` | DAMOS runtime wrapper 상세 |
