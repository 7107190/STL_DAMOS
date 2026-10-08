# STL_DAMOS

**현재 상태: CARLA/Scenic 비정상 상황 생성, 감시자 배치, BEV 추론·ZKP 공유·ego 합병 구현. 공식 18초 성능지표 합격은 미확정.**

DAMOS(`Decentralized Autonomous Mobility Simulation`)는 ego 차량과 주변 휴머노이드·배달로봇이 인지 정보를 공유해, ego가 직접 보지 못하는 비정상 상황을 확인하는 협력 자율주행 시뮬레이션입니다. 프로젝트 방향은 [Notion SDV / DAMOS](https://www.notion.so/32d7d69010ae805b8b88c6175f5d9898), 구현·실행 상태는 이 저장소 기준입니다.

이 저장소는 `/home/vvu/vv/DAMOS`와 같은 상대 경로를 사용하는 **source overlay**입니다. 전체 CARLA/Unreal 실행 환경·모델 가중치·검증 결과물은 미포함. clone만으로 바로 실행되지는 않습니다.

## 문서 안내

| 목적 | 문서 |
|---|---|
| 현재 상태·실행 진입점·코드 구조 | 이 README |
| 환경 설치·명령 옵션·결과 파일·실험 재현 | [docs/usage.md](docs/usage.md) |
| 런타임 내부 구성 | [_DAMOS/README.md](Carla-0.9.16-source/_DAMOS/README.md) |

## 현재 구현 (2026-10-08)

| 구분 | 구현 범위 |
|---|---|
| 시뮬레이션 | Town10HD_Opt, S1~S9 중 랜덤 3종 동시 실행, 날씨 4개 preset 중 랜덤 |
| Ego | Scenic이 생성한 빨간 `vehicle.ford.mustang` 1대, autopilot |
| 감시자 | anchor당 1대, 휴머노이드/배달로봇 랜덤, 인도에서 비정상 상황 방향 감시 |
| 센서 | 모빌리티마다 RGB 6방향 + 일반 LiDAR 1개; 부착 기준 `sensor_config.txt` |
| 인지 입력·출력 | RGB 6방향 × 4시점 + 현재 LiDAR → 17종 BEV 확률·관측 마스크 |
| 공유 | ego ↔ 가까운 감시자 양방향, 기본 반경 60m; 감시자끼리 통신 제외 |
| ZKP | 제공 `DAMOS_zkp`의 실제 Groth16 회로·키·WASM 사용; BEV 패킷 adapter 별도 구현 |
| 합병 | 검증한 BEV를 ego 좌표계로 정렬·합병, 장애물 존재·위치 확인 |
| 평가 | 동일 actor ID·프레임의 Scenic anchor와 비교; GT는 평가용이며 추론·합병 입력 아님 |
| 도로 오탐 보정 | 일반 LiDAR 지면 분리·입체 구조 주변 보존, 별도 체크포인트로 명시 선택 |
| 시각화 | 동일 시점 2×3 BEV/RGB 영상, 장애물 주황색·지면 회색·미관측 어두움, 빨간 EGO 위치·방향 |
| HW/SW 고장 | ego 기준 9종 설명용 보고서, ego front camera fault 선택 지원; 실제 전 모듈 고장 시험과 구별 |

현재 목적은 **Scenic 비정상 상황 구역의 장애물 존재·위치와 공유 후 사각 부분 복원**입니다. 사람/차량 종류 정확도는 보조 진단. BEV 합병 성공만으로 모든 객체 검출·모든 사각 해소·무단횡단 행동 분류 성공을 주장하지 않습니다.

기존 `--live-ego-sharing`은 정면 이미지 1장 공유 경로입니다. 현재 `--live-bev-fusion`의 인지·BEV 공유 경로와 별개이며, 두 모드를 동시에 실행하지 않습니다.

## 처리시간 기준

김기웅 담당: **성능지표 2. 개별 모빌리티 시스템 시뮬레이션 처리 속도**. 2026-09-16 확인한 [Notion 목표 & 평가](https://www.notion.so/3337d69010ae804a8f87c6417232a9d1) 기준 1단계 목표 18초 이내, 평가 비중 25%. 후속 목표는 12초·8초입니다.

| 항목 | 현재 계측 정의 |
|---|---|
| 대상 | ego 대표 계측; 다른 모빌리티도 같은 처리시간이라는 보장은 없음 |
| 시작 | 참여 노드의 동일 4프레임 원시 입력 준비 후 worker 전달 직전 |
| 종료 | 전처리·추론·증명·송수신·검증·ego 좌표 합병·장애물 존재 판정 완료 |
| 시간 | 단일 호스트 공통 `perf_counter_ns`의 실제 경과시간 |
| 제외 | 센서/history 취득 대기, 모델/키 초기화·워밍업, 평가·파일 저장, 경로계획 |
| 시간 조건 | `pipeline_budget_pass`로 18초 이내 여부 기록 |
| 공식 합격 | `metric2_pass=null`; 인지 기준·반복 수·네트워크 조건·공인 평가 합의 필요 |

현재는 vvu 한 대에서 독립 프로세스·공유 GPU·TCP loopback으로 검증합니다. 실제 다중 PC·무선망 성능 시험이 아닙니다. ZKP는 등록 송신자와 전달 데이터의 결합을 검증하며 **모델 추론 실행·인지 정확도·현실의 진실성 자체를 증명하지 않습니다.**

Scenic의 실행시간·FPS·영상 길이는 18초 처리시간과 다릅니다. Track 2의 CARLA GT 기반 5초 미래 궤적 예측은 Track 1 계측 루프와 별도 관리합니다.

## 최근 수정·검증

| 항목 | 확인 결과·제한 |
|---|---|
| 배달로봇 카메라 정지 수정 | C++ 시각 숨김이 센서 scene-capture까지 전파되던 오류 수정. CARLA 모듈 재컴파일·서버 재시작 필수 |
| 오류 5종 재취득 | S2/S3/S5/S6/S7 총 350프레임, 각 노드 6방향 RGB 갱신·동일 actor/시각·차도·무접촉 확인, 실제 모델/Groth16/TCP/합병 재실행 |
| 증명 재검사 | 위 재취득 영상의 시작/중간/끝 표본 30개 재검증; 수신 경로에서는 전송마다 검증 |
| 최신 표시 영상 | S1~S9 지정 anchor 각 1곳, 599프레임·119.8초. EGO 표식 1,797개·감시자 원본 RGB·개별/합본 디코딩 해시 대조 |
| 회귀 시험 | BEV 171개 + 이미지 공유 20개 + 공유 영상 8개, 총 199개 통과. 단위/통합 시험이며 199회 CARLA 실행 아님 |

**과거 결과 정정:** 기존 S2/S3/S5/S6/S7은 frame ID가 증가해도 실제 RGB 내용이 정지해 있었습니다. 해당 5종의 과거 동기 RGB/BEV 검증 주장은 철회합니다. 이전 검출률·처리시간 기록을 최신 RGB 기반 협력 인지 성능의 증빙으로 사용하지 않습니다. 원본과 실패 기록은 보존합니다.

새 영상 취득은 6방향 실제 이미지 내용도 검사합니다. 이 검사는 움직이는 ego 통과 영상용이며, 정적 장면의 일반 고장 판별기는 아닙니다. S3의 Traffic Manager 보행자 제동은 촬영 보조로서 BEV 기반 회피 성능과 구별합니다.

최신 합본은 아래 경로에 보존합니다. S2/S3/S5/S6/S7은 재취득 입력, S1/S4/S8/S9는 정상 확인한 기존 RGB 사용. 주황색·EGO 표식 변경은 **표시 전용 재생성**이며 새 추론·증명·18초 시험이 아닙니다.

```text
/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/reports/
├── observer_camera_refresh_20261008/       # 재취득·카메라 수정 검증
└── bev_orange_ego_20261008/
    ├── tests_report.json
    └── videos/
        ├── S1-S9_orange_ego_2x3.mp4
        ├── S*_orange_ego_2x3.mp4
        └── video_index.json
```

도로 보정의 과거 599프레임 비교는 구역 검출 564/585 유지, 미검출 21·미평가 14프레임 잔존입니다. 이는 저장 입력의 개발 진단이며 새 전체 anchor 검출률이나 독립 반복 성능이 아닙니다. 상세 실험·실패·모델 선택 기록은 [사용법](docs/usage.md)에 남겨둡니다.

## 실행

아래 명령은 **vvu에 준비된 환경 기준**입니다. conda 활성화 불필요. 다른 서버는 [새 컴퓨터 세팅](docs/usage.md#새-컴퓨터에서-처음-세팅하기), [BEV 추가 환경](docs/usage.md#실제-bev-추론zkp-공유ego-합병), [ZKP 준비](docs/usage.md#실시간-정면-이미지-공유와-zkp)부터 진행합니다.

### 기본 시뮬레이션

랜덤 3종 동시 실행, 전체 실행 5회 반복. GUI가 부담되면 `--offscreen` 추가.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --port 2620 --n-scenarios 3 --runs 5 \
  --scenic-time 20 --realtime-factor 1.0
```

### BEV 추론·공유·합병

현재 도로 보정 후보를 명시적으로 선택. GPU 메모리 확인용으로 처음에는 `--n-scenarios 3` 대신 `--selected-scenario S5` 권장.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt \
  _DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2620 --n-scenarios 3 --runs 1 \
  --scenic-time 30 --realtime-factor 1.0 \
  --live-bev-fusion --bev-rounds 3 --bev-radius-m 60 \
  --evaluate-bev --no-trajectory-report
```

`DAMOS_BEV_CHECKPOINT` 생략 시 원본 모델 사용. 후보 가중치는 Git 미포함이며 vvu에는 준비됨. 신규 환경의 전달·해시 확인·보정 패키징은 [사용법](docs/usage.md#도로-오탐-보정-모델) 참조.

실제 처리 중에도 Scenic은 진행합니다. 교환 완료 후 최신 공통 입력으로 다음 라운드를 시작하며 모든 tick을 연속 처리하지는 않습니다. `--bev-rounds`는 교환 횟수, `--runs`는 전체 시나리오 실행 횟수입니다. 이웃 없음은 대기이며 성공 횟수에 포함하지 않습니다.

### 동일 시점 2×3 영상

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt \
  /home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/run_bev_passby_sweep.py \
  --scenarios S1 --seconds 30 --port 2670 \
  --report-root /여유공간있는/새_영상_폴더
```

윗줄 ego/감시자/합병 BEV, 아랫줄 ego/감시자/탑다운 RGB. 먼저 센서 취득 후 실제 모델·ZKP·합병을 재생하는 **오프라인 영상**, 실시간 속도 평가 아님. 상세 옵션·최소 디스크 조건은 [영상 사용법](docs/usage.md#동일-프레임-2×3-주행-영상) 참조.

## Scenic 시나리오

| 번호 | 상황 | 객체 위치 | 생성 객체 수 | anchor 수 |
|---|---|---|---:|---:|
| S1 | 보행자 무단횡단 | 검증된 3구역 안에서 랜덤 | 보행자 3 | 3 |
| S2 | 자전거 무단횡단 | 랜덤 인도 위치 | 자전거 3 | 3 |
| S3 | 비가시영역 무단횡단 | 고정 | 보행자 3 | 3 |
| S4 | 도로 위 장애물 | 랜덤 차로 위치 | 대형 container 4 | 4 |
| S5 | 인도 위 장애물 | 고정 | 불법 주차 차량 6 | 1 |
| S6 | 도로 공사·차선 감소 | 고정 | barrier/warning 36 | 1 |
| S7 | 인도 공사·통행 불가 | 고정 | barrier/warning 40 | 1 |
| S8 | 인도 군중 | 고정 cluster 3곳 | 보행자 21 | 3 |
| S9 | 인도 쓰레기 더미 | 고정 cluster 5곳 | trash/bin 40 | 5 |

S1~S4는 개별 객체, S5~S9는 의미 있는 구역/군집이 anchor입니다. 각 anchor에 감시자 1대 배치. S2·S4 랜덤 생성 방식 유지. 고정 시나리오의 감시자는 검증된 인도 위치, 랜덤 시나리오는 생성 anchor에 맞춘 인도 위치 사용.

## 저장소·코드 구조

```text
STL_DAMOS/
├── README.md
├── sensor_config.txt
├── docs/usage.md
├── Carla-0.9.16-source/
│   ├── _DAMOS/
│   │   ├── README.md
│   │   ├── _scenarios/       # Scenic S1~S9
│   │   ├── scripts/          # 실행·센서·인지·공유·평가·시각화
│   │   ├── tests/            # 회귀 시험
│   │   ├── vendor/           # 제공 BEV·ZKP 소스와 출처
│   │   ├── custom_walkers/   # 환경 확인
│   │   ├── unreal/           # walker asset import
│   │   └── carla_import/     # CARLA 연결 helper
│   ├── Scenic/Maps/          # OpenDRIVE
│   └── Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/
├── UnrealEngine_4.26/README.md
└── archive/
```

| scripts 내부 역할 | 주요 파일 |
|---|---|
| 실행·배치 | `run_scenic_custom_walkers_town10hd.sh`, `scenic_custom_walker_injector.py` |
| 센서·동기화 | `bev_sensor_rig.py`, `bev_camera_freshness.py`, `bev_scene_camera.py` |
| 추론·보정 | `bev_model_runtime.py`, `bev_lidar_calibration.py`, `bev_ground_guard.py` |
| ZKP·통신·합병 | `bev_protocol.py`, `bev_mesh.py`, `bev_live.py`, `bev_fusion.py` |
| 존재 판정·평가 | `bev_occupancy.py`, `bev_evaluation.py`, `bev_anchor_evaluation.py` |
| 이미지·영상 | `report_bev_camera_triptych.py`, `bev_passby_video.py`, `bev_video_render.py` |
| 수집·학습·진단 | `run_bev_*`, `train_bev_*`, `audit_bev_*`; 상세 명령은 사용법 |

vvu 런타임은 `/home/vvu/vv/DAMOS`, Git mirror는 `/home/vvu/vv/DAMOS/_git/STL_DAMOS`입니다. 하위 상대 경로는 동일하지만 mirror에 실행 환경 전체가 있지는 않습니다. import 경로·실행 명령 호환성을 위해 기존 scripts 경계 유지, 중복 문서 중심 정리.

Git 제외: CARLA/UE 전체 소스·빌드, 전체 Scenic checkout, 3D 모델 원본, `.venvs`, `models`, `datasets`, `logs`, `reports`, 캐시·백업. 대용량 입력·가중치·영상은 별도 전달하고 해시·출처 확인. 제공 BEV 원본 코드 해시는 `vendor/yerin_bev/provenance.json`에 기록합니다.

## 검증·다음 작업

전체 회귀 시험:

```bash
DAMOS_TEST_REAL_ZKP=1 /home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  -m unittest discover \
  -s /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/tests -p 'test_*.py'
```

실제 Groth16 시험에는 Node.js·ZKP 의존성 준비 필요. 문법·실행 검사는 [사용법](docs/usage.md#수정-후-검증) 참조. Git 명령은 런타임 루트가 아니라 Git mirror에서 실행합니다.

남은 작업:

- 수정된 최신 RGB로 전체 anchor 검출·사각 복원 재평가, 미검출·미평가 구역 확인.
- 이웃 수·랜덤 seed·날씨별 실제 전체 처리시간 반복 시험, 실패·시간초과 포함.
- 다중 PC·실제 네트워크 배치와 통신 보호 검증.
- 공식 계측 범위·정확도 기준·반복 수·합격 통계·공인 시험 환경 확정.
