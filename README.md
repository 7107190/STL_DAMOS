# STL_DAMOS

**현재 상태(2026-10-13): CARLA/Scenic·협력 BEV·ZKP 공유 구현 및 성능지표 2·3번 측정자료 준비 완료. 2번 최대 1.911553289초, 3번 앵커 100건 중 90건 성공(90%). 제출자료 기준 두 목표 충족, 기관 발급 성적서는 별도.**

DAMOS(`Decentralized Autonomous Mobility Simulation`)는 ego 차량과 주변 휴머노이드·배달로봇이 인지 정보를 공유해, ego가 직접 보지 못하는 비정상 상황을 확인하는 협력 자율주행 시뮬레이션입니다. 프로젝트 방향은 [Notion SDV / DAMOS](https://www.notion.so/32d7d69010ae805b8b88c6175f5d9898), 구현·실행 상태는 이 저장소 기준입니다.

이 저장소는 `/home/vvu/vv/DAMOS`와 같은 상대 경로를 사용하는 **source overlay**입니다. 전체 CARLA/Unreal 실행 환경·모델 가중치·검증 결과물은 미포함. clone만으로 바로 실행되지는 않습니다.

## 문서 안내

| 목적 | 문서 |
|---|---|
| 현재 상태·실행 진입점·코드 구조 | 이 README |
| 환경 설치·명령 옵션·결과 파일·실험 재현 | [docs/usage.md](docs/usage.md) |
| 런타임 내부 구성 | [_DAMOS/README.md](Carla-0.9.16-source/_DAMOS/README.md) |

## 현재 구현 (2026-10-13)

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

## 성능지표·진행현황

김기웅 담당 평가자료: **2. 개별 모빌리티 시스템 시뮬레이션 처리 속도**, **3. 복합 모빌리티 환경 이상상황 탐지 시 객체 인식 및 탐지정확도**.

| 항목 | 시험 단위·판정 기준 | 측정 결과 | 현재 상태 |
|---|---|---|---|
| 2번 처리속도 | S1~S9 입력 9식, 각 15회 처리. 전체 135회 정상 완료 및 최대 18초 이하 | 135/135 정상 완료, 최대 **1.911553289초**, 시간 초과 0회 | 목표 충족·메타데이터 준비 및 검증 완료 |
| 3번 탐지정확도 | 신규 장면별 사전 지정 앵커 1건, 총 100건. 앵커 성공률 70% 이상 | **90/100 성공, 90%**. 탐지시간 미달 3건·실행 오류 7건도 분모에 포함 | 목표 충족·메타데이터 준비 및 검증 완료 |

현재 자료의 `criterion_pass=true`는 지정 측정자료의 기준 만족 판정. 기관이 발급한 공인 시험성적서와 구분.

### 2번 처리시간 기준·결과

김기웅 담당: **성능지표 2. 개별 모빌리티 시스템 시뮬레이션 처리 속도**. 2026-09-16 확인한 [Notion 목표 & 평가](https://www.notion.so/3337d69010ae804a8f87c6417232a9d1) 기준 1단계 목표 18초 이내, 평가 비중 25%. 후속 목표는 12초·8초입니다.

| 항목 | 현재 계측 정의 |
|---|---|
| 대상 | ego 대표 계측; 다른 모빌리티도 같은 처리시간이라는 보장은 없음 |
| 입력·반복 | 시나리오별 ego·감시자 1대의 RGB 6방향 × 4시점 + 일반 LiDAR 1프레임. 입력 9식 × 처리 세션 3회 × 세션당 5회 = 135회 |
| 시작 | 원시 RGB·LiDAR 입력 메모리 준비 완료 후 worker IPC 전달 직전 |
| 종료 | 전처리·추론·증명·송수신·검증·ego 좌표 합병·장애물 존재 판정 완료 |
| 시간 | 단일 호스트 공통 `perf_counter_ns`의 실제 경과시간 |
| 제외 | CARLA 취득·시뮬레이션, 파일 읽기·이미지 디코딩, 모델/키 초기화·준비 실행, 결과 저장·시각화·사후 검증, 경로계획 |
| 대표값·판정 | 전체 135회 최대 처리시간. 전체 정상 완료 및 최대 18.0초 이하 |
| 측정 환경 | Goosebump, Ubuntu 24.04, Ryzen 9 9950X, RTX 5090. 단일 호스트·독립 프로세스·공유 GPU·TCP loopback |
| 결과 | 평균 1.448647846초, 최대 1.911553289초. 정상 완료 135회, 누락·중복·시간 초과 없음 |
| 제출자료 판정 | `analysis/evaluation_summary.json`의 `criterion_pass=true`. 실시간 실행의 `metric2_pass` 필드 및 기관 성적서 발급 여부와 구분 |

135회는 **같은 시나리오 입력을 각 15회 반복 처리한 횟수**. 서로 다른 시나리오 135건이나 신규 앵커 135건이라는 의미 아님.

2번 사전 취득 입력의 반복 처리는 Goosebump, 3번 신규 장면 취득·처리는 vvu에서 수행. 두 자료 모두 동일 호스트 내 독립 프로세스 간 TCP loopback 조건이며 실제 다중 PC·무선망 성능 시험과 구분. ZKP는 등록 송신자와 전달 데이터의 결합을 검증하며 **모델 추론 실행·인지 정확도·현실의 진실성 자체를 증명하지 않습니다.**

Scenic의 실행시간·FPS·영상 길이는 18초 처리시간과 다릅니다. Track 2의 CARLA GT 기반 5초 미래 궤적 예측은 Track 1 계측 루프와 별도 관리합니다.

### 3번 앵커 100건 평가

- S1~S8 각 11건, S9 12건. 서로 다른 생성 seed로 신규 장면 100회 실행, 추론 전에 평가 앵커 1곳씩 지정
- 고정 시나리오의 고정 배치 및 S2·S4의 랜덤 생성 방식 유지. 모델·후처리·평가 기준 고정, 결과에 따른 seed 교체·실패 건 재실행 없음
- 평가 대상은 **Ego 기준 병합 BEV에서 비정상 상황의 장애물 존재·위치 확인**. 세부 객체 종류·행동 분류는 평가 대상에서 제외
- 동일 actor ID·시각의 정답과 대조. Ego 차도 위치·80m × 80m BEV 범위·정답 3셀 이상 확보 조건에서, 겹치는 예측 장애물 셀 3개 이상이 8방향 연결되면 해당 시점 탐지로 판정
- 유효 탐지가 **0.5초 이상 연속** 확인되면 앵커 1건 성공. 미탐지·자료 누락·정답 부족·평가 범위 또는 차도 이탈 시 연속 구간 종료
- 정상 취득 시 최초 앵커 20m 이내 접근부터 6초·5Hz·30샘플 확보. 성공 조건 최초 충족 시 처리 종료, 미충족 시 지정 관측 구간 전체 분석
- **최종 성공·실패 판정 단위는 앵커 1건이며 총 100건**. 591개는 연속 탐지 확인에 분석한 중간 BEV 관측 자료 수로, 추가 시험 건수 아님
- 성공 90건, 연속 탐지시간 미달 3건(확인된 최장 구간 각각 0.4초), 실행 오류 7건(Ego 접근 제한시간 초과 2건·CUDA OOM 5건)
- CARLA GPU 메모리 누적 확인 후 남은 회차는 서버를 회차별 재시작. 기존 오류 결과 보존 및 전체 100건 분모 유지
- vvu, Ubuntu 22.04.5, Ryzen 5 9600X, RTX 5060 Ti 16GB. 센서 취득 후 실제 모델·Groth16·전송·검증·Ego 병합 처리이며 2번 처리속도 자료와 별도
- 평가 로직 단위시험 27개 통과. 합성 fixture 검사는 실제 CARLA 평가 100건과 별도

| 시나리오 | 평가 앵커 수 | 성공 |
|---|---:|---:|
| S1 | 11 | 10 |
| S2 | 11 | 10 |
| S3 | 11 | 9 |
| S4 | 11 | 10 |
| S5 | 11 | 10 |
| S6 | 11 | 10 |
| S7 | 11 | 10 |
| S8 | 11 | 10 |
| S9 | 12 | 11 |
| 합계 | 100 | 90 |

### 제출 메타데이터·검증

제출 파일은 공인인증평가 폴더의 아래 두 ZIP. 시험신청서 2·3번의 시험 건수·계측 구간·산식·판정 기준과 대조 완료.

```text
/Volumes/STL/과제/2025.04 콜럼버스 - DAMOS/1단계-2차년도[2026]/공인인증평가/
├── 시험신청서_DAMOS.hwp
├── 2번항목_메타데이터.zip
└── 3번항목_메타데이터.zip
```

2026-10-13, 실제 제출 ZIP을 새 임시 폴더에 압축 해제하고 **외부 원본 파일 읽기·네트워크 접근을 차단**한 상태에서 독립 재계산 완료. Python 표준 라이브러리만 사용, CARLA·인지모델 재실행 없음. 두 ZIP 모두 압축 무결성·파일 해시·기록 정합성 검사 통과.

- 2번: 135회 시작·종료 나노초 차이, 정상 처리·송수신 기록, 최대 처리시간 및 18초 기준 재계산 통과
- 3번: 장애물·정답 셀 목록의 연결성, 연속 시각 차이, 앵커별 판정 및 100건 성공률 재계산 통과
- 원본 추가 대조: 2번 저장 BEV 및 실제 증명 270개, 3번 처리된 원시 자료 591샘플과 실제 증명 558개 재검증 기록 보존. 이 추가 검증 결과와 ZIP만으로 수행한 메타데이터 검증은 구분

아래 검증 도구는 **각 ZIP에 동봉**. 압축 해제한 각 자료의 루트에서 실행하며, 파일 변경을 피하도록 `-B`, 한글 인코딩 대응을 위해 `-X utf8` 사용.

```bash
# 2번항목_메타데이터.zip의 압축 해제 폴더
python -X utf8 -B tools/verify_submission.py .

# 3번항목_메타데이터.zip의 압축 해제 폴더
python -X utf8 -B tools/verify_metric3_100.py .
```

원시 센서 입력·BEV 배열·가중치는 원본 저장소에 별도 보존. ZIP의 경로·SHA-256 목록으로 식별하며 Git에 업로드하지 않음. 검증 도구·시험 코드 스냅샷은 2번 ZIP의 `tools/`·`source/`, 3번 ZIP의 `tools/`·`frozen_core/`에 동봉. 3번 100건 원본은 `/home/vvu/vv/_Data/Carla/Columbus/DAMOS_results/metric3_100_20261012_v1`, 제출용 구성은 같은 저장소의 `submission_metric3_100_20261012`에 보존. 기존 9건은 예비자료이며 새 100건에 합산하지 않음.

## 개발·시각화 검증 (2026-10-08)

아래 599프레임 영상·개발 진단은 **3번의 신규 앵커 100건 평가와 별도**.

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

성능지표 2·3번 측정자료에 사용한 고정 체크포인트를 명시적으로 선택. GPU 메모리 확인용으로 처음에는 `--n-scenarios 3` 대신 `--selected-scenario S5` 권장.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt \
  _DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2620 --n-scenarios 3 --runs 1 \
  --scenic-time 30 --realtime-factor 1.0 \
  --live-bev-fusion --bev-rounds 3 --bev-radius-m 60 \
  --evaluate-bev --no-trajectory-report
```

`DAMOS_BEV_CHECKPOINT` 생략 시 원본 모델 사용. 해당 가중치는 Git 미포함이며 vvu에는 준비됨. 신규 환경의 전달·해시 확인·보정 패키징은 [사용법](docs/usage.md#도로-오탐-보정-모델) 참조.

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

현재 진행현황:

- 완료: 2번 135회 처리속도 측정·집계, 3번 신규 앵커 100건 평가·집계
- 완료: 제출 메타데이터 ZIP 및 검증 도구 준비, ZIP만 사용한 독립 재계산, 시험신청서 2·3번 평가 내용 대조
- 다음: 성과지표용 기술문서 작성. 시스템 구성·센서/인지/ZKP/병합 흐름·평가방법·측정 결과·증빙 파일 설명 포함
- 이후: 시험신청서·메타데이터·기술문서 전달 및 기관 시험성적서 발급 절차 진행

다중 PC·실제 무선망·다수 감시자 성능은 향후 확장 범위. 이번 2·3번 제출자료에서 검증한 조건과 구분.
