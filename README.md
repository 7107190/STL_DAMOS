# STL_DAMOS

Ego 차량과 인도에 배치한 휴머노이드·배달로봇이 인지 정보를 공유해, Ego가 직접 보지 못하는 Scenic 이상상황의 장애물 존재·위치를 확인하는 협력 모빌리티 시뮬레이션.

문서 갱신: 2026-10-26. 프로젝트 방향은 [Notion SDV / DAMOS](https://www.notion.so/32d7d69010ae805b8b88c6175f5d9898), 구현·검증 범위는 이 저장소 기준.

## 현재 상태

| 항목 | 확인된 결과 | 해석 |
|---|---|---|
| 성능지표 2: 처리속도 | 입력 9식 × 각 15회, 135/135 완료. 최대 1.911553289초 | 사전 취득 입력의 Ego 처리시간, 목표 18초 이내 |
| 성능지표 3: 탐지정확도 | 신규 앵커 100건 중 90건 성공, 90% | 병합 BEV의 장애물 존재·위치 평가, 목표 70% 이상 |
| S5 고장 대응 | 기본 날씨 4조건 × 고장 15종, 60/60조건 | 불량 데이터 배제·감시자 대체 또는 정지 요구·서비스 복귀 확인 |
| F09 독립 감지 보완 | S5 × 기본 날씨 4조건, 4/4조건 | 과거 모델 출력 자동 배제·감시자 대체·정상 복귀 |

**60/60은 장애물 검출 100%나 15종 고장 원인의 자동 진단을 의미하지 않음.** 10월 26일 F09를 보완해 주입 이름 없이 출력의 프레임·수집시각·실제 추론 완료 기록으로 오래된 결과를 배제. 기존 60조건·제출자료는 보존하며, F09 새 검증은 별도 집계. OS 프로세스 멈춤·현재 메타데이터로 위장한 잘못된 출력의 감지는 별도 범위.

2·3번 메타데이터 준비·독립 재계산 완료. 기관 발급 성적서와 내부 기준 충족 판정은 구분.

## 문서 안내

| 문서 | 역할 |
|---|---|
| [사용법](docs/usage.md) | 환경 준비·실행 명령·카메라·결과 파일·문제 해결 |
| [검증 기술문서](docs/consulting_response.md) | 고장 판단 방식·15종 목록·60조건 통과 기준·성능지표·한계·증빙 |
| [런타임 안내](Carla-0.9.16-source/_DAMOS/README.md) | 내부 모듈·디렉터리 역할 |

## 시스템 구성

- CARLA Town10HD_Opt + Scenic S1~S9. 기본 Ego는 빨간 `vehicle.ford.mustang` 1대, 일반 실행에서는 autopilot 사용.
- 앵커마다 감시자 1대. 휴머노이드/배달로봇 중 랜덤 선택, 인도에서 해당 상황 방향 감시.
- BEV 모드의 센서: 모빌리티별 RGB 6방향 + 일반 LiDAR 1개. 장착 기준은 [sensor_config.txt](sensor_config.txt).
- 모델 입력: RGB 6방향 × 4시점 + 현재 LiDAR. 출력: 17종 BEV 확률·관측 마스크.
- Ego ↔ 가까운 감시자 직접 TCP 교환, 기본 반경 60m. 감시자끼리 통신 제외.
- 실제 Groth16으로 등록 송신자·메시지 결합 검증 후 Ego 좌표계로 BEV 병합.
- 목표는 장애물 존재·위치와 비가시권 보완. 세부 종류·무단횡단 행동 분류는 별도이며, GT는 평가에만 사용.

`--live-bev-fusion`은 인지·BEV 공유, 이전 `--live-ego-sharing`은 정면 이미지 1장 공유 전용. 두 모드 동시 실행 불가.

## 시나리오

| ID | 이상상황 | 객체 위치·생성 수 | 앵커 수 |
|---|---|---|---:|
| S1 | 보행자 무단횡단 | 검증된 3구역 안 랜덤, 보행자 3 | 3 |
| S2 | 자전거 무단횡단 | 랜덤 인도 위치, 자전거 3 | 3 |
| S3 | 비가시영역 무단횡단 | 고정, 보행자 3 | 3 |
| S4 | 도로 위 장애물 | 랜덤 차로 위치, 대형 container 4 | 4 |
| S5 | 인도 위 장애물 | 고정, 불법 주차 차량 6 | 1 |
| S6 | 도로 공사·차선 감소 | 고정, barrier/warning 36 | 1 |
| S7 | 인도 공사·통행 불가 | 고정, barrier/warning 40 | 1 |
| S8 | 인도 군중 | 고정 3군집 × 보행자 7, 총 21 | 3 |
| S9 | 인도 쓰레기 더미 | 고정 5군집, trash/bin 40 | 5 |

S1~S4는 개별 객체, S5~S9는 구역·군집을 앵커로 사용. 감시자 위치는 고정 상황의 검증된 인도 위치 또는 랜덤 앵커에 맞춘 인도 위치.

날씨는 `low_sun`·`heavy_rain`·`dense_fog`·`wet_night` 4조건이며 [공유 JSON](Carla-0.9.16-source/_DAMOS/_scenarios/weather_presets.json)으로 관리. 일반 실행은 Low 품질 기본값, 실제 안개·후처리 효과가 필요하면 `--quality-level Epic` 지정. 고장 시험은 Epic 기본값.

HW/SW·통신 고장은 F01~F15 총 15종, 단일 13종·복합 2종. [전체 목록과 판단 방법](docs/consulting_response.md#고장-15종). 이미지 보고서의 8종은 이 목록의 부분집합으로 별도 합산하지 않음.

## 빠른 실행

아래는 vvu에 이미 준비된 BEV 환경 기준. 새 장비는 [환경 설치](docs/usage.md#새-컴퓨터-설정)부터 확인.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt \
  _DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2620 --selected-scenario S5 \
  --runs 1 --scenic-time 30 --realtime-factor 1.0 \
  --quality-level Epic \
  --live-bev-fusion --bev-rounds 3 --bev-radius-m 60 \
  --evaluate-bev --no-trajectory-report
```

처음 확인할 때는 S5 단독 실행 권장.

일반 로그는 `_DAMOS/logs/`, BEV 결과는 `_DAMOS/reports/live_bev_*/`. GUI·카메라·랜덤 3종·고장 시험 명령은 [사용법](docs/usage.md) 참조.

## 작업 장소·공유

| 용도 | 경로 |
|---|---|
| 주작업·실행 환경 | `/home/vvu/vv/DAMOS` |
| 명령 실행 위치 | `/home/vvu/vv/DAMOS/Carla-0.9.16-source` |
| Git mirror | `/home/vvu/vv/DAMOS/_git/STL_DAMOS` |

```text
STL_DAMOS/
├── README.md
├── sensor_config.txt
├── docs/                   # 사용법·검증 기술문서
├── Carla-0.9.16-source/
│   ├── _DAMOS/
│   │   ├── _scenarios/     # S1~S9·공유 날씨
│   │   ├── scripts/        # 센서·인지·공유·평가·시각화
│   │   ├── tests/
│   │   └── vendor/         # 제공 BEV·ZKP 소스
│   ├── Scenic/Maps/
│   └── Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/
├── UnrealEngine_4.26/README.md
└── archive/
```

이 저장소는 실행 루트와 같은 상대 경로의 **source overlay**. clone만으로 실행 불가. CARLA/Unreal 전체·Scenic checkout·custom assets·가중치·가상환경은 별도 필요. 생성물·데이터셋·로그·영상은 Git 제외.

개발 시 원래 actor ID·시각 대응 유지, Scenic 외부에서 임의 tick 진행 금지. GT를 추론·공유에 사용하지 않으며 실패 기록·제출 ZIP은 덮어쓰지 않음. [검증과 공유](docs/usage.md#검증과-공유) 기준 준수.

## 남은 범위

- 결과 미반환·OS 프로세스 멈춤 대응, 고장 중 장애물 탐지정확도, 자연 주행 안전전환.
- 실물 센서 고장·실도로·무선망·다중 PC·다수 감시자·다른 GPU 환경의 성능.
- 컨설팅 의견 1·3번 미래예측 자료는 담당자 확인 필요. 이번 2·4·5번 보완과 구분.
