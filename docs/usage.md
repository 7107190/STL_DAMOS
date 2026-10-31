# DAMOS 사용법

문서 갱신: 2026-10-31. 이 문서는 환경 설정·코드 경로·실행·결과 확인 전용. 연구·구현 내용은 [2025년](2025.md)·[2026년](2026.md), 평가 기준·증빙은 [성능지표](metrics.md), 남은 작업은 [준비 현황](status.md) 참조.

## vvu에서 시작

```bash
ssh vvu
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

/home/vvu/anaconda3/envs/carla4/bin/python --version
/home/vvu/vv/DAMOS/.venvs/bev/bin/python --version
```

기본 실행은 `carla4`, BEV 실행은 `.venvs/bev` 사용. wrapper가 자동 선택하므로 conda 활성화 불필요. `conda activate carla4`가 실패해도 위 절대 경로의 환경과는 별개.

### 일반 시뮬레이션

랜덤 이상상황 3종을 한 번의 Scenic 실행에서 **동시에** 구성하고, 전체 실행 5회 반복.

```bash
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --port 2620 \
  --n-scenarios 3 --runs 5 \
  --scenic-time 20 --realtime-factor 1.0
```

| 옵션 | 의미 |
|---|---|
| `--selected-scenario S5` | S5만 선택. 단일 상황 확인 시 사용 |
| `--n-scenarios 3` | 랜덤 3종 동시 구성, 순차 실행 아님 |
| `--runs 5` | 전체 시나리오 5회 반복 |
| `--scenic-time 20` | 시뮬레이션 시간 제한, 모델 처리시간 지표 아님 |
| `--realtime-factor 1.0` | 시뮬레이션 진행 속도 조절 |
| `--quality-level Epic` | 실제 안개·후처리 효과 활성화. 일반 실행 기본값 Low |
| `--weather dense_fog` | 기본 네 날씨 중 하나 지정. 생략 시 랜덤 |
| `--observer-blueprint random` | 앵커당 1대. `deliverybot`·`humanoid`로 종류 고정 가능 |
| `--verbose` | 상세 로그 출력 |

기본 날씨: `low_sun`, `heavy_rain`, `dense_fog`, `wet_night`. `ClearNoon` 같은 CARLA 프리셋도 지원하지만 기본 네 조건과 동일한 설정은 아님. 선택된 이상상황 번호·이름은 터미널에 출력.

### 카메라 보기

GUI/VNC가 있는 vvu에서 일반 시뮬레이션 실행 후 **별도 터미널**에 아래 중 하나 실행. 포트는 시뮬레이션과 동일하게 지정.

Ego 뒤에서 따라가는 spectator:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
/home/vvu/anaconda3/envs/carla4/bin/python \
  _DAMOS/scripts/ego_spectator_follow.py \
  --port 2620 --distance 10 --height 4 --pitch -16
```

Ego 정면 RGB 창:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
/home/vvu/anaconda3/envs/carla4/bin/python \
  _DAMOS/scripts/live_ego_front_camera.py --port 2620 --fault none
```

순수 SSH에서 GUI 창이 자동으로 보이는 것은 아님. display/VNC 없이 확인하려면 아래 offscreen 저장 사용. 뷰어의 `--fault random`은 영상 효과이며 실제 BEV 고장 대응 시험과 별개.

### GUI 없이 카메라 저장

```bash
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2620 --selected-scenario S5 \
  --runs 1 --scenic-time 30 --realtime-factor 1.0 \
  --quality-level Epic --save-actor-camera-captures \
  --no-trajectory-report
```

`_DAMOS/reports/actor_camera_captures_*/`에 Ego·감시자 카메라 저장. `--save-observer-scene-captures`는 외부 장면, `--save-ego-fault-report`는 고장 효과 보고서 추가. 후자는 고장 8종+정상 화면의 이미지 보고서이며, 생성 성공을 고장 대응·처리속도 합격으로 판정하지 않음.

## BEV 추론·공유

준비된 기준 모델로 S5의 실제 CUDA 추론·Groth16·TCP·Ego 병합 실행.

```bash
env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt \
  _DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2620 --selected-scenario S5 \
  --runs 1 --scenic-time 30 --realtime-factor 1.0 \
  --quality-level Epic \
  --live-bev-fusion --bev-rounds 3 --bev-radius-m 60 \
  --evaluate-bev --no-trajectory-report
```

- `--bev-rounds 3`: 교환 3회. `--runs`의 시나리오 재시작 횟수와 구분.
- `--bev-radius-m 60`: 가까운 감시자와만 양방향 공유, `0`은 전체. 감시자끼리 교환 제외.
- `--evaluate-bev`: 같은 actor·프레임의 GT로 평가. GT는 모델·패킷·병합 입력에 미사용.
- `--bev-scene-cameras`: 평가 옵션과 함께 같은 프레임의 실제 탑다운 RGB 추가.
- 센서 취득·모델/키 준비·저장·시각화는 처리시간과 구분. 일반 live BEV 모드는 처리 중에도 Scenic tick 유지.
- 모든 tick을 추론하지 않으며 완료 후 최신 공통 입력 선택. 이웃 없음·미수신·시간초과를 공유 성공으로 세지 않음.
- `--bev-timeout-seconds`는 실패 처리 상한. `pipeline_budget_pass`는 시간 조건, 장애물 탐지 성공과 별개이며 공식 제출자료 판정을 대신하지 않음.
- `DAMOS_BEV_CHECKPOINT`를 빼면 원본 모델 선택. 가중치가 없을 때 다른 모델·모의 출력으로 우회하지 않음.

센서는 RGB 6개(800×450, FOV 90도)+일반 LiDAR 1개(64채널, 80m). 모델은 공통 4프레임 RGB와 현재 LiDAR를 사용. 센서 위치는 `sensor_config.txt` 기준, actor 재생성 없이 부착.

BEV는 80×80m·0.4m 격자. 주황색은 장애물, 회색은 지면, 어두운 영역은 미관측. 표시용 보간은 공유·검출 근거에 미포함. 관측되지 않은 영역을 빈 공간이나 통행 가능으로 해석하지 않음.

### 상대 신뢰도 병합 옵션

vvu의 기존 BEV 실행에 `relative-v3` 적용. `ego_observed` 모드의 아래 체크포인트 사용.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt \
  DAMOS_BEV_EVIDENCE_FUSION=relative-v3 \
  _DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2620 --selected-scenario S1 \
  --runs 1 --scenic-time 30 --realtime-factor 1.0 \
  --quality-level Epic \
  --live-bev-fusion --bev-rounds 3 --bev-radius-m 60 \
  --evaluate-bev --no-trajectory-report
```

- Ego의 실제 추론·Groth16 수신 검증 후 병합 단계에 적용. 보정 시간도 Ego 처리시간에 포함.
- 희소 LiDAR 구조 복원·고신뢰 감시자 관측 유지.
- 점수 0.50 이상 0.60 미만 감시자 관측은 Ego 지면 점수보다 높은 경우에만 추가 반영.
- 기본값 `off`. 환경변수 생략 또는 `DAMOS_BEV_EVIDENCE_FUSION=off`이면 선택한 체크포인트의 기존 병합 사용.
- `weighted` 모드의 원본 체크포인트와 함께 활성화하면 오류로 종료. 위 체크포인트를 함께 지정.
- 로그의 `evidence_fusion.policy`, `relative_promoted_cells`, `elapsed_ms`로 적용 확인.
- S1·S3 지정 4조건·120샘플 검증 결과는 [2026년 수행내용](2026.md#5-실패-분석과-변경재검증) 참조. 고장 대응 서비스 경로에는 적용하지 않음.

### 센서 입력만 저장

```bash
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2660 --selected-scenario S4 \
  --runs 1 --scenic-time 8 --realtime-factor 1.0 \
  --save-bev-inputs --no-trajectory-report
```

`--save-bev-inputs`는 동일 4프레임의 입력 저장이며 추론·ZKP 실행은 아님. `--attach-bev-sensors`는 부착·수신 버퍼만 활성화. RGB 필요 모드에는 `--headless` 대신 `--offscreen` 사용. BEV 모드와 기존 이미지 공유·캡처·영상 효과 모드는 동시 사용 제한.

### 이전 정면 이미지 공유

```bash
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2620 --selected-scenario S5 \
  --runs 1 --scenic-time 30 --realtime-factor 1.0 \
  --live-ego-sharing --sharing-rounds 5 --sharing-radius-m 60 \
  --save-sharing-video --no-trajectory-report
```

정면 1장과 ZKP만 교환하며 BEV 추론 없음. `sharing_complete_detection_pending`·`transport_budget_pass`는 인지 완료·전체 18초 지표 합격이 아님. 수신 화면은 마지막 검증 완료 프레임이므로 현재 Ego 미리보기보다 늦을 수 있음.

## S5 고장 대응 시험

**설계된 고장 주입에 대한 배제·대체·정지 요구·복귀 시험. 장애물 검출률 시험이나 모든 고장의 자동 진단 시험은 아님.** F09는 과거 결과의 프레임·시각·추론 완료 기록을 독립 검사. [판정 기준과 한계](2026.md#고장을-판단하는-방법) 확인.

아래 명령은 S5·기본 네 날씨·고장 15종·Epic 모드 60조건. 기존 포트를 사용 중이면 빈 포트 선택, 결과 폴더는 매 실행 새로 생성.

```bash
OUT=/home/vvu/vv/_Data/Carla/Columbus/DAMOS_results/resilience_$(date -u +%Y%m%dT%H%M%SZ)

/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/run_carla_resilience.py \
  --port 2690 --quality-level Epic \
  --weathers low_sun heavy_rain dense_fog wet_night \
  --output "$OUT"
```

`--cases output_freeze`를 위 실행 명령에 추가하면 F09만 네 날씨에서 검증. `front_blackout` 등 다른 조건 선택도 가능. 실패·미완료 시 종료 코드와 `summary.json` 확인, 기준을 낮추거나 같은 폴더를 덮어쓰지 않음. 정상 복귀는 인지 서비스 복귀이며 운행 자동 재개 아님.

같은 SSH 세션에서 저장된 결과 감사. 실제 증명·병합·상태 재검증 및 손상 입력 모델 3회 재추론:

```bash
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/audit_carla_resilience.py "$OUT" \
  --real-crypto --replay-inference --inference-repeats 3 \
  --output "$OUT/audit_replay_process1.json"
```

별도 프로세스의 모델 재현 확인을 반복하려면:

```bash
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/audit_carla_resilience.py "$OUT" \
  --replay-inference --inference-repeats 3 \
  --output "$OUT/audit_replay_process2.json"
```

증명 수는 첫 감사의 실제 검증 개수로 집계. 재추론 횟수를 새 CARLA 조건 수에 합산하지 않음. 고장 효과 보고서·뷰어의 `--fault` 옵션만으로 위 시험이 실행되지는 않음.

## 동일 시점 2×3 영상

먼저 센서를 취득하고 실제 모델·ZKP·병합을 오프라인 재생하는 시각화 모드. 윗줄은 Ego/감시자/병합 BEV, 아랫줄은 같은 프레임 Ego/감시자/탑다운 RGB.

```bash
env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt \
  /home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/run_bev_passby_sweep.py \
  --scenarios S1 --seconds 30 --port 2670 \
  --report-root /여유공간있는/새_영상_폴더
```

- 대상은 시나리오별 지정 앵커 1곳. `--scenarios S1 S2 ... S9`로 종류 선택.
- 같은 actor ID·프레임 유지, 접촉·차도 이탈·미통과·센서 내용 정지는 실패 기록. 다른 객체·과거 프레임으로 대체하지 않음.
- 영상용 저속 제어·S3 Traffic Manager 제동은 촬영 보조이며 BEV 기반 회피·자연 주행 안전성 시험 아님.
- `ffmpeg` 필요. 보고서 경로 최소 4GiB, 9종 연속은 25GiB 이상 권장. `/dev/shm`은 RAM·휘발성 저장소이며 별도 메모리 여유 검사 적용.
- `returncode`·`video_complete`·실패 기록을 함께 확인. 영상 완료만으로 전체 실행·18초 지표 통과를 주장하지 않음.

사후 검사:

```bash
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/audit_bev_passby_video.py \
  --report /영상/bev_video_시각 \
  --checkpoint /기준모델.pt --output /새_검사.json
```

## 결과 파일

일반 결과 루트는 `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/reports/`. 고장 시험은 지정 `--output`, 영상은 지정 `--report-root` 아래 저장.

| 경로·파일 | 내용 |
|---|---|
| `_DAMOS/logs/` | 서버·실행 로그 |
| `actor_camera_captures_*/`, `observer_scene_captures_*/` | 실제 카메라·외부 장면 |
| `bev_sensor_inputs_*/manifest.json` | 센서 ID·행렬·공통 프레임·입력 설정 |
| `live_bev_*/summary.json`, `round_*/peers.json` | 참여 노드·실제 처리시간·성공/실패 |
| `round_*/*_packet.json`, `ego_received_*.npz` | 증명·공개 입력·실제 수신 BEV |
| `round_*/ego_fused.npz`, `ego_obstacle_presence.png` | 병합 데이터·장애물 존재 지도 |
| `round_*/evaluation.json` | 평가 옵션 사용 시 정답 대조·존재·가림 진단 |
| 고장 시험 `protocol.json`, `frozen_source/` | 사전 조건·소스/설정·해시 |
| 고장 시험 `summary.json`, `audit_*.json` | 조건별 대응 판정·독립 감사 |
| F09 `*_model_output.json`, `*_rejected_output.npz` | 추론 당시 출처·완료시각, 거부된 원본 BEV. 실제 송신·병합은 현재 좌표의 미관측 BEV 사용 |
| `bev_video_*/passby_2x3.mp4`, `capture.json` | 동기 영상·실제 제어기·프레임·센서 내용 검사 |

최신 60조건의 원본·그림·집계 파일 위치는 [증빙](2026.md#고장-대응-증빙) 참조. 저장물·영상·가중치는 Git에 올리지 않음.

## 새 컴퓨터 설정

**Git clone만으로 실행 불가.** 빌드된 CARLA/UE·Scenic checkout·custom assets·가중치를 별도로 준비해야 함. 전체 환경 자동 설치 프로그램은 미제공.

### 준비 항목

| 항목 | vvu에서 확인한 기준 |
|---|---|
| 플랫폼 | Linux x86_64, NVIDIA GPU. CARLA/UE 디스크 사용량 약 66GB/103GB, 빌드 여유 공간 별도 |
| 엔진·시뮬레이터 | Unreal Engine 4.26, CARLA 0.9.16 source build + DAMOS walker factory/assets |
| 기본 Python | Python 3.10, CARLA SDK 0.9.16, Scenic checkout 3.1.0a1 |
| BEV | torch 2.7.0+cu128, torchvision 0.22.0+cu128, Mamba 2.2.6.post3 |
| 이미지 패키지 | NumPy 1.26.4, OpenCV 4.8.1.78, Pillow 11.2.1, pygame 2.6.1 |
| ZKP | Node.js 18 이상·npm, vvu 검증 버전 22.22.0. 회로·WASM·키는 vendor에 포함 |
| 별도 전달 | CARLA/Scenic 전체 환경, custom walker assets, 원본/Scenic 기준 가중치 |

다른 CUDA·Python·ABI 조합에서 아래 Mamba wheel의 호환성을 보장하지 않음. 환경·자산이 없으면 담당자에게 전달받거나 해당 빌드를 먼저 완료해야 함.

### 저장소와 overlay

```bash
mkdir -p ~/vv
cd ~/vv
git clone git@github.com:7107190/STL_DAMOS.git

export STL_DAMOS=~/vv/STL_DAMOS
export DAMOS_ROOT=~/vv/DAMOS
export CARLA_ROOT=$DAMOS_ROOT/Carla-0.9.16-source
```

`$CARLA_ROOT`에 완전한 CARLA source build, `$DAMOS_ROOT/UnrealEngine_4.26`에 UE, `$CARLA_ROOT/Scenic`에 전체 Scenic checkout을 준비한 뒤 아래 overlay 복사.

```bash
rsync -a "$STL_DAMOS/README.md" "$DAMOS_ROOT/README.md"
rsync -a "$STL_DAMOS/sensor_config.txt" "$DAMOS_ROOT/sensor_config.txt"
rsync -a "$STL_DAMOS/docs/" "$DAMOS_ROOT/docs/"
rsync -a "$STL_DAMOS/Carla-0.9.16-source/_DAMOS/" "$CARLA_ROOT/_DAMOS/"
rsync -a "$STL_DAMOS/Carla-0.9.16-source/Scenic/Maps/" "$CARLA_ROOT/Scenic/Maps/"
rsync -a "$STL_DAMOS/Carla-0.9.16-source/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/" \
  "$CARLA_ROOT/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/"
```

C++ 복사만으로 빌드·factory 등록·asset import가 완료되지는 않음. [워커 준비](../Carla-0.9.16-source/_DAMOS/custom_walkers/README.md)·[Unreal overlay](../Carla-0.9.16-source/Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/README.md) 참조. 배달로봇 카메라 갱신 수정에는 `DamosVisibilityLockComponent.cpp` 반영·CARLA 모듈 재컴파일·서버 재시작 필요.

### Python·BEV 환경

기본 환경 생성 후 해당 CARLA build의 Python SDK와 전달받은 Scenic checkout을 같은 환경에 설치/연결.

```bash
conda create -n carla4 python=3.10 -y
conda activate carla4
python -m pip install numpy==1.26.4 pillow==11.2.1 pygame==2.6.1 opencv-python==4.8.1.78
python -m pip install -e "$CARLA_ROOT/Scenic"

python -c 'import carla, scenic, pygame, cv2, numpy; from PIL import Image; print("imports OK")'
```

CARLA SDK가 설치되지 않았다면 import 실패가 정상이며 해당 build의 wheel/설치 경로부터 확인. BEV에는 위 표의 torch·torchvision·CUDA 조합이 추가로 필요. 아래 requirements는 보조 패키지뿐이며 새 환경에 이것만 설치해서는 실행되지 않음.

```bash
cd "$DAMOS_ROOT"
python -m venv --system-site-packages .venvs/bev
.venvs/bev/bin/python -m pip install \
  -r "$CARLA_ROOT/_DAMOS/vendor/yerin_bev/requirements.txt"
.venvs/bev/bin/python -m pip install --no-deps \
  'https://github.com/state-spaces/mamba/releases/download/v2.2.6.post3/mamba_ssm-2.2.6.post3%2Bcu12torch2.7cxx11abiTRUE-cp310-cp310-linux_x86_64.whl'
.venvs/bev/bin/python -c 'import carla, scenic, torch, torchvision, mamba_ssm; assert torch.cuda.is_available(); print("BEV imports/CUDA OK")'
```

vvu 예제의 `/home/vvu/...`는 새 장비의 실제 경로로 변경. wrapper의 `CARLA_PYTHON_BIN`, `DAMOS_BEV_PYTHON`, `DAMOS_BEV_CHECKPOINT`와 `--observer-camera-config "$DAMOS_ROOT/sensor_config.txt"`도 확인. 이미 설치된 vvu 환경에 위 설치 명령을 다시 실행할 필요는 없음.

### ZKP 준비

```bash
cd "$CARLA_ROOT/_DAMOS/vendor/damos_zkp"
node --version
npm --version
npm ci --ignore-scripts --no-audit --no-fund
python check_assets.py
```

vvu의 구버전 `/usr/bin/node` 대신 필요하면 `export PATH=/home/vvu/.nvm/versions/node/v22.22.0/bin:$PATH`. 다른 설치에서는 실제 Node 경로 사용, 직접 지정은 `V2V_NODE_CMD`. 원본 회로·키 재생성 불필요. assets의 신뢰 설정 한계는 [handoff 문서](../Carla-0.9.16-source/_DAMOS/vendor/damos_zkp/docs/ASSET_HANDOFF.md) 참조.

### 가중치 준비

전달받은 파일을 `$CARLA_ROOT/_DAMOS/models/yerin_bev/`에 배치하고 SHA-256 확인. 원본 하나로 학습된 Scenic 기준 모델을 대체할 수 없음.

| 파일 | SHA-256 |
|---|---|
| `best_people_cam+lidar_scratch_100k.pt` | `c33a825ac168f3f7e1b271219a36af99d8f17d3854a76ee00a586967e338a962` |
| `scenic_presence_ground_guard_20261008.pt` | `3600d8e29f602f77ca142637c0336d267e0d041a2d5486739fca0070e9e7646e` |

```bash
sha256sum "$CARLA_ROOT/_DAMOS/models/yerin_bev/"*.pt
```

원본 모델·입력 규약 출처는 `vendor/yerin_bev/provenance.json`. 비밀번호 포함 handover README나 비밀값은 Git에 추가하지 않음. 설치 후 첫 확인은 단일 S5 offscreen 캡처, 이후 단일 S5 BEV 순서로 진행.

## 검증과 공유

소스 수정 후 문법·회귀 검사. 실제 Groth16 시험은 Node·vendor 의존성이 필요하며 skip도 확인.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
/home/vvu/vv/DAMOS/.venvs/bev/bin/python -m py_compile \
  _DAMOS/scripts/*.py _DAMOS/unreal/*.py _DAMOS/carla_import/*.py

for script in _DAMOS/scripts/*.sh _DAMOS/custom_walkers/*.sh _DAMOS/unreal/*.sh
do
  bash -n "$script" || exit 1
done

DAMOS_TEST_REAL_ZKP=1 /home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  -m unittest discover -s _DAMOS/tests -p 'test_*.py'

git -C /home/vvu/vv/DAMOS/_git/STL_DAMOS diff --check
```

실행 경로를 바꿨다면 단일 S5도 실제 확인. Python 시험만으로 C++·asset·실제 카메라 갱신까지 검증됐다고 보지 않음. 문서만 수정한 경우 검증 범위도 문서 검사로 구분.

제출 ZIP 메타데이터 재계산은 CARLA·모델 재실행 없이 Python 표준 라이브러리로 수행. 각 ZIP을 별도 폴더에 풀고 해당 루트에서 실행:

```bash
# 2번항목_메타데이터.zip 루트
python -X utf8 -B tools/verify_submission.py .
```

```bash
# 3번항목_메타데이터.zip 루트
python -X utf8 -B tools/verify_metric3_retest.py .
```

**최신 3번 제출자료 확인:**

- 파일: `공인인증평가/3번항목_메타데이터.zip`.
- 정상 평가 100건·성공 97건·탐지 실패 3건·미완료 실행 오류 0건 확인.
- `retest_plan.json`, `condition_comparison.json`으로 실행 오류 7건의 동일 조건 재평가 확인.
- `previous_execution_attempts/`에 최초 오류 기록 보존.
- 619개 시점별 판정 자료는 독립 시험 100건과 구분.
- ZIP SHA-256: `ce0fdbe9567fcb55821199161ab9c488025db4248117c477b3060cff4888ddbf`.

**피드백 대응 자료:**

- 요약·현재 담당 상태: [컨설팅 보완 대응](status.md#컨설팅-대응-현황), [연말 제출 준비 현황](status.md).
- 원문 목차·현재 대응·반영 위치·후속 계획: [컨설팅 보완 대응](status.md#컨설팅-대응-현황).
- 실패 원인·수정·재검증: [5번 대응](2026.md#5-실패-분석과-변경재검증).
- 10월 31일 개선 자료: `DAMOS_results/perception_relative_20261031_v1/`의 `final_results.json`, `metadata.tar.gz`, `backup_manifest.json`. 기존 제출 ZIP과 별도 보관.

코드·문서는 Git, 가중치·입력·검증 JSON·영상은 별도 공유. 원본 시험 폴더와 최초 오류 기록 보존, 최신 제출자료와 과거 참고본 구분. 과거 수집·학습·후보 선정의 세부 명령은 [정리 전 사용법](https://github.com/7107190/STL_DAMOS/blob/e018060cf17b0d2991ee3a703cfbe4f1b5723d26/docs/usage.md)에 보존.

## 자주 나는 문제

| 증상 | 확인·대응 |
|---|---|
| `conda activate carla4` 실패 | vvu의 `/home/vvu/anaconda3/envs/carla4/bin/python` 절대 경로 사용 |
| `carla`·`scenic` import 실패 | 해당 Python에 SDK/checkout 연결 여부 확인 |
| SSH에서 창이 안 보임 | display/VNC 없으면 offscreen 캡처 사용 |
| RGB가 비어 있음 | `--headless`/nullrhi 대신 `--offscreen` |
| 안개가 보이지 않음 | `--quality-level Epic` 사용. Low의 후처리 제한과 구분 |
| 포트 충돌 | 다른 작업을 임의 종료하지 말고 빈 포트 선택 |
| 배달로봇 RGB 내용 정지 | C++ 수정·CARLA 모듈 재빌드·서버 재시작 후 새 취득 |
| CUDA/Mamba 오류 | Python·torch·CUDA·ABI 조합 확인, 모의 결과로 우회 금지 |
| GPU 메모리 부족 | 단일 상황·적은 이웃으로 확인. 이웃마다 모델이 추가됨 |
| 기준 가중치 없음 | 별도 전달·해시 확인, 원본으로 조용히 대체하지 않음 |
| 결정적 CUDA 작업공간 충돌 | 오류 확인 후 외부 `CUBLAS_WORKSPACE_CONFIG` 해제, 새 프로세스에서 재실행 |
| 영상은 있지만 종료 코드 실패 | `video_complete`와 coverage·접촉·센서 실패 기록을 함께 확인 |

## 코드·실행 경로

**저장소 성격:**

- `/home/vvu/vv/DAMOS`와 같은 상대 경로의 **source overlay**.
- 전체 CARLA/Unreal·Scenic checkout·커스텀 assets는 별도 필요.
- 가중치·가상환경·데이터셋도 별도 필요.
- clone만으로 실행 불가.

```text
STL_DAMOS/
├── README.md                         # 전체 시스템·서브시스템·평가지표
├── sensor_config.txt                 # 모빌리티별 센서 장착 기준
├── docs/
│   ├── 2025.md                       # 1차년도·서브시스템별 수행내용
│   ├── 2026.md                       # 2차년도·서브시스템별 수행내용
│   ├── metrics.md                    # 성능지표 1~4·평가·증빙
│   ├── usage.md                      # 환경 설정·코드·실행·결과 확인
│   ├── status.md                     # 남은 작업·연말 제출 준비
│   └── meetings/
│       └── meeting_261030.md         # 날짜별 회의 기록
├── Carla-0.9.16-source/
│   ├── _DAMOS/
│   │   ├── _scenarios/               # Scenic S1~S9·공유 날씨
│   │   ├── scripts/                  # 센서·인지·공유·병합·평가
│   │   ├── tests/                    # 회귀시험
│   │   └── vendor/                   # 제공 BEV·ZKP 구현
│   ├── Scenic/Maps/                  # 도로 네트워크
│   └── Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/
├── UnrealEngine_4.26/README.md
└── archive/
```

**작업·보존 위치:**

- 주작업 루트: `/home/vvu/vv/DAMOS`.
- 명령 실행 위치: `Carla-0.9.16-source`.
- Git mirror: `_git/STL_DAMOS`.
- 원시 입력·BEV·로그·영상·시험 원본은 Git에 미업로드.
- Track 2 모델·실행 소스는 별도 제출 패키지에 포함.
- Track 2를 이 트리의 BEV 모듈로 간주하지 않음.
