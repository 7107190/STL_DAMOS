# DAMOS Usage Guide

이 문서는 DAMOS 코드를 다른 사람이 받아서 확인하거나 vvu에서 실행할 때 필요한 최소 사용법을 정리합니다.

## 기준 환경

| 항목 | 값 |
|---|---|
| 런타임 서버 | `vvu` |
| 작업 루트 | `/home/vvu/vv/DAMOS` |
| CARLA 실행 루트 | `/home/vvu/vv/DAMOS/Carla-0.9.16-source` |
| DAMOS 코드 루트 | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS` |
| Python 환경 | `/home/vvu/anaconda3/envs/carla4` |
| 기본 map | `Town10HD_Opt` |

GitHub 저장소는 source overlay 공유용입니다. 전체 CARLA source tree, Unreal Engine, 실행 logs/reports, 3D model 원본은 포함하지 않습니다. 새 서버에서 실행하려면 CARLA/Unreal/Scenic 환경을 별도로 준비해야 하고, 현재 바로 실행 가능한 기준 환경은 vvu입니다.

## Python 실행 기준

`vvu`에서는 `conda activate carla4`가 현재 shell의 conda root에 따라 실패할 수 있습니다. 가장 확실한 방식은 절대 경로 Python을 쓰는 것입니다.

```bash
/home/vvu/anaconda3/envs/carla4/bin/python --version
```

conda activate를 써야 한다면 환경 이름 대신 전체 경로를 지정합니다.

```bash
conda activate /home/vvu/anaconda3/envs/carla4
```

## 기본 실행

모든 실행은 CARLA source 루트에서 시작합니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
```

랜덤 비정상 시나리오 3개를 한 번의 Scenic 실행 안에서 동시에 compose하고, 전체 실행을 5회 반복합니다.

```bash
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart \
  --port 2620 \
  --n-scenarios 3 \
  --runs 5 \
  --scenic-time 20 \
  --realtime-factor 1.0
```

## 자주 쓰는 실행 명령

| 목적 | 명령 |
|---|---|
| 옵션 확인 | `_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh --help` |
| 특정 시나리오 1회 실행 | `--selected-scenario S5 --runs 1` |
| 랜덤 3개 시나리오 실행 | `--n-scenarios 3` |
| ego 정지 상태로 확인 | `--static-ego` |
| GUI 없이 이미지 저장 | `--offscreen --save-actor-camera-captures` |
| ego front camera fault 적용 | `--ego-front-camera-fault random` |
| trajectory plot 생략 | `--no-trajectory-report` |
| observer 종류 고정 | `--observer-blueprint deliverybot` 또는 `--observer-blueprint humanoid` |
| observer 종류 랜덤 | `--observer-blueprint random` |

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

GUI 없이 camera capture 저장:

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
  --ego-front-camera-fault random \
  --no-trajectory-report
```

S1 무단횡단 autopilot trigger 검증:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart \
  --offscreen \
  --port 2620 \
  --selected-scenario S1 \
  --runs 1 \
  --scenic-time 40 \
  --realtime-factor 1.0 \
  --verify-s1-crossing-autopilot
```

## 시나리오 선택

| ID | 비정상 상황 | 생성 방식 |
|---|---|---|
| `S1` | 보행자 무단횡단 | 검증된 3개 구역 안에서 랜덤 |
| `S2` | 자전거 무단횡단 | 랜덤 인도 위치 |
| `S3` | 비가시영역 무단횡단 | 고정 위치 |
| `S4` | 도로 위 장애물 | 랜덤 차로 위치 |
| `S5` | 인도 위 장애물 | 고정 위치 |
| `S6` | 도로 공사로 인한 차선 감소 | 고정 위치 |
| `S7` | 인도 공사로 인한 통행 불가 | 고정 위치 |
| `S8` | 인도 내 군중 | 고정 cluster 3개 |
| `S9` | 인도 쓰레기 더미 | 고정 cluster 5개 |

랜덤 실행 시 `--n-scenarios 3`은 S1~S9 중 최대 3개를 고르고, 한 번의 Scenic 실행 안에서 동시에 compose합니다. 순차 실행이 아닙니다.

## Camera 확인

actor camera capture는 ego 6방향 camera와 observer별 6방향 camera를 저장합니다. ego front camera fault는 ego `cam_front` 하나에만 적용됩니다.

| fault mode | 의미 |
|---|---|
| `none` | fault 없음 |
| `random` | visible fault 중 랜덤 선택 |
| `blackout` | 검정 화면 |
| `blur` | blur |
| `occlusion` | 검정 박스 가림 |
| `color_failure` | RGB 채널 하나 제거 |
| `misalignment` | camera transform 오정렬 |
| `shaking` | 이미지 흔들림 |
| `freeze_cycle` | temporal freeze용 |

실시간 ego front camera 창은 VNC, 로컬 GUI, X forwarding처럼 display가 있는 환경에서만 의미가 있습니다. 순수 SSH에서는 저장형 capture를 사용합니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

/home/vvu/anaconda3/envs/carla4/bin/python \
  _DAMOS/scripts/live_ego_front_camera.py \
  --port 2620 \
  --fault random
```

ego를 따라가는 spectator camera:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

/home/vvu/anaconda3/envs/carla4/bin/python \
  _DAMOS/scripts/ego_spectator_follow.py \
  --port 2620 \
  --distance 10 \
  --height 4 \
  --pitch -16
```

## 결과 저장 위치

| 결과 | 위치 |
|---|---|
| 실행 log | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/logs` |
| plot/report/capture | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/reports` |
| actor camera capture | `_DAMOS/reports/actor_camera_captures_*` |
| observer scene capture | `_DAMOS/reports/observer_scene_captures_*` |

`logs`와 `reports`는 GitHub에 올리지 않습니다. 공유가 필요하면 필요한 이미지나 JSON만 따로 압축해서 전달합니다.

## 수정 후 검증

코드를 수정했다면 최소한 문법 검증을 실행합니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

/home/vvu/anaconda3/envs/carla4/bin/python -m py_compile \
  _DAMOS/scripts/*.py \
  _DAMOS/unreal/*.py \
  _DAMOS/carla_import/*.py

bash -n \
  _DAMOS/scripts/*.sh \
  _DAMOS/custom_walkers/*.sh \
  _DAMOS/unreal/*.sh
```

runtime을 건드렸다면 최소 1회 실행합니다.

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

## 공유 기준

| 공유 대상 | 방식 |
|---|---|
| 코드/문서 | GitHub `STL_DAMOS` |
| 실행 환경 | vvu 경로와 이 문서 |
| 검증 이미지 | 필요한 파일만 별도 전달 |
| 전체 `/home/vvu/vv/DAMOS` | 공유하지 않음 |

전체 DAMOS 폴더에는 CARLA/Unreal vendor tree, logs, reports, 3D model 원본이 같이 있으므로 통째로 공유하지 않습니다.
