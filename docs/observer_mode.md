# Custom Walker Observer Mode

현재 DAMOS Track 1에서 custom walker는 이동 목적의 actor가 아니라 비정상 상황을 바라보는 observer node입니다. 목표는 walker navigation이 아니라 ego가 직접 보지 못하는 비가시영역 정보를 custom observer가 보완하는 것입니다.

## 동작 개념

| 단계 | 내용 |
|---|---|
| 1 | Scenic이 S1~S9 비정상 상황을 생성 |
| 2 | injector가 생성 actor를 semantic anchor로 정리 |
| 3 | 각 anchor 주변 인도에 custom observer 1대 배치 |
| 4 | observer는 anchor 방향을 바라보도록 yaw 정렬 |
| 5 | observer에 `sensor_config.txt` 기준 6방향 RGB 카메라 부착 |
| 6 | ego와 observer의 camera/metadata를 이후 M2X 공유 입력으로 사용 |

`walker-mode`는 asset/controller가 움직일 수 있는지 확인하는 smoke test용입니다. 기본 실행은 `observer-mode`입니다.

## Runtime Modes

| Mode | 기본값 | 검증 기준 |
|---|---|---|
| `observer-mode` | Yes | observer-anchor 거리, anchor를 바라보는 yaw error, camera attachment |
| `walker-mode` | No | custom walker 이동 가능 여부 |

기본 observer mode 실행:

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

walker 이동 smoke test:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart \
  --headless \
  --walker-mode
```

## Anchor Policy

S1~S4는 개별 비정상 객체를 anchor로 봅니다. S5~S9는 여러 actor가 하나의 의미 단위를 만들기 때문에 vehicle region, construction region, crowd cluster, trash pile cluster를 anchor로 봅니다.

| 시나리오 | 비정상 상황 | Raw generated actors | Semantic anchors |
|---|---|---:|---:|
| `S1` | 보행자 무단횡단 | 3 | 3 |
| `S2` | 자전거 무단횡단 | 3 | 3 |
| `S3` | 비가시영역 무단횡단 | 3 | 3 |
| `S4` | 도로 위 장애물 | 4 | 4 |
| `S5` | 인도 위 장애물 | 6 | 1 |
| `S6` | 도로 공사로 인한 차선 감소 | 36 | 1 |
| `S7` | 인도 공사로 인한 통행 불가 | 40 | 1 |
| `S8` | 인도 내 군중 | 21 | 3 |
| `S9` | 인도 쓰레기 더미 | 40 | 5 |

`--max-anchor-pairs`는 legacy 옵션명입니다. 현재 의미는 “최대 몇 개 anchor까지 observer로 커버할지”입니다. 옵션을 생략하면 발견된 semantic anchor 전체를 커버합니다.

## Observer Policy

| 항목 | 현재 기준 |
|---|---|
| observer 수 | anchor당 1대 |
| observer 종류 | `deliverybot`, `humanoid`, `random` |
| 기본 종류 | `random` |
| spawn 위치 | 가능한 경우 anchor 주변 인도 |
| 방향 | anchor를 바라보도록 yaw 정렬 |
| 최대 observer-anchor 거리 | 기본 22.0 m |
| 최대 yaw error | 기본 35.0 deg |

관련 옵션:

| 옵션 | 의미 |
|---|---|
| `--observer-blueprint random` | anchor마다 휴머노이드/배달로봇 중 랜덤 선택 |
| `--max-observer-anchor-distance 22` | observer와 anchor 사이 허용 거리 |
| `--max-observer-facing-error-degrees 35` | observer가 anchor를 바라보는 yaw 오차 허용값 |
| `--max-anchor-pairs N` | 최대 N개 anchor만 커버 |

## Camera Layout

observer에는 기본적으로 6개 RGB 카메라를 붙입니다. 위치는 `/home/vvu/vv/DAMOS/sensor_config.txt`를 기준으로 합니다.

| Camera | Relative location | Relative rotation |
|---|---|---|
| `cam_front` | `[0.0, 0.0, 1.5]` | `[0, 0, 0]` |
| `cam_front_left` | `[0.0, -0.1, 1.5]` | `[0, -55, 0]` |
| `cam_front_right` | `[0.0, 0.1, 1.5]` | `[0, 55, 0]` |
| `cam_back` | `[0.0, 0.0, 1.5]` | `[0, 180, 0]` |
| `cam_back_left` | `[0.0, -0.1, 1.5]` | `[0, -110, 0]` |
| `cam_back_right` | `[0.0, 0.1, 1.5]` | `[0, 110, 0]` |

ego 차량도 동일한 6방향 camera capture를 저장할 수 있습니다. 카메라 이상상황은 ego `cam_front` 하나에만 적용합니다.

## Report Fields

Scenic injector는 observer와 anchor 정보를 report JSON에 저장합니다.

| Field | Meaning |
|---|---|
| `custom_walker_mode` | `observer` 또는 `walker` |
| `observer_to_anchor_distance` | observer와 Scenic anchor 사이 거리 |
| `observer_yaw_degrees` | observer 현재 yaw |
| `target_yaw_degrees` | anchor를 바라보기 위해 필요한 yaw |
| `facing_error_degrees` | yaw 오차 |
| `ego_to_anchor_distance` | ego와 같은 abnormal anchor 사이 거리 |
| `anchor_index` | 선택된 Scenic anchor index |
| `observer_role` | 해당 anchor에 배치된 `humanoid` 또는 `deliverybot` |
| `attached_sensor_count` | observer에 붙은 camera sensor 수 |
| `observer_camera_specs` | `sensor_config.txt`에서 읽은 6방향 camera mount |
| `observer_camera_attachments` | CARLA sensor actor id 목록 |

## 검증 기준

| 검증 | 통과 기준 |
|---|---|
| anchor 추출 | 선택된 Scenic 시나리오의 semantic anchor 수가 기대값과 일치 |
| observer spawn | 각 anchor마다 observer 1대 생성 |
| observer 위치 | observer가 도로 한복판/풀숲이 아니라 가능한 인도에 위치 |
| observer 방향 | observer가 anchor를 바라봄 |
| camera attachment | observer당 6개 camera sensor attach |
| actor camera capture | ego 6장 + observer별 6장 저장 |
| ego front fault | fault가 ego `cam_front`에만 적용되고 나머지는 정상 |
