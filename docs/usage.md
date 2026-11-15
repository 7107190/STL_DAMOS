# DAMOS 사용법

기준: 2026-11-16, 5090 메인 서버 `/home/vv/vv/DAMOS`.
연구·구현 내용은 [2025년](2025.md)·[2026년](2026.md), 평가 기준·증빙은 [성능지표](metrics.md), 남은 작업은 [준비 현황](status.md) 참조.

## 작업·Git 기준

- 코드·`README.md`·`docs/` 원본을 **5090의 동일 Git 작업 폴더**에서 관리.
- 원격 저장소: `7107190/STL_DAMOS`, 브랜치 `main`. 5090에서 직접 fetch·push 가능.
- vvu의 `/data/DAMOS/_git/STL_DAMOS`는 이전 관리본. 향후 수정 기준은 5090.
- 서버 간 자동 동기화는 없음. GitHub에 반영할 변경은 커밋·푸시 필요.

```bash
cd /home/vv/vv/DAMOS
git status
git pull --ff-only
# 작업 후 변경 파일을 선택하여 반영
git add README.md docs/
git commit -m "docs: update project status"
git push origin main
```

5090의 저장소 전용 SSH 키는 해당 저장소에만 접근. 개인키·SSH 설정은 Git에 포함하지 않음.

## 폴더 구성

```text
DAMOS/
├── README.md
├── docs/                       연도별 수행내용·성능지표·계획·회의
├── start_damos.sh               일반 시스템 실행
├── start_metric2.sh             처리속도 화면
├── start_metric3.sh             탐지정확도 화면
├── shared/
│   ├── runtime/
│   │   ├── Carla-0.9.16-source/  CARLA 프로젝트와 DAMOS 코드
│   │   ├── UnrealEngine_4.26/    공통 엔진
│   │   └── sensor_config.txt
│   ├── models/                 BEV 가중치, Git 제외
│   ├── venvs/                  base·simulation 실행환경, Git 제외
│   ├── python/                 Python 실행 파일, Git 제외
│   ├── runtime_overlay/        Mamba 등 추론 의존성, Git 제외
│   ├── tools/                  Node 등 실행 도구, Git 제외
│   ├── cache/                  지도 캐시, Git 제외
│   ├── locks/                  실행 잠금, Git 제외
│   ├── env.sh                  공통 경로·모델 설정
│   └── damos_paths.py          공통 경로 조회
├── metric2/                    평가·연동 코드·설정·실행 중 결과
├── metric3/                    평가·연동 코드·설정·실행 중 결과
├── metadata/                   보존 ZIP 재집계 도구
├── DAMOS-Observer-Research/     별도 연구 Git·모델·기존 실험 자료
└── runs/                       일반 실행 중 결과
```

- 일반 DAMOS 구현: `shared/runtime/Carla-0.9.16-source/_DAMOS/`.
- GitHub도 위 상대 경로 사용. 빈 자원·결과 폴더는 `.gitkeep`으로 표시.
- Git 포함: 시나리오·Python/C++ 코드·센서 설정·평가 화면·테스트·문서·필수 ZKP 회로와 공개 증명 자산.
- Git 제외: 엔진 전체·대형 바이너리·모델 가중치·가상환경·Node 설치본·센서 원본·BEV 결과·로그·영상·완료 시험자료.
- `runtime_ready.json` 등 설치·검증 표시는 PC별 로컬 상태. 다른 장비에서 검증한 것으로 복사하지 않음.

### Observer 연구 이전

- 위치: `/home/vv/vv/DAMOS/DAMOS-Observer-Research`.
- 별도 저장소: `7107190/STL_DAMOS-Observer-Research`. 기본 DAMOS Git에서는 제외.
- vvu 프로젝트 전체 12.55GB·파일 29,215개 해시 일치. 배포 소스·실험 기록·캐시·모델 포함.
- 최신 연구는 `agent/5090-migration-20261116` 브랜치 사용. 기존 연구 `main`과 동결 자료 보존.
- 공유 Python 사용: 연구 폴더에서 `bash run_5090.sh examples/demo.py`.
- 최신 소스 회귀 1,920개 통과, 기존 실제 캡처 입력의 선택 결과 재현.
- **Observer의 CARLA native 실험은 아직 5090 검증 완료가 아님.** vvu 전용 디스크·namespace·보호 설정을 별도로 준비해야 함.
- 상세 기록: 연구 저장소 `docs/MIGRATION_5090_20261116.md`.

### vvu에 남은 자료

- 일반 DAMOS 실행 코드·현재 BEV 모델·센서 설정은 5090에 존재. 두 실행 스크립트는 5090 경로·GPU·포트 수정본 사용.
- 원본/이전 모델·학습 데이터·3D 편집 원본·다른 연구용 holdout 시나리오·전체 CARLA/Unreal 빌드 소스는 vvu에 남아 있음.
- 현재 5090 일반 실행은 vvu 없이 가능. 단, 모든 과거 자료 이전이나 엔진 전체 재빌드 환경의 자립을 의미하지 않음.
- vvu 원본 삭제하지 않음. 서버 전체 정리 전 별도 보존 범위 확인 필요.

## DAMOS 일반 실행

5090 그래픽 데스크톱 터미널:

```bash
cd /home/vv/vv/DAMOS
bash start_damos.sh \
  --port 2840 --n-scenarios 3 --runs 1 \
  --scenic-time 300 --realtime-factor 1.0 \
  --live-bev-fusion --bev-rounds 3 --bev-radius-m 60 \
  --report-dir /home/vv/vv/DAMOS/runs/current
```

- 랜덤 이상상황 3종 **동시 실행**, Scenic의 빨간 Mustang Ego 1대 주행.
- 앵커마다 인도에 감시자 1대 배치. 휴머노이드·배달로봇 랜덤 선택.
- Ego·감시자 각각 RGB 6방향 × 4프레임 + 현재 LiDAR 1프레임 입력.
- 근거리 감시자와 실제 Groth16 증명·BEV 송수신·검증 후 Ego 좌표로 병합. 감시자끼리 통신하지 않음.
- `shared/env.sh`에서 환경·Scenic·모델 자동 선택. conda 수동 활성화 불필요.
- 위 명령은 공유·병합 3회 후 종료. 무한 실행 명령이 아님.
- SSH·창 없는 실행은 `--offscreen` 추가. `--headless`는 RGB가 없어 BEV 실행에 사용하지 않음.

| 옵션 | 의미 |
|---|---|
| `--selected-scenario S5` | 단일 상황 지정 |
| `--n-scenarios 3` | 랜덤 3종 동시 구성 |
| `--runs 5` | 전체 실행 5회 반복 |
| `--scenic-time 300` | 시뮬레이션 시간 상한. 추론 처리시간이 아님 |
| `--realtime-factor 1.0` | 시뮬레이션 진행 속도 조절 |
| `--bev-rounds 3` | 새로운 센서 입력의 BEV 공유·병합 반복 수 |
| `--bev-radius-m 60` | Ego 기준 통신 거리. 0이면 모든 감시자 |
| `--static-ego` | Ego 정지 상태 확인 |
| `--weather dense_fog` | 날씨 지정. 생략 시 기본 4조건 중 랜덤 |
| `--quality-level Epic` | 안개·야간 등 날씨 렌더링 효과 확인용 |
| `--offscreen` | 창 없이 RGB·LiDAR 렌더링 유지 |

기본 날씨: `low_sun`, `heavy_rain`, `dense_fog`, `wet_night`. 기본 품질 Low.
GPU 고정은 동일 UUID의 `CUDA_VISIBLE_DEVICES`, `DAMOS_GPU_UUID`와 해당 `DAMOS_RENDER_ADAPTER`를 함께 설정.

### 카메라 보기

일반 실행 중 별도 데스크톱 터미널:

```bash
cd /home/vv/vv/DAMOS
source shared/env.sh
"$DAMOS_PYTHON" "$CARLA_ROOT/_DAMOS/scripts/ego_spectator_follow.py" \
  --port 2840 --distance 10 --height 4 --pitch -16
```

정면 센서 창은 같은 환경에서 `live_ego_front_camera.py --port 2840` 사용.
GUI 없는 SSH에서는 화면 표시 대신 offscreen 실행의 저장 이미지 확인.

## 2·3번 평가 화면

```bash
cd /home/vv/vv/DAMOS
bash start_metric2.sh
# 3번은 별도 터미널에서 bash start_metric3.sh
```

- 2번: `http://127.0.0.1:8762/`, 3번: `http://127.0.0.1:8763/`.
- 인자 생략은 화면 조회. `--full`은 새 전체 시험 시작.
- `--scenario S5`: 대표 1건, `--three`: 누적 3건, `--five`: 누적 5건.
- 기본 설정에서 두 평가를 동시에 실행하지 않음. 일반 실행과도 GPU·포트 사용 구분.
- 2번: 현재 입력의 전처리·추론·증명·송수신·검증·병합 시간.
- 3번: 앵커별 연결 장애물 셀·연속 탐지시간 판정. 자세한 기준은 [성능지표](metrics.md).
- 과거 결과가 비어 있어도 정상. 신규 결과는 각 `reports/`·작업 폴더에 생성.

## 결과·NAS 보존

- 일반 결과는 `--report-dir` 아래 BEV·카메라·증명·궤적 JSON/PNG 등 저장.
- 완료 실험 보존: **109 NAS `Storage/Data/7107190/Damos` 아래로 한정**.
- Mac 접근: `/Volumes/Storage/Data/7107190/Damos`.
- vvu 접근: `/home/vvu/vv/_Data/7107190/Damos`.
- 완료 데이터는 NAS 백업·파일별 검증 후 서버에서 삭제. 실행 코드·모델·엔진 유지.
- 11월 11일 2·3번 원본: `5090_completed_runs_20261114/completed_runs.tar.zst`.
- 11월 15일 일반 점검: `5090_system_check_20261115/system_verification.json`, 같은 폴더의 압축 원본.
- 평가 JSON·CSV·증명 내용은 변경하지 않음. 원본·영상·가중치는 Git 제외.

### 과거 메타데이터 재집계

NAS 압축 내부 `metadata/inputs/metric2.zip`·`metric3.zip` 또는 해제본을 현재 `metadata/inputs/`로 선택 복원.

```bash
cd /home/vv/vv/DAMOS
bash start_metric2.sh --metadata --prepare
bash start_metric2.sh --metadata
```

3번은 `start_metric3.sh`. 압축 해제본을 복원했다면 `--prepare` 생략 가능.
저장 기록 재집계이며 신규 CARLA·모델 구동과 구분.

## 새 컴퓨터 설정

**Git clone만으로 바로 실행되지는 않음.** 현재 5090은 설정 완료. 새 장비는 다음 자원을 별도로 준비.

1. Linux x86_64·NVIDIA GPU 및 드라이버.
2. `shared/runtime/` 아래 CARLA 0.9.16 source build·Unreal 4.26·Scenic 전체 환경.
3. DAMOS walker factory 등록·휴머노이드/배달로봇 assets·Town10HD_Opt 맵.
4. `shared/venvs/base/`의 PyTorch/CUDA 의존성, `shared/venvs/simulation/`의 CARLA·Scenic 환경.
5. `shared/models/scenic_presence_ground_guard_20261008.pt`.
6. `shared/tools/`의 Node와 ZKP vendor의 npm 의존성, `shared/runtime_overlay/`의 Mamba 확장.

5090 기준 Python 3.10·torch 2.7.0+cu128. 복사 경로가 바뀌면 가상환경의 `.pth`·실행 스크립트 경로부터 확인.
모델 SHA-256: `3600d8e29f602f77ca142637c0336d267e0d041a2d5486739fca0070e9e7646e`.
full runtime·가중치는 별도 전달 필요. 전체 자동 설치 도구는 미제공.

회로·WASM·proving/verification 자산은 Git에 포함. 비밀값·계정·개인키는 미포함.
[워커 구성](../shared/runtime/Carla-0.9.16-source/_DAMOS/custom_walkers/README.md)·[ZKP 자산](../shared/runtime/Carla-0.9.16-source/_DAMOS/vendor/damos_zkp/docs/ASSET_HANDOFF.md) 참조.
새 PC는 설치·실구동 검증 후 로컬 readiness 표시 생성. 기존 PC의 `verified` 값을 그대로 신뢰하지 않음.

## 검증과 공유

```bash
cd /home/vv/vv/DAMOS
source metric2/env.sh
"$DAMOS_PYTHON" -B -m unittest discover -s metric2/app -p 'test_*.py'
source metric3/env.sh
"$DAMOS_PYTHON" -B -m unittest discover -s metric3/app -p 'test_*.py'
/usr/bin/python3 -B -m unittest discover -s metadata -p 'test_*.py'
git diff --check
```

실행 코드 변경 시 일반 단일 상황·센서·공유·병합까지 실제 확인. 단위 테스트만으로 전체 동작 검증을 대신하지 않음.

## 이전 환경·검증 기록

- 11월 15일: 랜덤 S9·S4·S2, Ego 약 44.6m 주행, 인도 감시자 12대·센서 91개.
- 실제 공유·병합 3회, 증명 6개 재검증·수신 몸체·재병합 배열 일치.
- 이번 점검은 전체 날씨·고장 조합 전수시험과 별개. 기존 60조건·성능지표는 [2026년](2026.md)·[성능지표](metrics.md) 참조.
- vvu `/data/DAMOS`와 `/home/vvu/vv/DAMOS` 링크는 보존. 5090과 자동 동기화되지 않음.
- vvu 설치·과거 고장/영상 명령은 [전환 전 사용법](https://github.com/7107190/STL_DAMOS/blob/17d5d5529e64e3c104e254751a398b4f29e32a85/docs/usage.md)에 보존.

### /data 이전 후 검증

vvu의 2026-11-04 이전·회귀·60조건 확인은 위 전환 전 사용법의 같은 항목 참조.
5090의 현 작업 위치는 `/data`가 아닌 `/home/vv/vv/DAMOS`.
