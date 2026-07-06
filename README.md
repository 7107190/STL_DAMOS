# STL_DAMOS

DAMOS는 `Decentralized Autonomous Mobility Simulation`의 약자입니다. CARLA/Scenic에서 ego 차량과 custom observer가 서로 정보를 공유해, ego의 비가시영역에서 발생한 비정상 상황까지 대응하는 협력 자율주행 시뮬레이션 프로젝트입니다.

이 저장소는 `/home/vvu/vv/DAMOS` 전체를 그대로 올린 백업이 아닙니다. 대용량 CARLA/Unreal/Scenic 원본은 제외하고, DAMOS 구현에 필요한 overlay 소스와 문서만 GitHub 구조에 맞춰 정리합니다.

## 프로젝트 기준

| 항목 | 기준 |
|---|---|
| 방향 기준 | Notion `SDV / DAMOS` |
| 런타임 서버 | `vvu` |
| 실제 작업 루트 | `/home/vvu/vv/DAMOS` |
| CARLA 작업 루트 | `/home/vvu/vv/DAMOS/Carla-0.9.16-source` |
| 활성 DAMOS 코드 | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS` |
| GitHub mirror 기준 | `STL_DAMOS/Carla-0.9.16-source/_DAMOS` |

## 핵심 아이디어

| 구성 | 역할 |
|---|---|
| Ego vehicle | Scenic으로 생성되는 빨간색 `vehicle.ford.mustang` ego 차량 |
| Scenic abnormal scenarios | S1~S9 비정상 상황 생성 |
| Custom observer | 비정상 상황 anchor 주변 인도에 배치되는 휴머노이드 또는 배달로봇 |
| Observer cameras | `sensor_config.txt` 기준 6방향 RGB 카메라 |
| Ego front camera fault | ego `cam_front` 1개에만 카메라 이상상황을 선택 적용 |
| M2X 공유 목표 | ego가 직접 보지 못하는 영역의 정보를 observer가 보완 |

## Track 구성

| Track | 목표 | 현재 범위 |
|---|---|---|
| Track 1 | M2X 실시간 통합 시뮬레이션, 18초 지표 방어 | CARLA + Scenic 비정상 상황 + observer + 카메라 캡처 + rule-based 제어 준비 |
| Track 2 | CARLA GT 기반 5초 미래 궤적 예측 | Track 1 루프와 분리된 오프라인 정량 평가 |

Track 1은 최종 제어 루프를 가볍게 유지해야 합니다. Track 2의 예측/학습/GT 평가는 별도 오프라인 경로로 분리합니다.

## 현재 진행상황

| 구분 | 상태 |
|---|---|
| vvu 폴더 구조 정리 | 완료 |
| CARLA/Scenic 실행 환경 | 완료 |
| Scenic S1~S9 비정상 상황 정리 | 완료 |
| custom walker asset/runtime | 완료 |
| observer mode | 완료 |
| anchor 기반 observer 배치 | 완료, anchor당 observer 1대 |
| observer 종류 | `deliverybot`, `humanoid`, `random` 지원 |
| observer camera 부착 | 완료, observer당 6방향 RGB |
| ego camera 캡처 | 완료, ego 6방향 RGB |
| ego front camera fault | 완료, ego `cam_front`에만 적용 |
| actor camera capture 저장 | 완료 |
| 다음 핵심 작업 | 6프레임 버퍼링, M2X payload, rule-based 제어 루프 |

## Scenic 시나리오 상태

한 번의 Scenic 실행에서 `--n-scenarios 3`을 주면 S1~S9 중 최대 3개가 랜덤 선택되어 동시에 compose됩니다. 날씨는 `--weather`를 지정하지 않으면 Scenic의 4개 weather preset 중 하나가 랜덤 선택됩니다.

| 시나리오 | 비정상 상황 | 생성 방식 | 생성 객체 | observer anchor |
|---|---|---|---:|---:|
| `S1` | 보행자 무단횡단 | 검증된 3개 구역 안에서 랜덤 | 보행자 3명 | 3 |
| `S2` | 자전거 무단횡단 | 랜덤 인도 위치 | 자전거 3대 | 3 |
| `S3` | 비가시영역 무단횡단 | 고정 위치 | 보행자 3명 | 3 |
| `S4` | 도로 위 장애물 | 랜덤 차로 위치 | 대형 container 4개 | 4 |
| `S5` | 인도 위 장애물 | 고정 위치 | 불법 주차 차량 6대 | 1 |
| `S6` | 도로 공사로 인한 차선 감소 | 고정 위치 | 공사 barrier/warning 36개 | 1 |
| `S7` | 인도 공사로 인한 통행 불가 | 고정 위치 | 공사 barrier/warning 40개 | 1 |
| `S8` | 인도 내 군중 | 고정 cluster 3개 | 보행자 21명 | 3 |
| `S9` | 인도 쓰레기 더미 | 고정 cluster 5개 | trash/bin 40개 | 5 |

S5~S9처럼 여러 actor가 하나의 의미 단위를 만드는 경우에는 actor 하나하나가 아니라 cluster/region을 observer anchor로 봅니다. S1~S4는 개별 비정상 객체를 anchor로 봅니다.

## 실행 명령

기본 랜덤 3개 시나리오 실행:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart \
  --port 2620 \
  --n-scenarios 3 \
  --runs 5 \
  --scenic-time 20 \
  --realtime-factor 1.0
```

특정 시나리오만 실행:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart \
  --port 2620 \
  --selected-scenario S5 \
  --runs 1 \
  --scenic-time 30 \
  --realtime-factor 1.0
```

GUI 없이 actor camera capture 저장:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart \
  --offscreen \
  --port 2620 \
  --selected-scenario S5 \
  --runs 1 \
  --scenic-time 30 \
  --realtime-factor 1.0 \
  --save-actor-camera-captures \
  --save-ego-fault-report \
  --ego-front-camera-fault random \
  --no-trajectory-report
```

저장 위치:

```text
/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/reports/
```

## 주요 옵션

| 옵션 | 의미 |
|---|---|
| `--selected-scenario S5` | 특정 시나리오 하나만 강제 실행 |
| `--n-scenarios 3` | 랜덤 실행 시 S1~S9 중 최대 3개 선택 |
| `--runs 5` | 전체 Scenic/custom observer 실행을 5회 반복 |
| `--scenic-time 20` | 각 run의 Scenic 실행 시간 제한 |
| `--realtime-factor 1.0` | 시뮬레이션 tick을 실제 시간에 가깝게 조절 |
| `--observer-blueprint random` | anchor마다 휴머노이드/배달로봇 중 랜덤 배치 |
| `--max-anchor-pairs N` | 이름은 legacy지만 실제 의미는 최대 anchor N개 커버 |
| `--save-actor-camera-captures` | ego와 observer의 카메라 이미지를 저장 |
| `--save-ego-fault-report` | ego 기준 LiDAR noise, sensor delay, module stop 보고서 이미지 저장 |
| `--ego-front-camera-fault random` | ego `cam_front`에만 랜덤 카메라 fault 적용 |
| `--offscreen` | GUI 없이 RGB capture 가능한 CARLA 서버 실행 |
| `--static-ego` | 수동 확인용으로 ego 정지 |

## 구현된 HW/SW 고장상황

모든 고장상황은 ego 기준으로 적용하거나 시각화합니다. Camera fault는 ego `cam_front` 하나에만 적용되며, observer 카메라와 ego의 나머지 카메라는 정상 이미지를 유지합니다.

| 분류 | 고장상황 | 적용 대상 | 구현 방식 |
|---|---|---|---|
| HW-like | Camera blackout | ego front camera | 카메라 신호를 검정 화면으로 대체 |
| HW-like | Camera degradation | ego front camera | blur, occlusion, color failure, shaking 등 영상 품질 저하 |
| HW-like | Camera misalignment | ego front camera | camera transform 오정렬 |
| HW-like | LiDAR noise | ego LiDAR | point cloud 좌표에 Gaussian noise 적용 |
| HW-like | LiDAR dropout | ego LiDAR | point cloud 일부 제거 |
| HW-like | LiDAR outlier | ego LiDAR | 실제 위치와 무관한 가짜 point 추가 |
| HW/SW 경계 | Sensor delay | ego sensor stream | 현재 frame 대신 과거 frame 사용 |
| SW-like | Module stop/freeze | ego module output | 입력은 갱신되지만 module output은 정지 |
| SW-like | Stale perception output | ego module output | 오래된 output을 계속 사용하는 상태를 delay/freeze 리포트로 시각화 |

Camera fault 선택값:

| mode | 포함 항목 |
|---|---|
| `blackout` | Camera blackout |
| `blur`, `occlusion`, `color_failure`, `shaking` | Camera degradation |
| `misalignment` | Camera misalignment |
| `random` | visible camera fault 중 랜덤 선택 |

LiDAR noise/dropout/outlier, sensor delay, module stop/freeze, stale output 이미지는 `--save-ego-fault-report`로 저장합니다. 예전 `/home/vvu/vv/DAMOS/Camera` 폴더는 legacy demo 코드입니다. 현재 실행 경로는 `_DAMOS/scripts/scenic_custom_walker_injector.py`와 `_DAMOS/scripts/live_ego_front_camera.py`입니다.

## 저장소 구조

```text
STL_DAMOS/
├── README.md
├── Carla-0.9.16-source/
│   ├── _DAMOS/
│   ├── Scenic/
│   │   └── Maps/
│   └── Unreal/
│       └── CarlaUE4/Plugins/Carla/Source/Carla/Damos/
├── UnrealEngine_4.26/
│   └── README.md
├── docs/
└── archive/
```

| GitHub 경로 | vvu 실제 경로 | 설명 |
|---|---|---|
| `Carla-0.9.16-source/_DAMOS` | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS` | 실행 스크립트, Scenic 시나리오, custom walker runtime |
| `Carla-0.9.16-source/Scenic/Maps` | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/Scenic/Maps` | Scenic용 OpenDRIVE map |
| `Carla-0.9.16-source/Unreal/.../Damos` | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/Unreal/.../Damos` | Unreal custom walker source overlay |
| `UnrealEngine_4.26/README.md` | `/home/vvu/vv/DAMOS/UnrealEngine_4.26` | 실제 UE 전체는 Git에서 제외하고 위치만 문서화 |
| `docs/` | `/home/vvu/vv/DAMOS/docs` | 공유용 실행/사용법 문서 |

## 운영 규칙

`AGENTS.md`에 따로 있던 내용은 README로 통합했습니다.

| 규칙 | 이유 |
|---|---|
| Notion `SDV / DAMOS`를 방향 기준으로 둔다 | 저장소는 구현 상태를 반영하고, 기획 원본은 Notion이 기준 |
| Track 1 루프는 가볍게 유지한다 | 18초 지표 방어가 목표이므로 최종 제어 루프에 무거운 연산을 넣지 않음 |
| Track 2는 오프라인으로 분리한다 | 5초 미래 궤적 예측은 CARLA GT 기반 평가 경로로 분리 |
| custom walker는 기본적으로 observer로 사용한다 | 현재 목표는 walker navigation이 아니라 비가시영역 정보 공유 |
| 전체 CARLA/UE/Scenic vendor tree는 Git에 올리지 않는다 | DAMOS 고유 overlay만 추적하기 위함 |

## Git에 올리지 않는 것

| 제외 대상 | 이유 |
|---|---|
| 전체 `Carla-0.9.16-source` vendor/build tree | 대용량이며 DAMOS 고유 소스가 아님 |
| 전체 `UnrealEngine_4.26` | 외부 엔진 dependency |
| 전체 Scenic source | 외부 dependency, 필요한 map과 overlay만 추적 |
| `Carla-0.9.16-source/_DAMOS/logs` | 실행 로그 |
| `Carla-0.9.16-source/_DAMOS/reports` | 검증 이미지/JSON |
| `Carla-0.9.16-source/_DAMOS/3d_model` | 대용량 모델 원본 |
| `__pycache__`, `*.pyc` | Python 생성물 |

## 수정 후 검증

수정한 파일 종류에 맞춰 필요한 것만 실행합니다.

```bash
cd /home/vvu/vv/DAMOS

python3 -m py_compile Carla-0.9.16-source/_DAMOS/scripts/*.py
bash -n Carla-0.9.16-source/_DAMOS/scripts/*.sh
git diff --check
```

observer runtime을 건드렸다면 vvu에서 최소 1회 실행 검증합니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart \
  --offscreen \
  --port 2620 \
  --n-scenarios 3 \
  --runs 1 \
  --scenic-time 20 \
  --realtime-factor 1.0 \
  --save-actor-camera-captures \
  --no-trajectory-report
```

## 다음 작업

| 우선순위 | 작업 |
|---|---|
| 1 | ego/observer 카메라 stream을 6프레임 버퍼로 연결 |
| 2 | observer metadata와 카메라 결과를 M2X 공유 payload로 정리 |
| 3 | ego local view와 observer shared view를 융합하는 입력 포맷 정의 |
| 4 | 충돌 위험 시 brake, 아니면 straight인 rule-based 제어 루프 작성 |
| 5 | Track 2용 CARLA GT 추출 경로 정리 |

## 참고 문서

| 문서 | 내용 |
|---|---|
| `docs/usage.md` | 환경 세팅, 실행 명령, 코드 사용법 |
| `Carla-0.9.16-source/_DAMOS/README.md` | DAMOS runtime wrapper 상세 |
