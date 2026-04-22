# Scenic + CARLA 시나리오 프로젝트

CARLA 시뮬레이터 위에서 Scenic 언어를 사용하여 다양한 자율주행 테스트 시나리오를 생성하고 실행하는 프로젝트입니다.
이 문서는 기존 `GUIDE.md`를 대체하는 현재 사용 안내 문서입니다.

## 폴더 구조

```
Carla-0.9.16/
├── _DAMOS/
│   ├── _scenarios/      # DAMOS용 Scenic 시나리오 파일 (.scenic)
│   │   ├── S_.scenic    # 메인 시나리오 (BaseSetup + S1~S9 랜덤 조합)
│   │   ├── S_1.scenic 등 # 개별 실행/실험용 시나리오
│   │   └── run_selected.py
│   └── 3d_model/        # 3D 모델 리소스 모음
└── Scenic/
    ├── src/scenic/      # Scenic 프레임워크 소스 (가급적 수정 X)
    ├── Maps/            # CARLA OpenDRIVE 맵 (.xodr) 및 Scenic network 파일 일부 (.snet)
    ├── Python/          # 카메라 부착, 데이터 수집, 차량 제어 보조 스크립트
    ├── Data/            # 실행 로그 및 결과 파일 (records.csv/json/txt)
    ├── examples/        # Scenic 공식 예제
    ├── docs/            # Scenic 공식 문서 소스
    └── README.md        # 현재 프로젝트용 사용 안내 문서
```

`_DAMOS/_scenarios`는 현재 `Scenic/Maps`의 맵 파일을 참조하도록 맞춰져 있습니다.

## 설치

### 1. 권장 Python 환경

현재 프로젝트는 `conda` 환경 `carla4`에서 검증되었습니다.

```bash
# 예시: conda 환경 활성화
conda activate carla4

# 확인
python --version
which python
which scenic
```

검증 기준:

- Python 3.10.x
- `carla==0.9.16`
- `numpy<2`
- `opencv-python<4.10`

### 2. Scenic 설치/연결

공식 문서: https://docs.scenic-lang.org/en/3.x/quickstart.html

```bash
# carla4 환경에서 실행
cd Scenic
pip install -e .
```

로컬 Scenic 소스를 editable 모드로 연결해 두면, 저장소 내부 수정 사항이 바로 반영됩니다.

### 3. CARLA 설치

- CARLA 0.9.16 서버가 필요합니다.
- `CarlaUE4.sh`로 서버를 실행할 수 있어야 합니다.

### 4. 추가 패키지

```bash
pip install "numpy<2.0.0" "opencv-python<4.10" pygame
```

> numpy 2.x는 OpenCV 등과 호환 문제가 있으므로 반드시 `numpy<2.0.0`을 설치하세요.

## 실행 방법

### Step 1: CARLA 서버 시작

```bash
# Carla-0.9.16 루트 디렉토리에서
conda activate carla4
./CarlaUE4.sh -carla-rpc-port=2000 -carla-map=Town10
```

### Step 2: 시나리오 실행

**Carla-0.9.16 루트 디렉토리에서** 실행합니다.

```bash
conda activate carla4

# 메인 시나리오 (S1~S9 중 랜덤 2개 실행)
scenic _DAMOS/_scenarios/S_.scenic \
  --model scenic.simulators.carla.model \
  --simulate --2d --scenario BaseSetup

# 랜덤 실행 개수 변경 (예: 5개)
scenic _DAMOS/_scenarios/S_.scenic \
  --model scenic.simulators.carla.model \
  --simulate --2d --scenario BaseSetup \
  --param N_SCENARIOS 5

# 개별 시나리오 단독 실행
scenic _DAMOS/_scenarios/S_1.scenic \
  --2d --model scenic.simulators.carla.model --simulate
```

### 배치 실행

`run_selected.py`로 여러 시나리오를 연속 실행할 수 있습니다.

```bash
conda activate carla4
python _DAMOS/_scenarios/run_selected.py
```

`run_selected.py` 안의 `SELECTED` 리스트에서 실행할 파일을 지정합니다.

개별 `.scenic` 파일은 실험용으로 추가된 것들이 섞여 있으므로, 현재 메인 DAMOS 흐름은 `S_.scenic` 기준으로 보는 것이 가장 안전합니다.

## 시나리오 목록

`S_.scenic`은 아래 S1~S9를 N개 랜덤 조합하여 순차 실행하는 메인 시나리오입니다.

| 시나리오 | 설명 |
|---------|------|
| S1 | 보행자 무단 횡단 |
| S2 | 자전거 무단 횡단 |
| S3 | 비가시 영역(정차 차량 사이) 무단 횡단 |
| S4 | 도로 위 장애물 |
| S5 | 인도 위 불법 주차 |
| S6 | 도로 공사로 인한 차선 감소 |
| S7 | 인도 공사로 인한 통행 불가 |
| S8 | 인도 내 군중 |
| S9 | 인도 쓰레기 더미 |

## 주요 파라미터

`--param` 옵션으로 CLI에서 오버라이드할 수 있습니다.

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `N_SCENARIOS` | 2 | 메인 시나리오에서 랜덤 실행할 시나리오 개수 |
| `map` | `Town10HD.xodr` | 사용할 CARLA 맵 경로 |
| `carla_map` | `Town10HD` | CARLA 맵 이름 |
| `weather` | 랜덤 | 4가지 날씨 중 랜덤 선택 (맑음/폭우/짙은안개/야간) |

## Maps 폴더

`Maps/`에는 CARLA Town 맵의 OpenDRIVE(`.xodr`) 및 Scenic 네트워크(`.snet`) 파일이 포함되어 있습니다.
Scenic이 도로 네트워크를 파싱할 때 사용합니다.

포함된 맵: Town01, Town02, Town03, Town04, Town05, Town06, Town07, Town10HD
