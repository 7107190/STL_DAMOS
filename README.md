# STL_DAMOS

DAMOS는 `Decentralized Autonomous Mobility Simulation`의 약자입니다. CARLA/Scenic에서 ego 차량과 custom observer가 서로 정보를 공유해, ego의 비가시영역에서 발생한 비정상 상황까지 대응하는 협력 자율주행 시뮬레이션을 목표로 합니다.

현재는 시나리오, observer, 센서 캡처 및 고장상황 보고서 생성까지 구현되어 있습니다. 협력 데이터 처리의 전체 연동과 성능지표 2번의 18초 이내 달성은 아직 검증되지 않았습니다.

이 저장소는 `/home/vvu/vv/DAMOS` 전체를 그대로 올린 백업이 아닙니다. 대용량 CARLA/Unreal/Scenic 원본은 제외하고, DAMOS 구현에 필요한 overlay 소스와 문서만 GitHub 구조에 맞춰 정리합니다.

## 프로젝트 기준

| 항목 | 기준 |
|---|---|
| 방향 기준 | [Notion SDV / DAMOS](https://www.notion.so/32d7d69010ae805b8b88c6175f5d9898) |
| 런타임 서버 | `vvu` |
| 실제 작업 루트 | `/home/vvu/vv/DAMOS` |
| CARLA 작업 루트 | `/home/vvu/vv/DAMOS/Carla-0.9.16-source` |
| 활성 DAMOS 코드 | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS` |
| GitHub mirror 기준 | `STL_DAMOS/Carla-0.9.16-source/_DAMOS` |

## 김기웅 담당 성능지표

2026-09-16 확인 기준, 핵심 달성 항목은 **결과물의 성능지표 2. 개별 모빌리티 시스템 시뮬레이션 처리 속도**입니다.

| 항목 | 기준 |
|---|---|
| 1단계 목표 (2025~2026) | **18초 이내** |
| 2단계 목표 (2027~2028) | 12초 이내 |
| 3단계 목표 (2029~2032) | 8초 이내 |
| 전체 평가 비중 | 25% |
| 평가 환경 | 모빌리티 간 협력 및 데이터 공유가 이루어지는 시뮬레이션 |
| 확인 내용 | 이동 정보, 환경 센서 데이터, 경로 최적화 정보의 공유 여부 |
| 2차년도 평가 | 공인 시험성적(확인)서; 평가방법·환경은 KOLAS 공인 기관 인증, TRL3 기준으로 원문에 기재 |
| 현재 달성 여부 | 미검증; 18초 판정용 계측 및 시험 증빙 미확보 |

출처: [Notion 목표 & 평가](https://www.notion.so/3337d69010ae804a8f87c6417232a9d1)의 성능지표 및 평가방법 표.

원문에는 계측 시작·종료 지점, 처리 단위, 통신·추론 포함 범위, 반복 횟수와 합격 통계가 구체적으로 정의되어 있지 않습니다. 평가 담당자와 이 조건을 확정한 뒤 실제 경과시간을 측정해야 합니다. `--scenic-time 20`, `--realtime-factor 1.0`, 화면 FPS 또는 캡처 생성 성공만으로 18초 목표 달성을 판정하지 않습니다.

김기웅 담당 구현은 CARLA 메인 동기화, actor 생성, 모빌리티별 6방향 이미지의 과거 6프레임 버퍼링 및 팀 모듈과의 시스템 통합입니다. 점유 예측·융합·ZKP 통신 모델은 담당 팀원과 연동합니다. 최신 Notion에서 룰 기반 브레이크/직진 제어는 취소선 처리되어 있어 현재 필수 후속 작업에서 제외합니다.

일정 참고: [260114_콜럼버스향후일정](https://www.notion.so/ba27d69010ae83b3be8b0111420af0db)에 최종 구현 목표가 **2026년 10월 중**, 공인 평가는 ANNA 진행 예정으로 기재되어 있습니다. 이는 회의록상의 계획이며, 공식 성과·연차보고서 제출 마감일은 확인되지 않았습니다.

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
| Track 1 | M2X 통합 시뮬레이션, 성능지표 2번 18초 이내 달성·입증 | CARLA + Scenic + observer + 센서 캡처·고장 보고서 구현; 협력 데이터 연동·처리시간 검증 대기 |
| Track 2 | CARLA GT 기반 5초 미래 궤적 예측 | Track 1 루프와 분리된 오프라인 정량 평가 |

Track 1은 합의된 처리 범위의 지연시간을 계측·최적화합니다. Track 2의 예측/학습/GT 평가는 별도 오프라인 경로로 분리합니다.

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
| ego 고장상황 9종 보고서 | 생성 완료; 실시간 고장 주입·모듈 정지 검증과는 구분 |
| 보고서 3x3 이미지 | 생성 완료, LiDAR 점·패널 확대 및 흰 여백 제거 |
| 협력 입력용 6프레임 버퍼 | 후속 작업; 보고서용 임시 프레임 수집과는 구분 |
| M2X·점유 예측·융합 연동 | 미완료 |
| 성능지표 2번 처리시간 검증 | 미완료; 측정 조건 확정 필요 |
| 다음 핵심 작업 | 평가 조건 확정, 6프레임 버퍼링, 팀 모듈 입출력 연동, 처리시간 계측·증빙 |

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
| `--save-ego-fault-report` | ego 기준 LiDAR noise, sensor delay, module stop 및 3x3 보고서 이미지 저장 |
| `--ego-front-camera-fault random` | ego `cam_front`에만 랜덤 카메라 fault 적용 |
| `--offscreen` | GUI 없이 RGB capture 가능한 CARLA 서버 실행 |
| `--static-ego` | 수동 확인용으로 ego 정지 |

## HW/SW 고장상황 구현 범위

모든 고장상황은 ego 기준으로 적용하거나 시각화합니다. Camera fault 선택 기능은 ego `cam_front` 하나에만 적용되며, observer 카메라와 ego의 나머지 카메라는 정상 이미지를 유지합니다. 아래 9개 항목의 보고서 생성은 구현되어 있지만, 9종 모두를 실제 협력 처리 파이프라인에 주입하고 영향을 검증한 상태는 아닙니다.

| 분류 | 고장상황 | 적용 대상 | 구현 방식 |
|---|---|---|---|
| HW-like | Camera blackout | ego front camera | 카메라 신호를 검정 화면으로 대체 |
| HW-like | Camera degradation | ego front camera | blur, occlusion, color failure, shaking 등 영상 품질 저하 |
| HW-like | Camera misalignment | ego front camera | 카메라 fault 선택 지원; 3x3 보고서는 캡처 영상의 회전·이동으로 재현 |
| HW-like | LiDAR noise | 캡처한 ego LiDAR | point cloud 좌표에 Gaussian noise를 적용한 보고서 |
| HW-like | LiDAR dropout | 캡처한 ego LiDAR | point cloud 일부를 제거한 보고서 |
| HW-like | LiDAR outlier | 캡처한 ego LiDAR | 가짜 point를 추가한 보고서 |
| HW/SW 경계 | Sensor delay | 수집한 ego RGB 프레임 | 과거·현재 프레임 비교로 지연 출력 재현 |
| SW-like | Module stop/freeze | ego RGB 기반 모의 출력 | 고정된 과거 프레임으로 출력 정지 재현; 실제 모듈 중지 아님 |
| SW-like | Stale perception output | ego RGB 기반 모의 출력 | 오래된 프레임으로 출력 노후화 재현; 실제 인식 결과 연동 아님 |

Camera fault 선택값:

| mode | 포함 항목 |
|---|---|
| `blackout` | Camera blackout |
| `blur`, `occlusion`, `color_failure`, `shaking` | Camera degradation |
| `misalignment` | Camera misalignment |
| `random` | visible camera fault 중 랜덤 선택 |

LiDAR noise/dropout/outlier, sensor delay, module stop/freeze, stale output 이미지는 `--save-ego-fault-report`로 저장합니다. 9개 항목을 한 장에 모은 `ego_fault_report_3x3.png`도 같이 생성됩니다. 예전 `/home/vvu/vv/DAMOS/Camera` 폴더는 legacy demo 코드입니다. 현재 실행 경로는 `_DAMOS/scripts/scenic_custom_walker_injector.py`와 `_DAMOS/scripts/live_ego_front_camera.py`입니다.

3x3 이미지는 고장 효과를 설명하는 자료입니다. 실제 인식 모듈 정지, 주행 대응, 협력 복구 또는 18초 처리 성능의 시험 증빙으로 사용하지 않습니다.

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
| Track 1 처리시간은 합의된 범위로 측정한다 | 성능지표 2번 18초 이내 달성·입증이 목표이며, 보고서 생성과 실제 처리 지연을 구분 |
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

| 우선순위 | 작업 | 완료 기준 |
|---|---|---|
| 1 | 성능지표 2번 평가 조건 확정 | 시작·종료 지점, 처리 단위, 포함 모듈, 반복 횟수, 합격 기준 문서화 |
| 2 | ego/observer 카메라 stream을 6프레임 버퍼로 연결 | 모빌리티별 6방향 이미지·frame ID·시각 정합 확인 |
| 3 | 팀 모듈과 데이터 입출력 연동 | 예측·통신·융합 담당자와 규격 합의, 실제 송수신·결과 대응 확인 |
| 4 | 실제 경과시간 계측 및 병목 개선 | 합의된 전체 처리시간과 단계별 시간 기록, 18초 기준 반복 검증 |
| 5 | 시험 증빙 확보 | 환경·조건·반복별 결과·실패 기록 및 공인 시험 제출 자료 정리 |

측정 설계와 현재 명령의 범위는 [사용법의 성능지표 2번 검증 준비](docs/usage.md#성능지표-2번-검증-준비)를 참고합니다. Track 2의 CARLA GT 추출은 별도 담당·평가 흐름으로 관리합니다.

## 참고 문서

| 문서 | 내용 |
|---|---|
| `docs/usage.md` | 환경 세팅, 실행 명령, 코드 사용법 |
| `Carla-0.9.16-source/_DAMOS/README.md` | DAMOS runtime wrapper 상세 |
