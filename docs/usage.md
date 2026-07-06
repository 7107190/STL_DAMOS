# DAMOS Usage Guide

이 문서는 DAMOS를 처음 받는 사람이 “무엇을 설치해야 하고, 어디에서 어떤 명령을 실행해야 하는지”를 순서대로 확인할 수 있게 정리한 실행 가이드입니다.

## 먼저 알아야 할 것

`STL_DAMOS` GitHub 저장소는 실행 가능한 전체 CARLA 패키지가 아니라 DAMOS source overlay입니다. 즉, GitHub repo만 clone해서는 바로 실행되지 않습니다.

| 구분 | 설명 |
|---|---|
| GitHub repo에 포함 | DAMOS scripts, Scenic scenarios, docs, map files, Unreal overlay source |
| GitHub repo에 미포함 | 전체 CARLA source/build tree, 전체 Unreal Engine, logs, reports, 3D model 원본 |
| 바로 실행 가능한 기준 환경 | `vvu:/home/vvu/vv/DAMOS` |
| 새 컴퓨터에서 필요한 것 | CARLA 0.9.16 source build, Unreal Engine 4.26, Scenic, Python 환경 |

처음 실행해보는 사람에게 가장 쉬운 방법은 **vvu 서버에 접속해서 이미 준비된 환경을 쓰는 것**입니다. 새 컴퓨터에서 처음부터 구성하는 것은 CARLA/Unreal 빌드가 필요해서 시간이 오래 걸리고 디스크도 많이 필요합니다.

## 전체 흐름

| 단계 | vvu 기존 환경 | 새 컴퓨터 |
|---|---|---|
| 1 | vvu 접속 | Linux/GPU/디스크 준비 |
| 2 | `/home/vvu/vv/DAMOS` 확인 | CARLA 0.9.16 source tree 준비 |
| 3 | `carla4` Python 확인 | Unreal Engine 4.26 준비 |
| 4 | 실행 명령 실행 | Python/Scenic/CARLA package 설치 |
| 5 | reports 확인 | `STL_DAMOS` overlay를 CARLA tree에 복사 |

## vvu에서 바로 실행하기

vvu에 접속합니다.

```bash
ssh vvu
```

작업 루트가 있는지 확인합니다.

```bash
test -d /home/vvu/vv/DAMOS && echo "DAMOS workspace exists"
test -d /home/vvu/vv/DAMOS/Carla-0.9.16-source && echo "CARLA source exists"
test -d /home/vvu/vv/DAMOS/UnrealEngine_4.26 && echo "Unreal Engine exists"
test -x /home/vvu/anaconda3/envs/carla4/bin/python && echo "carla4 python exists"
```

Python 환경을 확인합니다.

```bash
/home/vvu/anaconda3/envs/carla4/bin/python --version

/home/vvu/anaconda3/envs/carla4/bin/python - <<'PY'
import carla
import scenic
import pygame
import cv2
import numpy
from PIL import Image
print("DAMOS Python imports OK")
PY
```

`conda activate carla4`가 실패할 수 있습니다. vvu에는 `carla4`가 `/home/vvu/anaconda3/envs/carla4`에 있으므로, 가장 안전한 방식은 위처럼 절대 경로 Python을 쓰는 것입니다.

## 기본 실행

모든 DAMOS 실행은 CARLA source root에서 시작합니다.

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

실행 중 터미널에는 선택된 비정상 시나리오가 출력됩니다.

```text
DAMOS selected abnormal scenarios: S2(자전거 무단 횡단), S7(인도 공사로 인한 통행 불가), S6(도로 공사로 인한 차선 감소)
```

## GUI 없이 결과 저장하기

GUI가 부담되거나 SSH 환경이면 offscreen capture를 사용합니다.

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

결과는 아래에 저장됩니다.

```text
/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/reports/
```

actor camera capture 폴더 예시는 다음과 같습니다.

```text
_DAMOS/reports/actor_camera_captures_Town10HD_Opt_S5-1_port2620_YYYYMMDD-HHMMSS/
```

ego fault report 폴더 예시는 다음과 같습니다.

```text
_DAMOS/reports/ego_fault_report_Town10HD_Opt_S5-1_port2620_YYYYMMDD-HHMMSS/
```

## 특정 시나리오 실행

| 명령 옵션 | 의미 |
|---|---|
| `--selected-scenario S1` | S1만 실행 |
| `--selected-scenario S5` | S5만 실행 |
| `--n-scenarios 3` | S1~S9 중 랜덤 3개를 동시에 실행 |
| `--runs 5` | 전체 실행을 5회 반복 |

특정 시나리오만 실행하는 예시:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart \
  --port 2620 \
  --selected-scenario S4 \
  --runs 1 \
  --scenic-time 30 \
  --realtime-factor 1.0
```

## 시나리오 목록

| ID | 비정상 상황 | 생성 방식 | observer anchor |
|---|---|---|---:|
| `S1` | 보행자 무단횡단 | 검증된 3개 구역 안에서 랜덤 | 3 |
| `S2` | 자전거 무단횡단 | 랜덤 인도 위치 | 3 |
| `S3` | 비가시영역 무단횡단 | 고정 위치 | 3 |
| `S4` | 도로 위 장애물 | 랜덤 차로 위치 | 4 |
| `S5` | 인도 위 장애물 | 고정 위치 | 1 |
| `S6` | 도로 공사로 인한 차선 감소 | 고정 위치 | 1 |
| `S7` | 인도 공사로 인한 통행 불가 | 고정 위치 | 1 |
| `S8` | 인도 내 군중 | 고정 cluster 3개 | 3 |
| `S9` | 인도 쓰레기 더미 | 고정 cluster 5개 | 5 |

랜덤 실행 시 `--n-scenarios 3`은 S1~S9 중 최대 3개를 고르고, 한 번의 Scenic 실행 안에서 동시에 compose합니다. 순차 실행이 아닙니다.

## Observer와 카메라

현재 custom walker는 이동체가 아니라 observer node로 사용합니다. 각 abnormal anchor 주변 인도에 observer 1대를 배치하고, 그 observer가 anchor를 바라보게 합니다.

| 항목 | 기준 |
|---|---|
| observer 수 | anchor당 1대 |
| observer 종류 | `deliverybot`, `humanoid`, `random` |
| 기본 observer 종류 | `random` |
| observer 위치 | 가능한 경우 인도 |
| observer 방향 | abnormal anchor를 바라봄 |
| observer camera | 6방향 RGB |
| camera config | `/home/vvu/vv/DAMOS/sensor_config.txt` |

observer 종류를 고정하려면 다음 옵션을 사용합니다.

```bash
--observer-blueprint deliverybot
--observer-blueprint humanoid
--observer-blueprint random
```

## Camera fault

camera fault는 ego 차량의 `cam_front` 하나에만 적용합니다. observer camera와 ego의 나머지 camera는 정상으로 유지합니다.

| mode | 의미 |
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

사용 예시:

```bash
--save-actor-camera-captures \
--ego-front-camera-fault random
```

## Ego 기준 센서/모듈 이상상황

보고서용 이미지는 ego 기준으로 생성합니다. 다음 옵션을 추가하면 ego에 임시 LiDAR/front RGB 센서를 붙여 LiDAR noise, sensor delay, module stop 이미지를 저장합니다.

```bash
--save-ego-fault-report
```

저장되는 대표 파일:

| 파일 | 의미 |
|---|---|
| `01_ego_lidar_clean.png` | ego-local LiDAR 정상 point cloud |
| `02_ego_lidar_noise_dropout_outliers.png` | Gaussian noise, dropout, outlier가 섞인 LiDAR |
| `04_ego_sensor_delay_5_frames.png` | 현재 ego front RGB와 지연된 frame 비교 |
| `05_ego_module_stop_freeze.png` | 입력은 갱신되지만 module output이 정지된 상태 |
| `ego_fault_report_contact_sheet.png` | 위 이미지를 보고서용으로 모은 요약 이미지 |

## 실시간 화면 확인

vvu에서 GUI/VNC가 가능하면 spectator를 ego 뒤에 붙여서 볼 수 있습니다.

터미널 1에서 시나리오 실행:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart \
  --port 2620 \
  --n-scenarios 3 \
  --runs 1 \
  --scenic-time 60 \
  --realtime-factor 1.0
```

터미널 2에서 spectator follow 실행:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

/home/vvu/anaconda3/envs/carla4/bin/python \
  _DAMOS/scripts/ego_spectator_follow.py \
  --port 2620 \
  --distance 10 \
  --height 4 \
  --pitch -16
```

ego front camera를 별도 창으로 보고 싶으면 display가 있는 환경에서 실행합니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

/home/vvu/anaconda3/envs/carla4/bin/python \
  _DAMOS/scripts/live_ego_front_camera.py \
  --port 2620 \
  --fault random
```

순수 SSH에서는 pygame 창이 보이지 않을 수 있습니다. 이 경우 `--offscreen --save-actor-camera-captures` 방식으로 이미지를 저장해서 확인합니다.

## S1 무단횡단 검증

S1은 ego가 가까이 와야 보행자가 움직이는 trigger형 시나리오입니다. S1 동작을 확인하려면 autopilot verification 옵션을 사용합니다.

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

## 새 컴퓨터에서 처음 세팅하기

새 컴퓨터에서 실행하려면 GitHub repo 외에 외부 dependency를 먼저 준비해야 합니다.

| 필요 항목 | 설명 |
|---|---|
| Linux 개발 환경 | CARLA source build가 가능한 환경 |
| GPU/display 또는 offscreen 실행 환경 | GUI 실행 또는 RGB capture용 |
| 디스크 여유 공간 | vvu 기준 CARLA source 약 66GB, UE 약 103GB |
| Unreal Engine 4.26 | CARLA source build dependency |
| CARLA 0.9.16 source tree | DAMOS overlay를 얹을 기준 tree |
| Scenic checkout | CARLA Scenic scenario 실행 |
| Python 3.10 환경 | `carla`, `scenic`, `pygame`, `opencv-python`, `numpy`, `pillow` 필요 |

권장 폴더 구조:

```text
/home/<user>/vv/DAMOS/
├── Carla-0.9.16-source/
├── UnrealEngine_4.26/
├── README.md
├── sensor_config.txt
└── docs/
```

GitHub repo를 clone합니다.

```bash
mkdir -p ~/vv
cd ~/vv
git clone git@github.com:7107190/STL_DAMOS.git
```

CARLA/Unreal/Scenic 환경을 준비한 뒤, DAMOS overlay를 CARLA source tree에 복사합니다. 아래 예시에서는 CARLA source tree가 `~/vv/DAMOS/Carla-0.9.16-source`에 있다고 가정합니다.

```bash
export STL_DAMOS=~/vv/STL_DAMOS
export DAMOS_ROOT=~/vv/DAMOS
export CARLA_ROOT=$DAMOS_ROOT/Carla-0.9.16-source

mkdir -p "$CARLA_ROOT"

rsync -a "$STL_DAMOS/README.md" "$DAMOS_ROOT/README.md"
rsync -a "$STL_DAMOS/sensor_config.txt" "$DAMOS_ROOT/sensor_config.txt"
rsync -a "$STL_DAMOS/docs/" "$DAMOS_ROOT/docs/"

rsync -a "$STL_DAMOS/Carla-0.9.16-source/_DAMOS/" \
  "$CARLA_ROOT/_DAMOS/"

mkdir -p "$CARLA_ROOT/Scenic"
rsync -a "$STL_DAMOS/Carla-0.9.16-source/Scenic/Maps/" \
  "$CARLA_ROOT/Scenic/Maps/"

mkdir -p "$CARLA_ROOT/Unreal/CarlaUE4/Plugins/Carla/Source/Carla"
rsync -a "$STL_DAMOS/Carla-0.9.16-source/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/" \
  "$CARLA_ROOT/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/"
```

Python 환경을 준비합니다. vvu와 동일하게 Python 3.10 환경을 권장합니다.

```bash
conda create -n carla4 python=3.10 -y
conda activate carla4
pip install numpy pillow pygame opencv-python
```

CARLA Python package와 Scenic은 CARLA/Scenic 설치 방식에 따라 별도로 연결해야 합니다. 정상 연결 여부는 아래 명령으로 확인합니다.

```bash
python - <<'PY'
import carla
import scenic
import pygame
import cv2
import numpy
from PIL import Image
print("DAMOS Python imports OK")
PY
```

이 import가 실패하면 아직 DAMOS 문제가 아니라 CARLA/Scenic/Python 환경 연결 문제입니다.

## 새 컴퓨터 세팅 후 최소 검증

```bash
cd ~/vv/DAMOS/Carla-0.9.16-source

python -m py_compile \
  _DAMOS/scripts/*.py \
  _DAMOS/unreal/*.py \
  _DAMOS/carla_import/*.py

bash -n \
  _DAMOS/scripts/*.sh \
  _DAMOS/custom_walkers/*.sh \
  _DAMOS/unreal/*.sh
```

CARLA server까지 정상 준비되었다면 offscreen smoke test를 실행합니다.

```bash
cd ~/vv/DAMOS/Carla-0.9.16-source

_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart \
  --offscreen \
  --port 2620 \
  --selected-scenario S5 \
  --runs 1 \
  --scenic-time 20 \
  --realtime-factor 1.0 \
  --save-actor-camera-captures \
  --save-ego-fault-report \
  --no-trajectory-report
```

## 결과 저장 위치

| 결과 | 위치 |
|---|---|
| 실행 log | `_DAMOS/logs` |
| plot/report/capture | `_DAMOS/reports` |
| actor camera capture | `_DAMOS/reports/actor_camera_captures_*` |
| observer scene capture | `_DAMOS/reports/observer_scene_captures_*` |
| ego fault report | `_DAMOS/reports/ego_fault_report_*` |

`logs`와 `reports`는 GitHub에 올리지 않습니다. 공유가 필요하면 필요한 이미지나 JSON만 따로 압축해서 전달합니다.

## 자주 나는 문제

| 증상 | 원인 | 해결 |
|---|---|---|
| `conda activate carla4` 실패 | conda root가 다름 | `/home/vvu/anaconda3/envs/carla4/bin/python` 절대 경로 사용 |
| `ModuleNotFoundError: carla` | CARLA Python package 미연결 | 올바른 Python env에서 CARLA package 경로 확인 |
| `ModuleNotFoundError: scenic` | Scenic 미설치 또는 다른 env 사용 | Scenic checkout을 같은 Python env에 설치/연결 |
| pygame 창이 안 뜸 | 순수 SSH/display 없음 | `--offscreen --save-actor-camera-captures` 사용 |
| port 충돌 | 기존 CARLA server가 같은 port 사용 | `--restart` 사용 또는 `--port` 변경 |
| RGB capture가 비어 있음 | headless/nullrhi 실행 | capture에는 `--offscreen` 사용 |
| map 파일을 못 찾음 | Scenic Maps 미복사 | `Carla-0.9.16-source/Scenic/Maps` 확인 |
| observer가 안 생김 | Scenic actor spawn 실패 또는 timeout | `--scenic-time` 증가, `--verbose`로 anchor 로그 확인 |

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
