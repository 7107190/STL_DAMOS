# DAMOS 사용법

이 문서는 DAMOS를 처음 받는 사람이 “무엇을 설치해야 하고, 어디에서 어떤 명령을 실행해야 하는지”를 순서대로 확인할 수 있게 정리한 실행 가이드입니다.

**2026-10-08 기준: BEV 추론·ZKP 공유·ego 합병 구현, 공식 18초 합격 미확정.** 최신 상태는 [루트 README](../README.md), 상세 명령은 이 문서 기준입니다.

## 빠른 찾기

| 목적 | 위치 |
|---|---|
| vvu 기존 환경 실행 | [vvu에서 바로 실행하기](#vvu에서-바로-실행하기) |
| 새 서버·PC 설치 | [새 컴퓨터에서 처음 세팅하기](#새-컴퓨터에서-처음-세팅하기) |
| 인지·BEV 공유 | [실제 BEV 추론·ZKP 공유·ego 합병](#실제-bev-추론zkp-공유ego-합병) |
| 최신 도로 보정 후보 | [도로 오탐 보정 모델](#도로-오탐-보정-모델) |
| 동일 시점 BEV/RGB 영상 | [2×3 주행 영상](#동일-프레임-2×3-주행-영상) |
| 기존 이미지 공유·ZKP 설치 | [정면 이미지 공유](#실시간-정면-이미지-공유와-zkp) |
| 고장 효과 보고서·카메라 | [HW/SW 고장상황](#hwsw-고장상황-구현-범위), [실시간 화면](#실시간-화면-확인) |
| 검증 명령 | [수정 후 검증](#수정-후-검증) |

**과거 실험 해석 주의:** S2/S3/S5/S6/S7의 배달로봇 카메라가 이전 RGB를 반복하던 C++ 오류 확인·수정 완료. 아래 2026-10-07 수치와 모델 선택 기록은 당시 입력의 개발 기록이며 최신 RGB 협력 인지 성능의 증빙에서 제외합니다. 해당 5종 새 취득·모델/ZKP 재생 범위는 영상 절 참조. 정상 4종까지 모두 새 추론한 것은 아니며, 기존 파일·실패 기록은 보존합니다.

## 먼저 알아야 할 것

`STL_DAMOS` GitHub 저장소는 실행 가능한 전체 CARLA 패키지가 아니라 DAMOS source overlay입니다. 즉, GitHub repo만 clone해서는 바로 실행되지 않습니다.

| 구분 | 설명 |
|---|---|
| GitHub repo에 포함 | DAMOS scripts·tests, Scenic scenarios, docs, maps, Unreal overlay, 제공 BEV·ZKP 소스 |
| GitHub repo에 미포함 | 전체 CARLA/Unreal 실행 환경, BEV 가중치·데이터셋, 가상환경, logs/reports, 3D model 원본 |
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

## 성능지표 2번 검증 준비

2026-09-16 확인 기준, 김기웅의 핵심 달성 항목은 **개별 모빌리티 시스템 시뮬레이션 처리 속도**이며, 1단계(2025~2026) 목표는 **18초 이내**, 평가 비중은 **25%**입니다. 2차년도는 공인 시험성적(확인)서 평가가 기재되어 있습니다. 출처: [Notion 목표 & 평가](https://www.notion.so/3337d69010ae804a8f87c6417232a9d1).

`--live-bev-fusion`은 실제 인지 모델·BEV 공유·ego 합병·장애물 존재 판정까지 계측합니다. `--live-ego-sharing`은 기존 정면 이미지 공유 전용이며 BEV 추론을 실행하지 않습니다. 모든 비가시 객체의 복원·공식 18초 합격을 뜻하지 않으며 세부 종류 정확도는 보조 진단입니다.

| 구분 | 의미 |
|---|---|
| `--scenic-time 20` | 일반 모드의 Scenic 실행 시간 제한; live sharing에서는 공유 횟수까지 수명 연장 |
| `--realtime-factor 1.0` | 시뮬레이션 진행 속도 조절; 18초 달성 증빙 아님 |
| `--runs 5` | 시나리오 실행 반복; 공식 평가 반복 횟수로 확정된 값 아님 |
| `--save-ego-fault-report` | 고장 효과 설명 이미지 생성; 처리시간·실제 모듈 정지 시험 아님 |
| 18초 처리시간 | 공통 센서 입력 준비 후 전처리·각 객체 BEV 추론·ZKP·송수신·ego 합병 완료까지 실제 경과시간 |

원문의 평가 환경은 이동 정보, 환경 센서 데이터, 경로 최적화 정보의 공유 확인을 포함합니다. **아래는 2026-09-16 이미지 공유 합의를 2026-10-07 BEV 입력·합병으로 구체화한 구현 범위**이며, 경로계획 제외 범위와 공식 합격 통계는 평가 담당자에게도 확인해야 합니다.

| 항목 | 구현 기준 |
|---|---|
| 평가 대상 | ego만 대표 계측; 타 모빌리티의 동일 시간 달성 주장 아님 |
| 처리 단위 | 모빌리티마다 6방향 × 연속 4시점 + 현재 LiDAR → 17종 BEV 확률·관측 마스크 |
| 데이터 원천 | CARLA 실시간 센서, 처리 중에도 Scenic tick 유지 |
| 시작 | 동일 4프레임 원시 입력 준비 후 worker 전달 직전 |
| 종료 | ego의 자기·이웃 BEV 좌표 정렬·합병·장애물 존재 판정 완료; 경로계획 제외 |
| 인지 성공 기준 | 비정상 상황 구역의 장애물 존재·위치와 사각 부분 복원; 세부 종류는 보조 진단, 단순 합병 성공과 구분 |
| 포함 | IPC·전처리·GPU 전송/동기화·모델 추론·패킷/증명·TCP 대기/검증·좌표 합병·존재 판정 |
| 제외 | 센서/history 취득 대기·모델/키 초기화·실제 입력 워밍업·파일 저장·경로계획 |
| 시간 기준 | 단일 호스트의 공통 `perf_counter_ns` 시작값; CARLA 시각·타 PC 시각 차감 아님 |
| 후속 확정 | 공식 반복 수·합격 통계·하드웨어·실제 네트워크 환경 |

2026-10-07 실제 모델과 BEV 전용 ZKP 패킷 연결 완료. 원본 인지 소스·가중치 및 Groth16 회로·키는 유지하고 adapter만 별도 구현했습니다. 정면 이미지 전용 패킷과 BEV 패킷은 protocol/hash domain이 다릅니다.

별도 S1~S9 번호 분류기는 요구하지 않습니다. `pipeline_budget_pass=true`는 합병까지 처리시간 조건만 충족한 것이며, 비가시 객체 복원 미검증 시 `metric2_pass=null`입니다. 단일 vvu·공유 GPU·TCP loopback 결과를 실제 다중 PC/무선 네트워크 성능으로 해석하지 않습니다.

## 실제 BEV 추론·ZKP 공유·ego 합병

vvu는 별도 `/home/vvu/vv/DAMOS/.venvs/bev` 환경 준비 완료. 기존 `carla4` 패키지 변경 없음. 새 서버에서는 먼저 이 문서의 CARLA/Scenic·ZKP 환경을 준비하고, 아래 추가 설치를 진행합니다. 이 설치 예시는 **Linux x86_64 / Python 3.10 / torch 2.7.0+cu128 / CXX11 ABI TRUE** 전용이며 다른 조합에 그대로 사용하면 안 됩니다.

`vendor/yerin_bev/requirements.txt`는 **추가 패키지만** 포함합니다. CARLA/Scenic·PyTorch/CUDA·torchvision·NumPy·OpenCV·Pillow가 준비된 부모 환경이 선행 조건이며, 새 Python 환경에 이 파일만 설치해서는 실행되지 않습니다. 해당 원본 소스의 해시는 `vendor/yerin_bev/provenance.json`으로 관리하고 adapter는 `scripts/bev_*.py`에 분리합니다.

제공 원본 `viz_carla.py`의 후행 공백 1곳도 해시 보존을 위해 유지합니다. `.gitattributes`는 해당 파일의 후행 공백 검사만 예외 처리하며, 모델 동작·원본 파일 내용은 바꾸지 않습니다.

```bash
cd /home/vvu/vv/DAMOS
/home/vvu/anaconda3/envs/carla4/bin/python -m venv --system-site-packages .venvs/bev
.venvs/bev/bin/python -m pip install \
  -r Carla-0.9.16-source/_DAMOS/vendor/yerin_bev/requirements.txt
.venvs/bev/bin/python -m pip install --no-deps \
  'https://github.com/state-spaces/mamba/releases/download/v2.2.6.post3/mamba_ssm-2.2.6.post3%2Bcu12torch2.7cxx11abiTRUE-cp310-cp310-linux_x86_64.whl'
```

제공 가중치 `best_people_cam+lidar_scratch_100k.pt`는 Git 제외. 전달받은 handover의 `checkpoints`에서 다음 위치로 배치합니다. NAS 비밀번호가 들어 있는 원본 README는 저장소에 넣지 않습니다.

```bash
mkdir -p /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev
cp /path/to/carla_bev_handover/checkpoints/best_people_cam+lidar_scratch_100k.pt \
  /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/
sha256sum /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/best_people_cam+lidar_scratch_100k.pt
```

기대 SHA-256: `c33a825ac168f3f7e1b271219a36af99d8f17d3854a76ee00a586967e338a962`. 원본 소스 해시는 `vendor/yerin_bev/provenance.json` 참조. 가중치 519항목 전체 strict 로딩, 학습 100,000단계와 TC-STOP 설정 복원. 원본 시각화 wrapper의 `strict=False`/step=0을 그대로 쓰지 않습니다. CUDA·Mamba 실패 시 모의 출력으로 대체하지 않고 중단합니다.

Scenic 검증용 가중치는 별도 전달받아 같은 `models/yerin_bev/`에 배치합니다. Git에는 미포함, vvu에는 준비 완료. 최신 선택은 `scenic_presence_ground_guard_20261008.pt`이며, 이전 `scenic_presence_20261007.pt`는 비교·패키징용으로 유지합니다.

| 파일 | SHA-256 | 범위 |
|---|---|---|
| `scenic_presence_20261007.pt` | `03d3510f79284e43aebb710398bdbe25aaed4eeca209b9d3e22da4ca855cf638` | 일반 LiDAR 보정·`ego_observed` 합병 규칙 |
| `scenic_presence_ground_guard_20261008.pt` | `3600d8e29f602f77ca142637c0336d267e0d041a2d5486739fca0070e9e7646e` | 위 기준 가중치 + 지면 보정 설정 |

아래 `env DAMOS_BEV_CHECKPOINT=...`는 **해당 명령에만** 적용됩니다. 원본 비교는 해당 접두어를 빼고 실행하며, 이미 변수를 export한 셸이면 먼저 `unset DAMOS_BEV_CHECKPOINT` 실행. 기존 기준 모델만 있으면 [보정 패키징 명령](#도로-오탐-보정-모델) 사용 가능하며 새 생성 파일의 실제 해시를 기록합니다. 원본 가중치 하나만으로 학습된 기준 후보를 대신할 수는 없습니다.

주검증은 **Scenic으로 생성한 이상상황 객체·anchor의 검출과 공유 후 사각 복원**입니다. 같은 actor ID·프레임의 GT는 채점 전용이며 추론·ZKP 본문·합병 입력에 사용하지 않습니다. 미평가는 성공에 포함하지 않습니다. 2026-10-08 영상 판독을 방해한 도로 오탐은 별도 보정·진단하며, 일반 장면 전체 정확도나 정상 장면 오경보의 공식 합격으로 확대 해석하지 않습니다. 과거 전체 장면 gate의 보류 기록은 당시 기준으로 보존합니다.

실행 예시, 최신 도로 보정 후보 + ego autopilot + 랜덤 3종 시나리오. 최초 설치 확인에는 `--n-scenarios 3` 대신 `--selected-scenario S5`로 GPU 부하 축소 권장:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt \
  _DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2620 \
  --n-scenarios 3 --runs 1 \
  --scenic-time 30 --realtime-factor 1.0 \
  --live-bev-fusion --bev-rounds 3 --bev-radius-m 60 \
  --evaluate-bev \
  --no-trajectory-report
```

- wrapper가 `.venvs/bev/bin/python` 자동 선택. 수동 conda 활성화 불필요. 다른 설치 경로는 `DAMOS_BEV_PYTHON` 지정.
- 모빌리티마다 독립 GPU 프로세스·모델·비밀키·TCP inbox. 최초 실제 입력으로 워밍업 후 **새 프레임**으로 계측. 모델/키 준비 시간은 별도 기록.
- 가까운 감시자만 모델 로딩·워밍업·라운드 추론·BEV 교환. ego ↔ 감시자 양방향, 감시자끼리 통신 없음. 먼 노드 모델은 GPU에서 해제. actor·sensor ID 교체 없음. 최초 프로세스/키 등록은 전체 감시자, 신규 이웃 모델 준비는 별도 기록·계측 제외.
- 같은 4프레임 세트 사용, 현재 프레임 BEV만 전송. 처리 중 Scenic tick 유지; 처리 완료 후 최신 입력 세트로 다음 라운드. 모든 tick을 처리하는 구조는 아님.
- 증명 결합: BEV SHA-256·관측 마스크·actor/frame/session/round·수신자·시각·자세·모델 해시. 원본 canonical JSON 제약에 맞춰 자세는 1e6 고정소수 정수, 격자는 mm 정수. 수신자는 등록 ID·프레임·재전송·유효시간·본문 해시·Groth16 모두 검증.
- `--bev-rounds 3`: 교환 3회. `--bev-radius-m 60`: 60m 이내, `0`은 전체. 이웃 없음은 대기하며 성공으로 세지 않음. 안전 시간 안에 요청 횟수를 못 채우면 실패 종료.
- `--bev-timeout-seconds 60`: 실패 처리 상한, **18초 성능 목표와 별개**. 18초 초과는 완료돼도 `pipeline_budget_pass=false`.
- 격자 80×80m·0.4m, 17종 확률. 현재 LiDAR 반환점이 있는 셀만 관측으로 취급. ego 평면 z=0 기준 역좌표 정렬·최근접 보간. 원본은 confidence 가중 합병, Scenic 기준 모델은 ego 관측 유지·미관측 영역 공유 보완. 실제 완전 가시영역/빈 공간 추정이나 3D 지형 합병 아님.
- 주평가 목표는 장애물 존재·위치, 세부 종류 정확도는 보조 진단. 합병 최댓값 클래스를 장애물 / 지면 / 미확인으로 묶는 판정까지 18초 구간 포함. 미관측은 빈 공간으로 취급하지 않음.
- Groth16은 등록 송신자와 전달 데이터 결합을 검증. 실제 환경의 진실성·모델 추론 실행/정확도 자체를 증명하지 않음.
- 모델 수는 감시자 수에 비례해 GPU/RAM 사용 증가. 많은 이웃에서의 메모리·시간 보장 없음. 초기에는 단일 시나리오로 실행 권장.

결과: `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/reports/live_bev_<UTC>/`.

| 파일 | 내용 |
|---|---|
| `summary.json` | 환경·제외 구간·모델 준비 시간·ego 라운드 결과·처리시간 |
| `round_001/peers.json` | 모든 참여 노드의 실제 추론/증명/검증/합병 상세 시간 |
| `round_001/*_front.png`, `*_own.png` | 동일 actor·프레임 정면과 자체 BEV |
| `round_001/ego_received_observer_*.npz` | ego가 실제 수신·검증한 BEV |
| `round_001/*_packet.json` | BEV 해시·자세·프레임에 결합된 실제 Groth16 증명 및 공개 입력; 비밀키 없음 |
| `round_001/ego_fused.npz`, `ego_fused.png` | 합병 확률·관측 마스크·기여 노드·추가 셀·occupancy(0 미확인 / 1 지면 / 2 장애물) |
| `round_001/ego_obstacle_presence.png` | 종류를 묶은 존재 지도: 주황 장애물 / 회색 지면 / 보라 미확인 / 어두움 미관측 |
| `round_001/ego_presence_interpolation_comparison.png` | 같은 합병 결과의 원본 / 작은 틈 보간 그림, 표시 전용 |
| `round_001/presence_interpolation.json` | 보간 방법·거리 제한·추가 표시 셀 수, 평가/공유 미사용 명시 |
| `round_001/comparison.png` | ego 자체 BEV·ego가 실제 검증한 감시자 BEV·합병 비교, 같은 프레임 정면 포함 |

암호·좌표·센서 회귀 시험:

```bash
DAMOS_TEST_REAL_ZKP=1 /home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  -m unittest discover \
  -s /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/tests -p 'test_*.py'
```

### BEV 점무늬 시각화 개선

추가 옵션 없이 원본/보간 비교 그림을 함께 저장합니다. LiDAR 반환 셀만 표시해 생기는 작은 빈틈을 장애물/지면별 3×3 Closing 1회로 보완. 원본 같은 그룹 셀에서 약 0.57m 이내로 제한하고 관측된 셀·유효하지만 클래스가 미확인인 셀은 변경하지 않습니다. 보간한 셀을 다시 보간의 근거로 사용하지 않으며, 두 그룹의 제안이 충돌하는 곳·넓은 미관측 영역은 그대로 둡니다.

- 주황/회색: 원본 장애물/지면. 연주황/청회색: 표시용 보간 장애물/지면.
- 어두운 영역: 남은 미관측, 빈 공간이나 통행 가능 판정이 아님.
- NPZ·원본 PNG·ZKP·합병·검출 점수 유지. 보간 그림의 셀은 검출 근거로 세지 않음.
- 보간·PNG 저장은 기존 보고서 저장 단계에 포함, 18초 구간 밖. 그림 생성 부하는 존재하며 센서 입력·모델 추론의 정확도 개선은 아님.

기존 S5 F318/F341/F364를 재생성 없이 다시 그린 결과는 `_DAMOS/reports/live_bev_20261007T064102_535338Z/display_interpolation/round_00N/`에 저장. 각 프레임의 전체 ego/합병 비교와 6개 객체별 비교, 총 42장. `display_interpolation/summary.json`에 원본 파일 SHA-256·프레임·표시 정보 기록. 원본 JSON/NPZ 52개 변경 없음, 실제 Groth16 포함 전체 테스트 88개 통과. 처리시간 신규 재측정 아님.

### 실제 객체와 BEV 대조

`--live-bev-fusion`에 `--evaluate-bev`를 추가합니다. 다음은 이전 S5 정지 가림 장면의 재현 설정이며 **ego가 인도에 있어 자연 주행 검증용이 아닙니다.** 새 주행 시험은 아래 `시나리오별 차도 주행 검증` 명령을 사용합니다. actor ID는 실행마다 바뀌며, **한 실행·라운드 안에서는 원래 객체와 같은 프레임만 비교**합니다. 다른 시나리오에 S5의 위치를 그대로 적용하지 않습니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2662 --selected-scenario S5 \
  --runs 1 --scenic-time 15 --realtime-factor 1.0 \
  --static-ego --ego-start -59 -10 0 --weather ClearNoon \
  --live-bev-fusion --bev-rounds 3 --bev-radius-m 60 \
  --evaluate-bev --no-trajectory-report
```

- 모빌리티마다 평가 전용 semantic LiDAR 1개·6방향 instance 카메라, CARLA snapshot 추가. 원래 비정상 actor ID에 실제 맞은 점만 GT 사용. GT는 모델 입력·증명·송수신·합병에 사용하지 않음.
- GT snapshot·RGB·일반 LiDAR·평가 LiDAR 프레임 일치 필요. 다른 객체로 대체하거나 오래된 프레임으로 대체하지 않음. bbox와 collision mesh 불일치 가능해 bbox는 참고 정보, 채점·GT 그림은 exact actor-ID 반환 셀 기준.
- 주평가 단위는 **상황 구역(anchor)**. S5 차량 6대는 구역 1곳, S4처럼 떨어진 장애물은 각 구역으로 구분. 구역 내 원래 member actor의 GT 반환 셀을 합쳐, 같은 위치에 **연결된 원본 관측 장애물 셀 3개 이상**이면 구역 존재 진단 성공. 평가용 임시 기준이며 검증된 공인 검출 기준 아님. 전체 구역 발견과 이미 발견한 구역의 가려진 부분 복원은 별도 기록.
- 개별 객체 보조 존재 진단은 기존 최소 3 GT 반환 셀·장애물 50% 이상 기준 유지. 사람·차량·시설물·식생(클래스 1~10, 15~16)을 장애물, 도로·인도·지형(11~14)을 지면, class 0/미관측을 미확인으로 묶음. 최댓값 클래스 기준이며 전체 장애물 확률의 단순 합으로 판정하지 않음.
- 종류 정확도·3×3 격자 허용 오차(축마다 ±0.4m)는 보조 진단으로 유지. 존재 판정의 성공을 종류 일치로 제한하지 않음.
- 80×80m ego ROI 밖 객체는 실패로 세지 않음. ego LiDAR 반환 0만으로 RGB 가림을 주장하지 않음. `occlusion_verified`는 같은 프레임 6방향 ego target 픽셀 0, peer target 픽셀 3 이상, ego 시야 내 대상점 투영 3 이상, ego LiDAR 0·peer 양수일 때만 true. actor ID 16비트 초과/충돌·프레임 불일치 시 실패 종료.
- `occlusion_recovered`는 위 가림 검증 + ego 존재 진단 실패 + 합병 존재 진단 성공. 지면 오탐률·장애물 정밀도도 `scene_presence`에 기록. semantic 반환 셀 GT만 평가하므로 완전한 free-space 평가나 통행 가능 판정은 아님.
- 평가 센서 대기·채점·이미지/NPZ 저장은 18초 구간 제외. 평가 센서 자체의 시뮬레이터 부하는 존재하므로 평가 옵션 없는 시험과 완전히 같은 부하 조건으로 취급하지 않음.
- `metric2_pass=null` 유지. `pipeline_budget_pass=true`라도 장애물 존재·위치 복원 실패라면 인지 성공 아님. 모든 비가시영역이 해소됐다는 뜻도 아님.

추가 결과 파일, 각 `round_00N/`:

| 파일 | 내용 |
|---|---|
| `ground_truth.json` | 같은 프레임의 원래 actor ID·종류·실제 위치·속도·시나리오; 과거 자료의 bbox도 읽기 지원 |
| `*_eval_only_semantic.npz` | 평가 전용 실제 반환점·actor ID·semantic tag, 모델 입력과 분리 |
| `*_eval_only_instance.npz`, `*_rgb_cam_*.png` | 같은 프레임 6카메라의 instance ID/tag·실제 RGB; 가림 증거 |
| `evaluation.json` | `anchors` 상황 구역별 존재·신규 발견·사각 부분 복원, `ego_road_state` 차도 위치·속도, 객체·종류 보조 진단·지면 오탐 |
| `presence_target_<ID>.png`, `presence_crops.png` | GT / ego / 검증된 감시자 / 합병의 같은 16m 존재 지도, 흰 선은 대상 GT |
| `presence_interpolation_{ego,fused}_target_<ID>.png` | 같은 16m 영역의 원본 / 표시용 보간 비교, GT는 윤곽 표시에만 사용 |
| `visibility_target_<ID>.png` | 같은 actor·프레임의 ego/감시자 RGB, 노란 선은 실제 instance 윤곽 |
| `evaluation_overview.png` | 전체 80×80m GT·ego·합병 비교 |
| `evaluation_crops.png` | 같은 16m 영역의 객체별 GT·ego·합병 비교, 흰 선은 GT 반환 셀 |
| `*_model_inputs.npz` | 추론에 실제 사용한 고정 입력, 사후 재현용 |
| `*_projection_check.png` | 같은 프레임의 6카메라에 대상 LiDAR 점을 초록색으로 투영한 좌표 점검 |

#### BEV 아래 실제 카메라 이미지 추가

평가 실행에 `--bev-scene-cameras` 추가. `--evaluate-bev` 필수. 각 라운드의 **현재 BEV 입력 프레임과 정확히 같은** ego RGB·감시자 RGB·실제 CARLA 탑다운 RGB 저장. 기존 자료에 탑다운이 없으면 새로 동기 취득해야 하며, 이전 BEV에 다른 실행의 사진을 붙이지 않습니다.

```bash
env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_20261007.pt \
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/run_bev_driving_sweep.py \
  --scenarios S1 S2 S3 S4 S5 S6 S7 S8 S9 \
  --rounds 3 --scene-cameras --port 2663 \
  --report-root /여유공간있는/새_수집_폴더

/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/report_bev_camera_triptych.py \
  --sweeps /수집_폴더/bev_driving_sweep_<UTC>/summary.json \
  --checkpoint /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_20261007.pt \
  --output /절대경로/새_이미지_폴더 \
  --retain-root /절대경로/선택프레임_보존_폴더
```

- `Sx_bev_rgb_2x3.png`: 윗줄 ego BEV / 배정 감시자의 실제 수신 BEV / 실제 전체 이웃 합병, 아랫줄 ego RGB / 해당 감시자 RGB / 실제 탑다운. BEV 세 장은 같은 ego 좌표·같은 관심 구역.
- 원래 대상 actor ID·프레임·모델 해시·실제 Groth16·저장된 합병 재생 일치 확인. 대상 카메라는 6방향 중 시야 투영 기준 선택, 반드시 정면은 아니며 방향 표기.
- 저장 프레임 중 가림 검증된 대상의 최근접 장면 우선. 없으면 저장 대상의 최근접 장면을 `OCCLUSION NOT VERIFIED`로 표시. 검출 성공 여부로 장면을 고르지 않음. 연속 주행 전체의 절대 최근접 시점 보장은 아님.
- RGB 노란 윤곽은 실제 대상 instance, 청록 십자는 대상점의 가려진 투영 위치. 탑다운 E/O/T는 ego/감시자/대상 위치 안내. 모두 평가용 표시이며 모델 예측 아님.
- `overhead_rgb.png`, `overhead_capture.json`: 평가 전용 1600×1600 RGB·실제 센서 자세·intrinsic·프레임·시각·SHA-256. 모델·패킷·합병 입력에 미사용. 저장/대기는 처리시간 밖이지만 센서 렌더링 부하 존재.
- 입력 자료 변경 없음. 선택 라운드 전체를 `--retain-root`에 복사하며 실패 실행·미완료 라운드는 `summary.json`에 그대로 기록. 새 수집 자료와 기존 성능 시험을 구분. 이미지 생성만으로 모든 anchor 검출·공식 18초 합격을 주장하지 않음.

#### 동일 프레임 2×3 주행 영상

**2026-10-08 정정:** 기존 S2/S3/S5/S6/S7 배달로봇 영상은 카메라 6방향 모두 이전 화면 반복으로 동기 검증 완료 판정 철회. 기본 사람 모델 숨김이 부착 센서까지 전파되는 C++ 오류 수정, CARLA 모듈 재컴파일·서버 재시작 필요. 기존 파일 보존, 새 입력으로 재취득해야 하며 옛 MP4만 재합성해서 해결할 수 없음. BEV RGB 입력도 영향 대상. 과거 수치 기록은 최신 RGB 협력 인지 성능 증빙에서 제외.

수정 후 해당 5종 350프레임 새로 취득, 실제 모델·Groth16/TCP·합병 재실행 완료. ego와 배정 감시자의 각 6방향 원시 RGB가 매 프레임 변화하며 actor·시각·차도·무접촉 검사 통과. 시작/중간/끝 증명 30개 사후 재검증, 당시 회귀 시험 191개 통과(최신 표시 수정 후 총 199개). 결과·시험 범위는 `_DAMOS/reports/observer_camera_refresh_20261008/`에 보존. 지정 anchor 각 1곳의 영상 검사이며 전체 anchor 검출률·실시간 18초 성능 재평가와 구분.

수정 합본: `_DAMOS/reports/observer_camera_refresh_20261008/videos/S1-S9_observer_camera_fixed_2x3.mp4`, 개별 `S*_observer_camera_checked_2x3.mp4`. 총 599프레임·119.8초의 감시자 패널을 같은 프레임의 원본 RGB와 대조, 합본/개별 영상의 디코딩 프레임 해시 일치 확인. 정상 S1/S4/S8/S9는 기존 RGB와 오프라인 도로 보정 영상 유지, 오류 5종만 새 센서·실제 모델·증명 재생으로 교체. 종류별 출처·검사는 같은 폴더의 `video_index.json` 참조. 기존 파일은 삭제·덮어쓰기하지 않음.

스냅샷 대신 ego가 대상에 접근하고 지나가는 연속 장면을 생성합니다. **센서를 먼저 취득한 뒤 실제 모델·Groth16/TCP·합병을 실행하는 오프라인 재생**입니다. 정상 실시간 BEV 실행·18초 계측 경로는 바꾸지 않습니다. 모델 초기화 중 ego가 지나가거나 보행자와 충돌한 뒤의 사진을 사용하는 문제를 피하기 위한 별도 시각화 모드입니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt \
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/run_bev_passby_sweep.py \
  --scenarios S1 --seconds 30 --port 2670 \
  --report-root /여유공간있는/새_영상_폴더
```

- `--scenarios S1 S2 ... S9`: 선택한 종류마다 별도 실행·영상. 시나리오 전체 anchor가 아닌 지정한 anchor 한 곳을 촬영. 기존 Scenic 생성 로직은 유지하며 영상용 seed·차도 시작 위치·주행 경로만 명시적으로 지정.
- 영상용 저속 자동 주행: S1/S2/S3 목표 8km/h, 나머지 15km/h. 센서 준비 중 원래 ego의 브레이크 유지, 주행 중 ego·대상 순간이동·재생성 없음. S3는 기존 영상처럼 native CARLA Traffic Manager의 보행자 제동·고정 경로 사용. 나머지는 `LocalPlanner/PID`의 5Hz 경로 제어이며 회피 판단 없음. 모든 경우 접촉 시 영상 거부. S3의 제동은 **CARLA actor 상태 기반 촬영 보조이며 BEV 판단·18초 계측·회피 성능 평가가 아님**. 신호/표지 판단은 영상에서만 우회. BehaviorAgent의 bbox RPC 시간초과 및 PID 보행자 접촉 실패도 보존. S3는 제동 대기를 고려해 `--seconds 30` 권장. S2는 35m 전방 차선 시작·접근 구간 확대, S4/S6는 장애물 옆 Driving 차선 사용. 일반 Scenic 오토파일럿은 변경하지 않음.
- 공통 4프레임 입력의 끝 프레임을 0.2초마다 선택. RGB·LiDAR·GT·탑다운·증명 프레임과 시각 일치 검사. 누락 프레임 복제·시간 보간 없음. MP4는 시뮬레이션 시각 1배속, 5fps, 1964×1320.
- 윗줄: ego BEV / **ego가 실제 수신·검증한 배정 감시자 BEV** / 두 노드의 실제 합병. 아랫줄: 같은 프레임 ego RGB / 감시자 RGB / CARLA 탑다운. 카메라는 대상이 보이는 방향을 선택하고 이름 표시. 대상 이동은 같은 프레임 실제 위치로 표시.
- 원래 actor ID 고정, 대상 접촉·주행 구간 차도 이탈·미통과·입력 누락은 실패 처리. 실패 실행의 경과·접촉·오류 메타데이터 보존. 충돌 장면을 정상 통과 영상으로 잘라내지 않음.
- `capture.json.camera_content_audit`: actor별 6방향 원시 영상의 고유 이미지 수·동일 영상 최장 연속 수·첫/마지막 해시 기록. 전체 구간에서 6개 모두 같으면 취득 실패, 모델/ZKP 재생 전에 중단. 정면 하나만 같고 다른 방향이 변화하는 경우는 허용. RGB 패널에도 실제 frame ID·시뮬레이션 경과시각 표시.
- 결과: `bev_video_<UTC>/passby_2x3.mp4`, 시작/중간/끝 `preview_*.png`, `capture.json`, `summary.json`, 각 `round_*/`의 입력·증명·수신/합병 BEV·같은 프레임 RGB/GT. 단일 호스트 ego↔배정 감시자 1대만 교환하는 시각화이며 주변 전체 노드 평가와 구분.
- `ffmpeg` 필수. 시작 전 보고서 경로 최소 4GiB·서버 루트 최소 1GiB 확인, 9종 연속 생성에는 25GiB 이상 디스크 경로 권장. vvu 루트 공간 부족 시 충분한 디스크 저장소 사용. `/dev/shm`은 RAM을 사용하며 재부팅 시 소실됨. 이 경로에서는 각 실행 전 가용 메모리 24GiB 이상을 요구하는 보수적 검사 적용. 완성 자료를 별도 복사·해시 검증한 뒤 RAM 원본을 정리하고 다음 실행; 9종 원본을 RAM에 누적하지 않음. 센서 취득 종료 후 수신 rig도 해제. 공식 성능지표 합격은 이 영상으로 판정하지 않음.

사후 검사: `audit_bev_passby_video.py --report /영상/bev_video_<UTC> --checkpoint /기준모델.pt --output /새_검사.json`. 모든 표시 프레임·시각·actor·RGB 해시·차도 위치·MP4 재생 시계를 검사하고, 시작/중간/끝의 실제 Groth16 증명과 송수신 BEV·합병을 재검증합니다. 원래 수신기는 전 프레임에서 실제 증명 검증을 수행하며 사후 검사는 표본 재검증입니다. 종료 시 전체 anchor 거리 검사가 실패해도 이미 완성된 영상의 동기화·무접촉 검증과 구분; sweep의 `returncode`와 `video_complete`를 함께 확인해야 합니다.

2026-10-07 **과거 결과, 오류 5종 동기 검증 철회**: S1~S9 지정 anchor 각 1곳의 접근·통과·이탈 영상 599프레임·119.8초를 생성했습니다. 당시 actor/시각/RGB 파일/차도 대조·대상 접촉 검사, 실제 증명 표본 54개 재검증과 자동 테스트 172개는 수행했으나 카메라 내용 정지를 놓쳤습니다. 이 검사만으로 최신 센서 내용의 동기화를 보장하지 못했으며, 오류 5종은 위 재취득 영상으로 대체합니다.

- 저장: `_DAMOS/reports/bev_passby_videos_20261007/S1-S9_passby_2x3.mp4`, `S*_passby_2x3.mp4`, `S*_capture.json`, `S*_audit.json`, `video_index.json`. 전체 센서 입력·증명·RGB 원본과 실패 시도 기록은 작업용 별도 저장소에 보존, RAM 임시본은 복사·해시 확인 후 정리.
- 초기 S1/S3 영상은 native Traffic Manager, S4는 BehaviorAgent(cautious), S2/S5~S9는 LocalPlanner/PID. 수정 후 재실행은 S3 native Traffic Manager·나머지 LocalPlanner/PID. 영상별 실제 제어기는 `capture.json.drive`에 기록. 일반 Scenic 생성·오토파일럿 로직은 변경하지 않음.
- S1 종료 시 다른 감시자-이동 보행자 거리 27.95m로 기존 22m coverage 검사 실패(`returncode=1`). 완성 영상의 무접촉·동기화 검사 통과와 구분. S2/S4의 초기 미통과·접촉·bbox RPC 실패, S2의 메모리 부족 상태에서 발생한 `SNARKJS_TIMEOUT`도 보존. 운영 환경 전체 통과·전 anchor 검출·실시간 18초 합격으로 해석하지 않음.

2026-10-07 S5 완전 가림 시험: 같은 정지 장면 3라운드 F318/F341/F364, ego 존재 1/6·1/6·0/6 → 합병 5/6·5/6·4/6. 완전히 가려진 차량 3대는 매회 3/3 복원. 전체 1.574~1.649초, 평균 1.615초. 합병 지면 오탐률 31.7~33.1%(미확인 제외), 일부 차량 미검출 잔존. 단일 vvu·감시자 1대·loopback 조건, 공식 합격 아님. 결과: `_DAMOS/reports/live_bev_20261007T064102_535338Z`. 실제 Groth16 포함 회귀 시험 81개 통과.

이전 S4/S5 결과는 종류 진단용으로 보존. 같은 고정 예측을 별도 `presence_recheck`에서 재평가하면 존재 S4 1/1·S5 3/6이며, 기존에는 instance 증거가 없어 가림 복원으로 소급 판정하지 않음. 원본 예측·시간 변경 없음.

### 도로 오탐 보정 모델

일반 LiDAR의 넓게 관측된 지면 평면을 추정해, 평면에서 ±12cm 이내이고 수직 폭 8cm 이하인 평평한 셀의 장애물 확률을 낮춥니다. 보행자·자전거·이륜차로 예측된 입체 셀 주위 1셀은 보존. 미관측 셀을 도로로 채우지 않으며 GT·actor ID·OpenDRIVE로 지면을 지우지 않습니다. 모델 종류 분류 성능의 개선·완전한 주행 가능 영역 판정과 다릅니다.

vvu의 새 후보를 명시적으로 사용:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_ground_guard_20261008.pt \
  _DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2620 --n-scenarios 3 --runs 1 \
  --scenic-time 30 --realtime-factor 1.0 \
  --live-bev-fusion --bev-rounds 3 --evaluate-bev --no-trajectory-report
```

환경변수 생략 시 원본 모델 정책 유지. 이전 `scenic_presence_20261007.pt`를 지정하면 이전 LiDAR 보정까지만 적용. 새 후보의 `ground_guard_enabled`·`ground_guard_ms`·`ground_guard_changed_cells`는 각 노드 추론 기록에 저장되며 보정 처리도 추론 시간에 포함됩니다. 모델 파일은 Git 제외, 기존 기준 모델을 확보한 환경에서는 아래처럼 동일 가중치에 설정을 묶어 새 파일 생성 가능:

```bash
/home/vvu/vv/DAMOS/.venvs/bev/bin/python _DAMOS/scripts/pack_bev_ground_guard.py \
  --parent _DAMOS/models/yerin_bev/scenic_presence_20261007.pt \
  --profile _DAMOS/scripts/bev_ground_guard_20261008.json \
  --output /절대경로/새_ground_guard_candidate.pt
```

- 비교: 개발 5종에서 기준 포함 45개 설정 시험, 검출·미확인 조건 고정 후 별도 4종 확인. 초기 전체 도로 오탐 80% 감소 목표에는 미달; 검출 조건을 완화하지 않은 실용 보정 선택. 599프레임의 표시 범위 오탐 1.211%→0.204%, 전체 BEV 1.504%→0.348%. 구역 검출 564/585 유지, 미검출 21·GT 부족 14프레임 별도.
- 실제 CUDA 재추론 출력은 과거 저장 예측과 수치·일부 셀 차이가 존재하며 원인 미확정. 과거 출력과 비트 동일성을 가정한 초기 감사 2회는 실패 기록 보존. 최종 검사는 **동일 CUDA forward의 보정 전 출력**을 함께 저장해 보정 재생의 비트 동일성 및 실제 증명·전송·합병을 확인. 27라운드·증명 54개, 구역 검출 25/26 유지. 과거 출력과 완벽히 동일한 재현이라는 주장은 하지 않음.
- `DAMOS_BEV_AUDIT_GROUND_GUARD=1`은 위 감사용 미보정 확률을 노드 자체 NPZ에 추가. 이 배열은 BEV 전송 본문에 포함하지 않으며 모델 입력·합병에도 사용하지 않음. 운영 사용에는 불필요.
- 현재 표시는 **건물·수목을 포함한 모든 장애물 예측 주황색**, 회색 지면, 검정 미관측. 과거 파란 장애물 셀도 주황색으로 통일; 세부 분류·점유 데이터·ZKP·합병 결과 변경 없음. 흰 표시·4m 확대창은 GT 위치 안내이며 검출 성공을 뜻하지 않음. 비교 영상은 기존 동기 RGB를 유지한 **오프라인 보정 비교**. 새 실제 Groth16 검사는 별도 `bev_ground_guard_cuda_replay_20261008_v3/summary.json`에 기록.
- 세 BEV 패널의 **빨간 EGO 점은 기록된 차량 위치, 화살표는 차량의 전방 방향**. 감시자 패널도 ego 좌표계로 정렬되어 같은 차량을 표시. 기존 대상 영역을 잘라내지 않고 ego까지 포함하도록 표시 범위를 넓힐 수 있으며 헤더에 실제 범위 표시. 확대창은 ego 표식을 가리지 않는 모서리에 배치. 지도 표시만 수정하며 인지·성능지표 판정에는 미사용.
- 표시 수정 최신 합본: `_DAMOS/reports/bev_orange_ego_20261008/videos/S1-S9_orange_ego_2x3.mp4`, 개별 `S*_orange_ego_2x3.mp4`. 599프레임의 CARLA 실제 좌표·세 지도 표식 1,797개·감시자 원시 RGB·합본 디코딩 해시 대조 및 원본 불변 확인. 회귀 시험 199개 통과. 기존 검증 영상의 저장 BEV 표시만 수정한 것으로 새 추론·증명·성능지표 시험이 아님. `videos/video_index.json`에 프레임별 표식 좌표·원본/표시 범위·출처 기록.
- 보고서·개별/모음 영상: `_DAMOS/reports/bev_ground_guard_20261008/`. 기존 센서·예측·증명·영상 원본은 변경하지 않음. 단일 호스트·지정 anchor 각 1곳의 상관된 프레임 진단이며 정식 18초 합격·전체 anchor 검증과 구별.

### 시나리오별 차도 주행 검증

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/run_bev_driving_sweep.py \
  --scenarios S1 S2 S3 S4 S5 S6 S7 S8 S9 --rounds 5 --port 2663
```

- Town10HD OpenDRIVE의 Driving 차선에서 ego 한 대 생성, 기존 Scenic 오토파일럿 사용. 주행 중 좌표 강제 이동·고정·별도 tick 없음. `ClearNoon` 통제 조건, 감시자 반경 60m, 실제 모델·Groth16 공유 유지. S2/S4의 랜덤 생성 방식 변경 없음.
- 한 시나리오씩 재시작, 5라운드 관측. 전역 anchor를 전부 방문하도록 강제하지 않으므로 **관측 구역 / 전체 구역**을 반드시 함께 확인. 5라운드는 상관된 연속 관측이며 독립 반복 5회가 아님.
- `ego_driving_trace.json`: 평가 센서 설치 후부터 종료까지 프레임별 ego 위치·속도·차선. `summary.json`의 `ego_driving`: 이동거리·차도 이탈 프레임. 차량 중심점 검사이며 바퀴/차체 전체가 차선 안이라는 보증은 아님.
- 구역별 한 번 이상 검출과 구역×프레임별 검출을 분리. S1~S3의 보행자/자전거 존재 검출이 무단횡단 행동 분류 성공을 뜻하지 않음. 미관측·GT 반환 부족은 미평가, 모든 사각이 해소됐다고 해석하지 않음.
- 구역 GT 대조·차도 검사·그림 저장은 18초 계측 밖. 원본 관측 장애물 지도 생성까지는 계측 안.
- 결과: `_DAMOS/reports/bev_driving_sweep_<UTC>/summary.json`, 시나리오별 실행 로그, 각 `live_bev_<UTC>/`의 동일 프레임 입력·증명·평가 자료. 실패 실행도 삭제하지 않음.

저장된 결과만 사용해 비교 이미지와 전체 맵 주행 궤적 생성:

```bash
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/report_bev_driving_sweep.py \
  --sweep-summaries /절대경로/bev_driving_sweep_<UTC>/summary.json \
  --output /절대경로/새_보고서_폴더
```

`--sweep-summaries`는 여러 경로 지정 가능하나 중복 시나리오는 오류 처리. 비교 장면은 **첫 평가 가능 프레임·첫 anchor**로 고정, 가장 잘 나온 프레임 선택 금지. `S1_S9_presence.png`, 각 `Sx_presence.png`·동일 프레임 `Sx_rgb.png`·전체 맵 `Sx_map.png`, 프레임별 판정 포함 `summary.json` 생성. 재시뮬레이션·재추론·원본 보고서 변경 없음.

GT 준비 중 bbox RPC 시간초과가 발생한 S8/S9의 최초 실패 자료는 보존. 채점에는 bbox가 필요하지 않으므로 새 실행은 같은 snapshot의 actor 위치·실제 반환점 사용, bbox를 필수로 조회하지 않음. 시뮬레이션이 왜 해당 RPC에 응답하지 않았는지 세부 원인은 미확정. 모델·센서·ZKP·구역 검출 기준 변경 없음.

평가 프레임 0개인 실행 오류만 명시적인 `--retry-summaries /절대경로/재시도/summary.json`으로 병합 가능. `initial_failed_attempt`에 최초 실패 경로·오류 보존. 이미 검출 평가를 수행한 실패 결과를 성공 재시도로 덮어쓰는 기능은 없음.

BEV 모드는 Scenic의 `Sx#번호` setup 완료 출력을 실시간으로 수집하고, 그 뒤 안정된 ego·member ID에만 연결. 거절된 생성 시도의 actor에는 센서·감시자를 연결하지 않음. S8 군중을 삭제·재생성하던 과거 캡처 보조 경로는 BEV 모드에서 사용하지 않으며 Scenic 원래 군중을 유지. actor가 사라지면 다른 객체로 대체하지 않고 실패 기록.

2026-10-07 과거 통합 결과: `_DAMOS/reports/bev_driving_validation_20261007/summary.json`. 9종 각 5라운드, 전체 24구역 중 10구역 평가. S1 미검출, S3/S8 프레임별 누락, S8 F462는 단독 검출 성공→합병 실패. S6/S7/S9는 이미 발견한 구역의 사각 일부 복원이며 신규 구역 발견 추가는 0개. 지면 오탐률 21.9~38.8% 잔존. 전체 정상 검출·공식 합격 아님. 배달로봇 카메라 갱신 재검증 전 기록이므로 최신 협력 성능 증빙에서 제외. `S8_fusion_regression.png`는 최초 악화 프레임을 별도 표시하며 상세 표는 해당 보고서 참조.

### 입력·합병 개선 실험

**과거 개발 진단 재현.** 아래 경로는 `ego_observed`만 따로 비교하던 실험입니다. 현재 Scenic 기준 모델에는 LiDAR 보정과 이 합병 규칙이 함께 포함되며, 체크포인트를 명시한 실시간 실행에도 적용됩니다. ego 관측 영역은 유지하고 미관측 영역을 공유 정보로 보완하며, 무조건 장애물 OR을 수행하지 않습니다.

기존 검증된 BEV 결과의 합병만 재생(원본 수정 없음):

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/bev_fusion_diagnostic.py \
  --source-summary /절대경로/bev_driving_validation_날짜/summary.json \
  --output /절대경로/새_합병비교_폴더
```

S1의 FOV × 이력 간격 4조건 비교:

```bash
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2663 \
  --selected-scenario S1 --runs 1 --scenic-time 30 \
  --realtime-factor 1.0 --weather ClearNoon \
  --ego-start -74.49302673339844 -127.7459487915039 70.63380432128906 \
  --compare-bev-inputs --bev-rounds 5 --no-trajectory-report
```

- 같은 Scenic 실행의 ego·가장 가까운 감시자 1대에 현재/참조 RGB 각 6대와 공통 일반 LiDAR 부착. 나머지 anchor·감시자 위치/생성 방식 변경 없음. 부하 제한으로 비교 대상 감시자만 1대 선택하며 동적 이웃 통신 시험은 아님.
- 공통 0.05초 tick에서 7프레임을 받아 이력 0.1초/0.05초를 구성. 두 시야각은 같은 시점에 별도 카메라로 취득, 각 카메라의 실제 FOV에 맞는 intrinsic 사용. 끝 프레임·원래 actor ID·현재 LiDAR·끝 자세가 동일한지 검사.
- 현재 90도와 참조 후방 110도·나머지 70도를 비교. 부착 위치·모델 가중치 유지. 이번 공통 LiDAR는 20Hz이므로 기존 운영 10Hz 시험의 정확한 전후 비교는 아님.
- 입력을 모두 먼저 저장하고 같은 모델로 순차 추론. 모델 로딩 중 재취득·actor 교체 없음. 평가 전용 semantic LiDAR는 GT 채점만 수행, 추론·합병에 사용하지 않음. **ZKP 송수신·18초 전체 계측은 수행하지 않음.**
- `--live-bev-fusion`, `--evaluate-bev`, `--save-bev-inputs`, 기존 이미지 공유/고장 모드와 동시 사용 금지. 비교 라운드 1~10.
- 결과: `_DAMOS/reports/bev_input_comparison_<UTC>/summary.json`, `sensor_manifest.json`, 라운드별 입력·4조건 예측·같은 프레임 RGB·GT. `input_comparison.png`는 첫 평가 가능 프레임. 각 라운드 `ego_input_comparison.png`/`fused_input_comparison.png`는 모든 평가 가능 라운드. `first_fov_difference.png`는 별도로 표시한 최초 FOV 판정 차이이며 최고 결과 선별 아님.
- 움직이는 S1 보행자가 감시자에서 멀어지면 마지막 coverage 검사가 실패할 수 있음. 완료된 추론 결과와 전체 실행 통과를 구분. 실패 결과 삭제·덮어쓰기 금지.

2026-10-07 개발 결과: 5라운드 중 평가 가능 3프레임. 참조 FOV에서 ego 1/3→2/3이나 기존 합병은 모두 1/3, 지면 오탐도 증가. 시간 간격 변경만으로 추가 구역 검출 이득 없음. S1 종료 coverage 39.13m로 실패, 입력 비교 추론은 완료. 운영 기본값 유지. 상세 표는 해당 `bev_input_comparison_*` 보고서 참조.

저장 예측만으로 비교 그림 재생성(재추론 없음):

```bash
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/bev_input_comparison.py \
  --render-report /절대경로/bev_input_comparison_<UTC>
```

### 새 장면 수집·소규모 추가 학습

운영 입력(FOV 90도·4×0.1초·일반 LiDAR 10Hz) 유지. 아래는 기존 BEV CUDA 환경이 준비된 vvu용 개발 명령이며, 공식 평가·운영 가중치 자동 교체가 아닙니다. 데이터·출력 폴더는 새 경로 사용. 디스크 여유 공간 최소 4GiB 확인.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/run_bev_adaptation_collection.py \
  --output /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/datasets/새_데이터셋 \
  --port 2663 --rounds 8

/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/train_bev_adaptation.py \
  --dataset /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/datasets/새_데이터셋 \
  --output /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/reports/새_학습결과 \
  --epochs 8 --lr 0.0001
```

- 사전 지정한 학습 6장면(S1/S8 각 2, S4/S5 각 1), 검증 2장면(S1/S8), 시험 2장면(S1/S8). 각 장면 ego·가장 가까운 감시자, 8라운드: 총 160개 입력. seed·ego 접근 거리·일부 날씨 구분. 같은 Town10HD·고정 S8 구역 중복이 있어 미지 지도·위치 일반화 검증은 아님.
- 장면별 `manifest.json`: 원래 actor ID·frame·파일/입력 내용 해시·센서 설정·실제 GT 반환 셀 수. 모델 입력과 `*_supervision.npz` 분리. semantic GT는 손실·채점에만 사용, `model.forward`에는 일반 센서 입력만 전달. bbox·보간·GT 강제 예측 없음.
- 짧은 원시 입력 시퀀스를 먼저 고정한 뒤 압축·저장. ego는 Scenic 오토파일럿으로 차도 주행. `collection_status.json`에 취득 완료와 wrapper 통과를 별도 기록. S1 종료 감시거리 검사가 실패해도 이미 취득한 데이터·실패 로그 보존; 전체 시뮬레이션 통과로 표현하지 않음.
- 중단한 동일 계획 수집은 `--resume`으로 이어갈 수 있음. 장면 seed 중복·split 간 동일 입력·불완전 manifest를 학습 전 차단. 이미 있는 미완성 장면을 삭제하거나 성공 장면으로 자동 덮어쓰지 않음.
- 기존 체크포인트·전체 구조 유지, 마지막 `decoder_v2` 34,081개 파라미터만 학습. 동적 routing 때문에 매번 전체 forward 재계산. 학습 FP32·기울기 누락/비정상 값 검사, 검증·시험은 운영 FP16. 원래 step 100,000 유지, 나머지 가중치 불변 검사.
- 검증 전체·시나리오별 구역 검출/지면 오탐/미확인 비율 악화 금지, 전체 장애물 F1 개선 조건으로 `candidate.pt` 선정. 자격 없는 경우 시험 미실행. 선정 후 별도 시험은 한 번만 수행, 시험 결과로 다른 epoch 재선택 금지.
- `training_history.json`, `baseline_val.json`, `val_epoch_*.json`, `summary.json` 기록. 시험을 수행하면 `heldout_original/`, `heldout_adapted/`, `S1_heldout.png`·`S8_heldout.png` 생성. 기본 그림은 첫 평가 가능 프레임, 최고 결과 선별 없음. 악화가 있으면 `Sx_first_regression.png`에 최초 악화 프레임도 별도 표시. 원본 체크포인트·데이터 해시 재검사.
- 이 경로는 ZKP·18초 전체 계측 없음. 구역 존재 판정·희소 semantic 반환 셀 기준이며 행동 분류·모든 사각 복원 정확도·공식 성능 합격과 구별. 데이터·가중치·보고서는 Git 제외.

선정된 고정 후보의 기존 S1~S9 입력 회귀 검사:

```bash
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/audit_bev_adaptation.py \
  --source-summary /절대경로/bev_driving_validation_날짜/summary.json \
  --checkpoint /절대경로/학습결과/candidate.pt \
  --output /절대경로/새_회귀검사_폴더
```

기존 프레임·GT·증명·예측 파일 수정 없음. 같은 원시 입력을 후보로 재추론하고 동일한 확률 가중 합병·채점 적용. 기존 ego·검증된 peer의 자세/관측 마스크를 대조·재사용해 통신 고정소수 반올림 조건까지 유지; GT 자세로 대체하지 않음. 시나리오별 검출/오탐/미확인 및 기존 사각 부분 검출 유지 검사. 이전 개발 장면의 회귀 확인이며 독립 시험·새 증명·새 처리시간 결과가 아님.

후보를 명시적으로 선택하는 실시간 시험은 `DAMOS_BEV_CHECKPOINT=/절대경로/candidate.pt` 환경변수 사용. 생략하면 원본 체크포인트 유지. 실제 로딩 파일의 SHA-256을 BEV 패킷·증명에 결합하므로 후보 결과를 원본 모델 결과로 기록하지 않음.

첫 학습 후보의 과거 결과는 **검출·사각 복원 퇴행으로 채택 보류**. 이후 LiDAR 보정을 포함한 Scenic 기준 모델과 다른 구성입니다. 별도 S1/S8 시험 F1 0.398→0.710·지면 오탐 33.35%→7.30%이나 S1 구역×프레임 검출 6/8→3/8 악화(S8 4/8→8/8). 기존 S1~S9 재생에서도 S3 5/9→4/9·기존 사각 복원 4회 중 2회 소실. 보고서: `bev_adaptation_20261007/`, `bev_adaptation_regression_20261007_v2/`(초기 v1도 보존).

첫 학습 종료 검사에서 curriculum 상태 `z_collapse._step`의 정상 forward 동기화(99,999→100,000)를 오판한 오류 기록 보존. 검사는 해당 값만 정확한 복원 step으로 비교하도록 수정, 다른 가중치 불변 검사 유지. 이 실행은 이미 완료한 8 epoch·검증 선정 epoch 7을 그대로 사용해 `--finalize-only`로 시험 전 완료 단계만 재개. 해당 옵션은 전체 epoch 기록·후보/원본 해시·선정 기준·불변성 검사를 요구하며, 시험 출력이 이미 존재하면 재실행 거부. 시험 결과를 보고 재학습하거나 다른 epoch로 교체하는 옵션이 아님. 다음 후보 개발에서는 이 계획의 시험 seed를 재사용하지 않고 새 미사용 시험 장면 필요.

### 복수 개선 후보 비교

기존 첫 후보의 검출 퇴행을 보완한 **과거 실험의 재현 경로**. 이 스크립트의 전체 장면 gate는 당시 조건 보존용이며 현재 Scenic 검증용 모델 채택 조건과 구분합니다. 아래 단계는 기존 `bev_presence_adaptation_20261007` 학습 자료·epoch 7 후보·주행 보고서를 사용하는 vvu 전용 예시입니다. 해당 자료는 Git에 포함되지 않습니다. 새 실험에서는 출력 경로와 `--plan-file`의 seed를 새로 지정하고, 이미 확인한 시험 seed 재사용 금지.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

# 1. 사전 지정한 추가 학습 6 / 검증 9 / 새 시험 9장면 취득
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/run_bev_adaptation_collection.py \
  --output /절대경로/새_추가데이터 \
  --plan-file _DAMOS/scripts/bev_improvement_plan_20261007.json \
  --port 2663 --rounds 6

# 2. foreground 보존 판정부 / 판정부+LiDAR encoder 후보 학습
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/train_bev_presence_candidates.py \
  --old-dataset _DAMOS/datasets/bev_presence_adaptation_20261007 \
  --new-dataset /절대경로/새_추가데이터 \
  --initial-checkpoint _DAMOS/reports/bev_adaptation_20261007/candidate.pt \
  --output /절대경로/새_후보학습

# 3. 모델 / 일반 LiDAR 보정 / 합병 조합 선정 후 고정 후보만 새 시험
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/run_bev_improvement_experiment.py \
  --old-dataset _DAMOS/datasets/bev_presence_adaptation_20261007 \
  --new-dataset /절대경로/새_추가데이터 \
  --training-report /절대경로/새_후보학습 \
  --previous-candidate _DAMOS/reports/bev_adaptation_20261007/candidate.pt \
  --driving-summary _DAMOS/reports/bev_driving_validation_20261007/summary.json \
  --output /절대경로/새_조합비교
```

- 추가 의존성: `scikit-learn==1.6.1`와 SciPy. 기존 BEV 환경에서 사용; 다른 conda 환경 변경 불필요.
- 추가 데이터 288개 입력 중 학습 72·검증 108·새 시험 108개. 기존 학습 96개를 합친 168개만 손실·LiDAR 보정 학습에 사용. 기존 검증·이미 열람한 과거 시험·주행 자료는 모두 개발/회귀 자료로 명시, 독립 최종 시험으로 재주장하지 않음.
- 기존 전체 모델을 FP32 forward하면서 마지막 판정부 또는 판정부+LiDAR encoder만 학습. 장애물 그룹 BCE·작은 대상 가중·Dice·학습 정답 장애물에서 원본 검출 보존 손실 비교. 동결 가중치 불변·기울기 검사 수행.
- LiDAR 보정 입력은 실제 반환점의 높이·수직 폭·밀도·거리와 모델 확률. 지면 평면을 일반 LiDAR로 추정하고 학습 split에서 이진 보정 계수 결정. **추론/합병에 GT·actor bbox·보간 결과 입력 없음.** 관측 마스크 불변, 미관측 영역 생성 없음. 세부 사람/차량 분류 개선과는 구분.
- 기본 가중 평균, ego 관측 유지, 장애물/지면 확신 가중 합병 비교. 단순 장애물 OR 아님. 변경된 계수·합병 규칙까지 `selected_candidate.pt`에 묶고 전체 체크포인트 SHA를 ZKP 패킷에 결합.
- 개발 전체/시나리오/자료군의 검출·오탐·미확인 비악화 및 기존 사각 부분 검출 유지 검사. 모든 후보 실패 시 진단용 1개만 미리 고정하여 새 시험 수행; 이 경우 성적이 좋아도 자동 채택하지 않음.
- `selection.json` 작성 후에만 seed 9101~9109 새 시험을 읽음. 시험을 보고 후보 재선택 금지. `--resume`은 선정/시험 시작 이전 캐시 재사용만 지원, 선정 파일 존재 시 거부.
- `variants.json`, `selection.json`, `summary.json`, `Sx_confirmation.png` 저장. 같은 프레임 원본/후보 비교, 첫 평가 가능 프레임 사용. 9종 모두 평가 가능한 경우 3×3 통합 이미지 생성. 같은 Town10HD·고정 anchor 위치 중복·상관된 연속 프레임이라는 한계 존재.
- 오프라인 조합 비교는 ZKP·18초 전체 계측 없음. 실시간 시험은 `DAMOS_BEV_CHECKPOINT=/절대경로/새_조합비교/selected_candidate.pt`를 명시. 생략하면 원본 유지. 보정 CPU 처리도 실시간 추론 시간에 포함.

2026-10-07 과거 실험: 전체 장면 gate 통과 0/50. 사전 고정 후보는 이전 epoch 7 + LiDAR 보정(foreground 12, 강도 1) + `ego_observed`. 개발 Scenic 검출 91/118→114/118, 새 시험 46/54→53/54, 기존 사각 부분 복원 4개 프레임 유지. 당시 유일한 탈락 사유였던 S6 지면 오탐은 현재 검증 범위 밖이며, 해당 S6 이상상황 구역 검출은 5/5 유지했습니다. **시험 이후 사용자 확정 범위에 따라 같은 후보를 Scenic 검증용 기준 모델로 사용**. 과거 gate·선정 파일·시험 결과는 수정하지 않음. 새 시험 S3 미검출 1건, S2 미평가 남아 있으며 전체 성공으로 집계하지 않습니다.

아래는 고정 결과를 다시 그리기만 하는 명령. 모델 추론·후보 재선택·원본 보고서 수정 없음. S2처럼 평가 불가능한 시나리오도 미평가 표시로 포함하며 최초 S6 개발 오탐 악화 그림 추가.

```bash
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/report_bev_improvement.py \
  --report _DAMOS/reports/bev_improvement_experiment_20261007 \
  --output /절대경로/새_비교그림
```

Scenic 기준 모델로 S1~S9 각각 실행·검출·실시간 ZKP 연결 확인:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
env DAMOS_BEV_CHECKPOINT=/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/models/yerin_bev/scenic_presence_20261007.pt \
  /home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/run_bev_driving_sweep.py \
  --scenarios S1 S2 S3 S4 S5 S6 S7 S8 S9 --rounds 3 --port 2663
```

같은 호스트·loopback 진단. 이웃 없음·미평가·시간초과를 성공 횟수에 포함하지 않음. `DAMOS_BEV_CHECKPOINT` 미지정 시 호환성용 원본 모델 유지, 지정 시 기준 모델의 보정·합병까지 적용. 실제 다중 PC 네트워크·공식 18초 합격과 구별.

실시간 시험 **종료 후**, 저장된 후보 해시·송수신 BEV/관측 마스크 동일성·고정소수 자세·동일 합병 재생·실제 Groth16 증명을 다시 검사:

```bash
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  _DAMOS/scripts/audit_bev_candidate_live.py \
  --sweep /절대경로/bev_driving_sweep_날짜/summary.json \
  --checkpoint _DAMOS/reports/bev_improvement_experiment_20261007/selected_candidate.pt \
  --output /절대경로/새_실시간검사.json
```

원본 파일 수정·모델 재추론 없음. 이 검사는 저장 증명의 과거 유효성 재확인이며, 이미 지나간 패킷의 실시간 유효기간이나 네트워크 전송을 새로 시험하는 것이 아님. 실시간 freshness는 원래 수신기 검증 결과와 구분.

2026-10-07 고정 후보 실시간 결과: S1~S9 각 3라운드 **27/27 완료**, 평균 1.846초·최대 2.433초. 60개 실제 증명 재검증·66방향 송수신 본문·동일 합병 재생 일치, 원본 파일 해시 불변. 관측된 구역×프레임 30/30 검출, 전체 24구역 중 실제 관측은 10구역. S1/S2는 ego 단독 미검출 구역을 공유 후 발견, S2/S6/S9는 검증된 가림 부분 복원. S2 새 고정 입력 시험의 미평가와 구분. S1 ego는 교통상 거의 정지, 나머지는 이동 기록. 단일 호스트/공유 GPU/loopback의 소수 연속 라운드이며 모든 anchor 성공·공식 합격 아님. 실제 보고서: `bev_driving_sweep_20261007T102516/`, 재검사 `bev_improvement_experiment_20261007/live_candidate_audit.json`. 서버 여유 약 4GiB, 다음 데이터 수집 전 공간 확인 필요.

### 원본 데이터 진단 재현

`prepare_bev_original_subset.py`는 NAS 원본을 읽기만 하고 주간·야간 차량 두 agent의 마지막 10% 구간에서 6개씩 미리 선정합니다. RGB는 원본 준비 코드와 동일하게 0.5배·JPEG 품질 92, intrinsic 0.5배, sample token 수정 적용. GT는 원본 `carla_gt_core.py`의 semantic LiDAR·박스 결합으로 재생성. 결과 파일만 지정된 출력 폴더에 기록하며 NAS 원본 수정 없음.

평가 전용 의존성은 `nuscenes-devkit==1.2.0`·`pyquaternion`·NumPy·Pillow·OpenCV이며, 학습·추론 모델은 기존 BEV 환경 사용. NAS가 마운트된 컴퓨터에서 아래 실행. 기본 GT 코드는 저장소 vendor에서 읽으며, 인계 코드만 따로 있는 경우 `--code /path/to/carla_bev_handover/code` 추가. NAS 접속 비밀번호를 코드·문서·Git에 기재하지 않습니다.

```bash
python /path/to/STL_DAMOS/Carla-0.9.16-source/_DAMOS/scripts/prepare_bev_original_subset.py \
  --source /path/to/mounted/Storage/Data/Carla/Columbus/Dataset/2026 \
  --output /path/to/original_subset --per-agent 6
```

생성된 `original_subset` 폴더를 vvu로 전송한 후, 해당 경로를 `--dataset`에 지정합니다. 이 시험에서 전송한 데이터는 약 92MB이며 전체 데이터셋 복사 아님. vvu에서의 재실행:

```bash
/home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/scripts/bev_original_diagnostic.py \
  --dataset /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/reports/bev_original_diagnostic_20261007/subset \
  --output /home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/reports/bev_original_diagnostic_20261007/results
```

- CARLA 실행·통신 없이 같은 원본 입력으로 원본 `viz_carla.build_model` 및 현재 `BEVPredictor`를 차례로 실행. 18초 통신·합병 계측 시험 아님.
- 원본 데이터 로더의 `__getitem__`은 그대로 사용하되, 미리 선정한 전체 시계열 검증 구간 인덱스를 명시적으로 지정. 다운로드한 작은 표본을 다시 90:10으로 나누지 않음.
- 원본 추론은 `strict=False`, TC 미사용 52개 키, step 0. 현재는 519개 키 strict 로딩·step 100,000. 설정 차이·해시를 JSON에 기록하며 운영 설정 변경 없음.
- GT는 원본 ground densify·`CARLA_DILATE=1` 적용. GT를 모델에 전달하지 않음. 원본 캐시가 없으므로 2,123프레임 전체 성적 재현·정식 성능 평가라고 주장하지 않음.
- `results/diagnostic.json`: 클래스별 dense GT IoU, 현재 LiDAR 반환 셀만의 IoU, 전처리 오차, 체크포인트·원본 코드 해시.
- `results/sample_00~11/original_vs_runtime.png`: 같은 입력 카메라와 GT / 원본 예측 / 현재 예측. 각 폴더에 재현용 `original_model_inputs.npz` 저장.
- 원본에서 계산한 dense GT IoU와 live 객체별 반환 셀 일치율은 평가 영역·정의가 다르므로 수치 직접 비교 금지.

2026-10-07 결과: 원본/현재 추론의 object IoU 0.079/0.080, car 0.243/0.253, pedestrian 0.107/0.109. 원본에서도 오분류 확인. RGB·LiDAR 전처리 대조 오차 0. 확인한 원본 차량 카메라는 후방 110도·나머지 70도, 시점 간격 0.05초인 반면 현재 90도·0.1초. 운영 설정은 유지하며 센서 조건 대조 시험을 다음 단계로 남김.

## BEV 모델용 센서 부착·입력 저장

ego와 모든 custom observer에 **RGB 6개 + 일반 ray-cast LiDAR 1개**를 각각 부착합니다. actor 재생성 없음. 카메라 위치·방향은 `sensor_config.txt`의 기존 장착값 유지. RGB 800×450, 수평 FOV 90도, LiDAR 64채널·80m·초당 130만 점 설정. LiDAR 회전 주파수는 고정 tick 간격의 역수(현재 0.1초 tick이면 10Hz)로 맞춰 한 tick에 360도 수집합니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --offscreen --port 2660 \
  --selected-scenario S4 --runs 1 \
  --scenic-time 8 --realtime-factor 1.0 \
  --save-bev-inputs --no-trajectory-report
```

- `--attach-bev-sensors`: 센서 부착 및 제한된 수신 버퍼만 활성화.
- `--save-bev-inputs`: 위 옵션 포함, 모든 센서·모빌리티에 공통인 연속 4개 CARLA frame ID를 선택해 입력 파일 저장.
- `--headless` 불가. SSH에서는 `--offscreen` 사용. 기존 정면 공유·고장·캡처 옵션과 동시 실행 차단, 기존 명령 동작 유지.
- 시뮬레이션 tick은 Scenic만 진행. 입력 취득 대기 중에도 ego autopilot 동작. 4프레임 저장은 센서 취득 검증이며 모델 추론·전체 처리시간 평가 아님.

저장 위치: `_DAMOS/reports/bev_sensor_inputs_<UTC 시각>/`.

| 파일 | 내용 |
|---|---|
| `manifest.json` | 시나리오, actor/sensor ID, 장착 행렬·설정, 공통 프레임·시각, 입력 크기·점 수 |
| `<label>_model_inputs.npz` | `imgs[1,4,6,3,128,320]`, `lidar_bev[1,6,200,200]`, 보정 행렬, `ego2globals[1,4,4,4]`, 프레임·시각 |
| `<label>_<camera>_f<frame>.png` | 모빌리티당 24장 원본 RGB |
| `<label>_lidar_f<frame>.npy` | 현재 일반 LiDAR 원본 `[M,4]`: x,y,z,intensity |
| `<label>_six_cameras.png`, `all_mobility_cameras.png` | 동일 현재 프레임의 카메라 6방향 비교 |

카메라 순서: 전방 좌 / 전방 / 전방 우 / 후방 좌 / 후방 / 후방 우. 이미지 전처리는 제공된 평가 코드의 resize·crop·정규화와 동일. LiDAR는 6채널 격자(존재·점수·최대/최소 높이·높이차·최대 반사강도)로 변환하며, CARLA `[M,4]`를 nuScenes `[M,5]`로 오해하지 않습니다. 모델 좌표는 x 전방·y 좌측·z 위, 카메라는 optical 축으로 변환. 실제 시점별 센서 transform으로 모빌리티 자세·외부 파라미터 대조. 누락 센서·빈 점군·잘못된 좌표를 조용히 0으로 대체하지 않습니다.

현재 버퍼는 0.1초 간격 연속 4 tick. **학습 데이터의 시간 간격·FOV·좌표 규약 최종 대조와 모델 정확도 검증은 추론 연결 단계에서 별도 수행**합니다. GT·semantic LiDAR는 모델 추론 입력으로 쓰지 않습니다.

2026-10-05 S4 실기 검증: ego 1대·휴머노이드 2대·배달로봇 2대, 35개 센서, 공통 F282~F285. 모빌리티마다 RGB 24장 및 LiDAR 약 6.5만~6.9만 점 수집. observer coverage 4/4, 오류 0. `inference_executed=false`, `zkp_bev_sharing_executed=false`.

## 실시간 정면 이미지 공유와 ZKP

### 처음 한 번 준비

기존 CARLA/Python 환경 외에 **Node.js 18 이상과 npm**이 필요합니다. vvu 검증 버전은 Node.js 22.22.0입니다. 새 장비는 관리 정책에 맞게 Node.js를 설치한 후 버전을 확인합니다. ZKP assets·원본 Python 모듈·package-lock은 `_DAMOS/vendor/damos_zkp`에 포함됩니다. 증명 키를 재생성하거나 원본을 수정할 필요 없습니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

# vvu: 기본 /usr/bin/node는 구버전이므로 준비된 NVM 경로 사용
export PATH=/home/vvu/.nvm/versions/node/v22.22.0/bin:$PATH
node --version
npm --version

cd _DAMOS/vendor/damos_zkp
npm ci --ignore-scripts --no-audit --no-fund
/home/vvu/anaconda3/envs/carla4/bin/python check_assets.py
```

새 장비에서는 경로를 실제 설치 위치로 바꿉니다. Python은 CARLA 실행 환경의 `numpy`, `opencv-python`을 사용합니다. Node 경로를 직접 지정해야 한다면 `export V2V_NODE_CMD=/실제/경로/node`. 기본값은 설치된 NVM 최신 버전부터 탐색합니다. 라이브 모드에는 mock 대체 경로가 없습니다.

### 실행

SSH에서도 아래 명령 하나로 실행·저장 가능합니다. 별도 카메라 창 명령 불필요.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh \
  --restart --offscreen --port 2620 \
  --n-scenarios 3 --runs 1 --scenic-time 30 --realtime-factor 1.0 \
  --live-ego-sharing --sharing-rounds 0 --sharing-duration-seconds 90 \
  --sharing-radius-m 60 --save-sharing-video \
  --no-trajectory-report
```

| 옵션 | 의미 |
|---|---|
| `--live-ego-sharing` | 기존 부착 위치의 정면 카메라만 사용; 독립 노드·실제 TCP·Groth16 활성화 |
| `--sharing-rounds 5` | 공유 시도 5회가 상한; 대기는 횟수에서 제외. `--runs`는 전체 시나리오 재실행 횟수 |
| `--sharing-rounds 0` | 횟수 상한 해제. 양수 `--sharing-duration-seconds` 필수 |
| `--sharing-duration-seconds 90` | 실제 경과시간 90초. 횟수 상한도 있으면 먼저 도달한 조건으로 종료 |
| `--sharing-radius-m 60` | ego 카메라에서 60m 이내 감시자만 선택. `0`이면 ego ↔ 모든 감시자. 감시자끼리 통신은 항상 제외 |
| `--sharing-timeout-seconds 18` | 라운드 제한; 이웃 미수신·증명 실패·시간초과를 성공 처리하지 않음 |
| `--save-sharing-video` | ego 정면 + ego가 실제 수신·검증한 감시자별 화면 + 전체 지도·주행 궤적·검증 기록 MP4 |

Scenic 수명은 공유 세션을 유지할 수 있도록 연장합니다. 시간 상한은 라운드 사이에서 확인하므로 진행 중인 교환의 종료·시간초과 처리만큼 초과할 수 있습니다. 명시적 시간 제한을 생략하면 `120 + rounds × (3 × timeout + 10)`초의 안전 상한 적용. CARLA를 멈추거나 증명 처리 속도에 맞춰 tick을 직접 진행하지 않습니다. `--headless`의 null renderer는 사용할 수 없습니다. 별도 센서 캡처·fault·S1 검증 옵션과의 동시 사용도 차단합니다. 일반 캡처·고장 보고서 실행 방법은 유지됩니다.

**ego와 선택된 감시자만** 같은 CARLA frame ID의 한 장씩 사용합니다. 가장 최신의 공통 프레임만 꺼내며, 처리 중 발생한 이전 프레임은 건너뜁니다. 이는 과거 6프레임 모델 입력 버퍼가 아닙니다. 먼 감시자의 프레임과 worker 결과는 기다리지 않습니다. 먼 감시자는 카메라 listen을 멈추고 이미지를 인코딩하거나 증명하지 않으며 worker 대기 상태를 유지합니다. 처음 등록할 때의 키 준비는 전체 노드에 대해 한 번 수행합니다.

등록 대상 actor·sensor ID는 시작 시 고정, 공유 대상은 ego 이동에 따라 갱신합니다. 감시자는 기존 actor 그대로 유지하며 재생성하지 않습니다. 1~31개 observer 지원. 이웃 0대는 `idle`이며 ego 주행을 계속합니다. 대기만 하다 시간이 끝나도 정상 종료하지만 공유 성공·성능 합격으로 세지 않습니다. 실제 선택된 이웃의 패킷이 오지 않는 `timeout`과 구분합니다.

현재 대상 탐색은 CARLA ground-truth 좌표를 사용하며 실제 무선 discovery/beacon 구현은 아닙니다. 카메라 취득 직전과 동일 프레임 취득 후에 거리를 확인하고, 확정된 명단은 한 교환 동안 유지합니다. 지도에 표시한 현재 위치와 마지막 교환의 위치·프레임은 시간이 다를 수 있습니다.

### 저장 결과와 계측 해석

```text
/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/reports/live_sharing_<UTC 시각>/
  summary.json             # 대상/범위/노드/성공·실패·ego 평균·최대/버퍼 통계
  ego_rounds.jsonl          # ego 라운드별 단계 시간, frame ID, 검증한 이웃
  peer_debug.jsonl          # 타 노드 진단용; 타 모빌리티 공식 성능 평가 아님
  membership.jsonl          # 접근·이탈·대기 전환, 시각과 선택된 대상
  ego_neighbour_sharing.mp4 # --save-sharing-video 선택 시
  video_telemetry.jsonl     # 시각/pose/frame/대상/완료 라운드/표시된 수신 이미지 해시/영상 프레임 범위
  round_001/
    ego.png                # ego가 보낸 자기 정면 영상
    observer_<actor_id>.png # ego가 실제 수신하고 검증 완료한 이미지
```

터미널에 대상 진입·이탈, 라운드, frame ID, 검증한 이웃 수와 공유 시간을 출력합니다. 정상 상태도 `sharing_complete_detection_pending`이며 탐지 시간·결과는 null입니다. `reciprocal_verified_peer_ids`는 ego 이미지를 검증한 감시자 목록으로, 양방향 검증 여부를 추가 확인합니다. `transport_budget_pass=true`는 **ego 공유 부분만** 18초 이내라는 뜻입니다. `metric2_pass`는 계속 null입니다. 실패·시간초과는 JSON에 기록하고 wrapper도 실패 종료하지만, 이웃 없음은 정상 대기로 처리합니다. PNG 저장 오류는 `artifact_error`로 별도 기록됩니다.

영상은 ffmpeg가 필요합니다(`command -v ffmpeg` 확인; Ubuntu에서는 관리 정책에 맞게 `sudo apt install ffmpeg`). 실제 세계시간 1배속, 5fps 기록이며 원본 카메라보다 새 프레임이 늦게 오면 마지막 실제 프레임을 유지합니다. 파란 점·선은 ego·실제 주행 궤적, 노란 연결선은 선택된 대상, 초록 연결선은 마지막 양방향 검증 완료 대상, 회색은 비참여 감시자입니다. 영상은 별도 스레드로 기록하지만 CPU·렌더 부하는 존재하므로 영상 포함 결과와 영상 없이 잰 값을 동일 조건으로 취급하면 안 됩니다.

수신 화면은 감시자 카메라를 직접 미리보기한 것이 아니라 **ego worker가 실제 수신·Groth16 검증 후 저장한 PNG**입니다. 완료 라운드의 actor ID·frame ID·SHA-256과 대조하여 표시합니다. 감시자별 O 번호·actor ID·R(라운드)·F(원본 프레임)를 표시하며, 새로 진입한 감시자는 검증 완료까지 대기, 이탈한 감시자는 화면에서 제외합니다. 오류 파일을 다른 객체·과거 라운드 이미지로 대체하지 않습니다. 한 프레임 공유이므로 수신 화면은 다음 교환까지 유지되며 ego 실시간 화면과 프레임 번호가 다를 수 있습니다. 현재 출력은 1920×1080, 수신 2대까지 크게 나란히 표시하고 더 많으면 모든 대상을 격자 표시합니다.

프레임 번호 예: ego 미리보기 F684 / 감시자 수신 화면 F669라면, 해당 공유 라운드의 ego 입력도 F669이며 왼쪽 미리보기만 최신 프레임으로 갱신된 것입니다. 2026-09-16 시험의 83번 라운드는 공유 1.241초 중 패킷·증명 생성 0.479초, 감시자 2대 검증 0.736초였습니다. `ego sharing`에는 센서 취득 대기·이미지 저장·화면 갱신이 제외되므로 촬영부터 표시까지의 전체 지연으로 해석하지 않습니다. 프레임 차이 전체를 ZKP 시간으로 환산하지 않습니다.

### 통신·보안 범위

2026-10-05 적용 범위 확인: **원본 ZKP 기법을 이미지 공유에 적용**한 구조입니다.

| 부분 | 현재 사용 |
|---|---|
| 원본 Groth16 엔진·회로·WASM·증명키·검증키 | 그대로 사용, 실제 snarkjs 증명·검증 실행, 라이브 mock 대체 없음 |
| 원본 융합 객체 목록·Merkle 패킷 SDK | 현재 이미지 공유 경로에서는 미사용 |
| 이미지 공유용 연결부 | 이미지 SHA-256·actor/frame/session/round ID·수신자 정보를 증명에 결합하고 직접 TCP 교환 |

- ego와 선택된 휴머노이드·배달로봇이 같은 코드로 자기 증명 생성·상대 이미지 검증을 수행합니다. ego 증명은 선택된 감시자 명단에 묶고, 감시자의 수신자는 ego 하나뿐입니다. 감시자끼리 이미지·증명을 전송하지 않습니다.
- 메시지에 session/round/frame/actor ID, sender/recipients, 시각, 크기, 이미지 SHA-256을 넣습니다. 이미지 해시는 실제 받은 바이트로 재계산하고, manifest와 수신자 목록은 Groth16 public input에 결합합니다. 재전송 공격·다른 프레임·잘못된 수신자·변조·만료 패킷 거부.
- **이미지 내용이 실제 현실이라는 증명, 암호화, 위치 진실성 증명은 아님.** 등록한 비밀값의 소유와 해당 메시지 결합을 검증합니다.
- 최초 등록은 신뢰된 로컬 coordinator가 임시 commitment를 각 노드에 전달합니다. 비밀값은 노드 메모리에만 존재합니다. 이미지 교환은 coordinator를 경유하지 않는 직접 TCP입니다. 센서 취득·프레임 선택은 CARLA adapter가 공통 담당합니다.
- 현재는 **단일 vvu의 독립 프로세스·127.0.0.1 TCP**입니다. 다중 PC 실행, 서비스 탐색, 영구 인증 등록, TLS, 실제 무선 지연·손실 환경은 아직 구현·검증하지 않았습니다. 외부 네트워크에 이 프로토타입을 그대로 노출하면 안 됩니다.
- 제공된 proving assets의 신뢰 설정 한계는 `vendor/damos_zkp/docs/ASSET_HANDOFF.md` 참조. 원본 assets와 회로는 변경하지 않았습니다.

### 개발 검증

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

DAMOS_TEST_REAL_ZKP=1 /home/vvu/anaconda3/envs/carla4/bin/python \
  -m unittest discover -s _DAMOS/tests -p 'test_*sharing*.py' -v

# 합성 입력 통신 시험. CARLA 성능 증빙으로 사용하지 않음.
/home/vvu/anaconda3/envs/carla4/bin/python _DAMOS/scripts/check_live_sharing.py \
  --nodes 3 --rounds 3 --output "_DAMOS/reports/sharing_smoke_$(date +%Y%m%d_%H%M%S)"
```

기존 이미지 모드: `_DAMOS/scripts/m2x/live.py`·`mesh.py`·`security.py`·`transport.py`, 빈 `pending_detector` 유지. 실제 BEV 모델 경로: 별도 `bev_model_runtime.py`·`bev_mesh.py`·`bev_protocol.py`·`bev_fusion.py`·`bev_live.py`. 이미지 공유 부분 시간과 BEV 전체 처리시간을 구분합니다.

### Ego 중심 통신·영상 검증 (2026-09-16)

S9, 감시자 5대, 반경 60m, Scenic ego autopilot, ClearNoon, 영상 기록 활성화 조건으로 확인했습니다. ego를 도중에 이동시키거나 재생성하지 않았습니다.

| 확인 항목 | 결과 |
|---|---|
| 영상 | 90.6초, 1440×900 H.264, 5fps, 실제 세계시간 1배속 |
| ego 실제 이동 | 약 221.5m, 동일 actor ID 유지 |
| 대상 변경 | 0대 → O1 → O1·O3 → O3 → O3·O5 |
| 대기 | 시작 후 약 14.4초, 실패·공유 성공으로 계산하지 않음 |
| 공유 | 92/92회 양방향 검증 성공; 1대 공유 77회, 2대 공유 15회 |
| ego 부분 처리시간 | 평균 0.814초 / 최대 1.328초, 탐지 미포함·영상 부하 포함 |
| 거리 검사 | 모든 라운드에서 선택 감시자 거리 ≤ 60m |
| 감시자끼리 통신 | peer_debug 전체 검사에서 0건; 감시자 수신자는 ego 하나 |
| 미참여 감시자 | O2·O4의 수신 카메라 프레임 0개 |
| 자동 테스트 | 28개 통과: 기존 통신 20개 + 수신 영상 8개(actor/frame/hash, 미검증·이탈·파일 오류, 타임라인, 실시간 합성 경로) |

결과 폴더: `_DAMOS/reports/live_sharing_20260916T104133_201283Z/`. 전체 영상은 `ego_neighbour_sharing.mp4`, 대상 변경이 집중된 원본 60~90초 구간은 `ego_neighbour_handoff_30s.mp4`입니다. 짧은 영상의 약 4초에 O3 추가, 7초에 O1 제외, 15초에 O5 추가를 볼 수 있습니다. 위치·시각은 `video_telemetry.jsonl`, 연결 변경은 `membership.jsonl`, 실제 교환 결과는 `ego_rounds.jsonl`에 대응합니다.

수신 화면을 추가한 동일 실행 재구성본: `ego_neighbour_received_views.mp4`(전체), `ego_neighbour_received_handoff_30s.mp4`(60~90초). 기존 ego·지도 영상과 해당 시점의 완료 라운드 PNG를 사용하며 시뮬레이션 재실행·객체 재생성·새 성능 계측은 하지 않습니다. 원본 결과는 보존하고 `received_views_summary.json`, `received_views_telemetry.jsonl`에 프레임별 이미지 대응·해시 검사를 별도 기록합니다. 1440×900 구버전 녹화본에만 적용되는 재구성 명령:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
/home/vvu/anaconda3/envs/carla4/bin/python _DAMOS/scripts/render_received_sharing_video.py \
  _DAMOS/reports/live_sharing_20260916T104133_201283Z
```

단일 vvu의 loopback TCP이며, 실제 무선 네트워크 및 탐지 포함 18초 성능 달성 검증은 아닙니다.

### 초기 전원 상호 통신 시험 기록 (변경 전)

| CARLA 실행 | 노드 수 | ego 공유 성공 | ego 평균 / 최대 | 결과 폴더 (`_DAMOS/reports` 아래) |
|---|---:|---:|---|---|
| S5 | ego 1 + 배달로봇 1 | 5/5 | 0.737초 / 0.748초 | `live_sharing_20260916T100227_501373Z` |
| 랜덤 S8·S9·S2 동시 실행 | ego 1 + 감시자 11 (두 종류 포함) | 5/5 | 10.312초 / 10.715초 | `live_sharing_20260916T100940_225173Z` |

위 표는 **ego 중심 통신으로 바꾸기 전**의 전원 상호 통신 결과입니다. 정면 800×600 RGB, 실제 Groth16, radius=0, vvu 단일 호스트 조건. 12개 노드 시험은 60개 노드 라운드 검증 성공, 이미지 60장의 SHA-256 일치 확인. 당시 테스트 16개 통과. 현재의 ego 중심 통신 성능 수치로 재사용하지 않습니다. 탐지 미포함 결과이며 평균·최대는 해당 5회 표본에만 해당합니다.

초기 랜덤 S9·S2·S1 시험은 Scenic 생성 재시도 중 폐기된 ego ID에 카메라를 붙여 실패했습니다. 공유 모드에서 동일 ego·전체 시나리오 actor 집합이 0.5초 및 3 tick 이상 유지되는지 확인한 뒤 연결하도록 수정했습니다. 계측 도중 actor를 새로 생성하거나 다른 ego로 교체하지 않습니다. S2/S4 위치·Scenic 생성 규칙은 변경하지 않았습니다.

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

## HW/SW 고장상황 구현 범위

2026-10-22 중복 통합 기준 **총 15종: 단일 13종·복합 2종**. 실제 파이프라인 검증 10종, 이미지·캡처 단계 5종. Ego 입력 고장 또는 Ego로 들어오는 감시자 데이터의 통신 고장 기준.

| ID | 고장상황 | 구현·검증 수준 |
|---|---|---|
| F01 | 정면 카메라 신호 소실 | 파이프라인 검증. Ego 정면 raw RGB를 0으로 대체 |
| F02 | 카메라 품질 저하 | 이미지·뷰어 단계. blur·occlusion·color failure·shaking을 한 유형으로 집계 |
| F03 | 카메라 방향 틀어짐 | 이미지·센서 각도 변경 캡처. 인지·ZKP 경로 미검증 |
| F04 | LiDAR 좌표 노이즈 | 이미지 단계. 수집한 point cloud에 Gaussian noise |
| F05 | LiDAR 일부 포인트 누락 | 이미지 단계. 일부 점 제거 |
| F06 | LiDAR 이상 포인트 추가 | 이미지 단계. 가짜 점 추가 |
| F07 | LiDAR 전체 데이터 소실 | 파이프라인 검증. raw LiDAR 버퍼 비움 |
| F08 | 센서 입력 지연 | 파이프라인 검증. 실제 과거 입력 유지 |
| F09 | 인지 출력 고정·오래된 출력 유지 | 파이프라인 검증. 실제 과거 모델 출력 유지. OS 프로세스 중지 아님 |
| F10 | 감시자 송신 단절 | 파이프라인 검증. 실제 전송 생략 |
| F11 | 전송 본문 변조 | 파이프라인 검증. 증명 생성 후 본문 변경 |
| F12 | 과거 패킷 재전송 | 파이프라인 검증. 이전 실제 본문·증명 재사용 |
| F13 | ZKP 증명 손상 | 파이프라인 검증. 실제 생성 증명 변경 |
| F14 | RGB·LiDAR 동시 소실 | 파이프라인 검증. 복합 고장 |
| F15 | RGB·LiDAR 소실+감시자 송신 단절 | 파이프라인 검증. 복합 고장 |

기존 보고서의 `module_stop`·`stale_perception_output`은 현재 구현상 과거 출력 유지이므로 F09로 통합. 과거 이름은 `fault_catalog.py`에서 F09 별칭으로 해석하며 신규 보고서에서 따로 생성·집계하지 않음. 일부 점 누락(F05)과 데이터 전체 소실(F07)은 다른 조건으로 유지.

중복 통합 후 보고서 고장 8종 + 파이프라인 시험 10종 - 공통 3종(F01·F08·F09) = 15종. 정상 `none`과 비교용 정상 화면은 고장 수에 미산입.
파이프라인 검증 근거는 2026-10-14 실제 시험. **S5 1종 × 날씨 3조건 × 고장 10종 = 30개 시험 조건**. S5는 인도 불법 주차 차량 6대가 있는 배경이며, 대응 대상은 이 장애물 자체가 아니라 Ego 센서 입력·인지 출력·감시자 통신의 고장.

| 고장 예시 | 실제 대응 동작 |
|---|---|
| Ego 정면 카메라 소실·LiDAR 소실·과거 입력/출력 유지 | 불량 로컬 입력·출력 배제, 유효한 감시자 BEV로 협력 출력 대체 |
| 감시자 송신 단절·변조 본문·이전 패킷·손상 증명 | 미수신·검증 실패 데이터 병합 금지, 유효 감시자 정보가 없으면 협력 인지 불가·정지 필요 상태 |
| 고장 종료 후 정상 입력·검증된 감시자 정보 복귀 | 정상 데이터 3회 연속 확인 후 인지 서비스 NORMAL 복귀. 운행 자동 재개 아님 |

맑은 낮·폭우·야간에 각각 새 S5 장면 1개를 만들고 같은 장면에서 고장 10종을 순서대로 주입. 고장 시험의 세 날씨는 일반 Scenic 랜덤 날씨 4조건과 별도 설정이며, 안개 조건은 이번 고장 시험에 미포함. 비교용 Ego는 정지 유지, 정지 요구 이후 별도 저속 제동 기능 확인. 장애물 회피·자연 주행 중 전체 안전정지·고장 상태의 이상상황 탐지정확도를 검증한 시험은 아님.
실행 방법·결과·한계는 [컨설팅 대응 기술문서](consulting_response.md) 참고. 일반 실행의 자동복구 기능으로 활성화되는 것은 아님.

통합 목록 확인(CARLA 서버 실행 불필요):

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
/home/vvu/vv/DAMOS/.venvs/bev/bin/python _DAMOS/scripts/fault_catalog.py
```

Camera fault 선택값:

| mode | 포함 항목 |
|---|---|
| `blackout` | Camera blackout |
| `blur`, `occlusion`, `color_failure`, `shaking` | Camera degradation |
| `misalignment` | Camera misalignment |
| `random` | visible camera fault 중 랜덤 선택 |
| `none` | camera fault 없음 |

Camera fault 사용 예시:

```bash
--save-actor-camera-captures \
--ego-front-camera-fault random
```

## Ego fault report 저장

보고서용 이미지는 ego 기준으로 생성합니다. 다음 옵션을 추가하면 ego의 임시 LiDAR와 전방 RGB 센서(기존 센서가 없으면 임시 생성)에서 데이터를 수집해 고장 효과 이미지를 저장합니다.

카메라 3종은 수집 영상 변형, LiDAR 3종은 수집 point cloud 변형, 입력 지연·출력 유지는 수집한 과거 프레임 비교로 재현합니다. 중복 출력 항목을 합쳐 **고장 8종 + 정상 비교 화면 1개**를 3x3에 표시합니다. 이 보고서 자체는 인지 파이프라인 시험이 아니며 실제 인식 모듈을 중지하거나 제어 입력을 지연시키지 않습니다. 이미지 생성 성공은 고장 복구 또는 18초 처리 성능의 달성 증빙이 아닙니다.

```bash
--save-ego-fault-report
```

저장되는 대표 파일:

| 파일 | 의미 |
|---|---|
| `01_ego_lidar_clean.png` | ego-local LiDAR 정상 point cloud |
| `02_ego_lidar_noise_dropout_outliers.png` | Gaussian noise, dropout, outlier가 섞인 LiDAR |
| `04_ego_sensor_delay_5_frames.png` | 현재 ego front RGB와 지연된 frame 비교 |
| `05_ego_module_stop_freeze.png` | F09 출력 고정·노후화 통합 비교. 기존 파일명 유지, OS 프로세스 중지 아님 |
| `ego_fault_report_3x3.png` | 고장 8종 + 정상 화면 1개. 정상 화면은 고장에 미산입 |
| `ego_fault_report_contact_sheet.png` | 위 이미지를 보고서용으로 모은 요약 이미지 |
| `ego_fault_report.json` | F01~F15 통합 개수·실제 캡처된 고장 수·각 기록의 통합 ID. 이미지 기록을 파이프라인 검증으로 표시하지 않음 |

이전 보고서·시험 원본은 보존하며 삭제·덮어쓰기하지 않음. 신규 보고서에서는 중복된 `fault_09_stale_perception_output.png`를 별도 생성하지 않음.

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

이 문서의 `/home/vvu/...`는 vvu 전용 경로입니다. 새 컴퓨터에서는 사용자 경로와 `CARLA_PYTHON_BIN`, `DAMOS_BEV_PYTHON`, `DAMOS_BEV_CHECKPOINT`를 실제 설치 위치로 변경합니다. 현재 clone만으로 CARLA/UE를 자동 설치하는 installer는 제공하지 않습니다.

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

**C++·asset 준비 확인:** 위 복사는 빌드 완료를 뜻하지 않습니다. CARLA에 DAMOS walker factory를 연결하고 custom walker asset을 import한 source-build 환경이 필요합니다. `custom_walkers/README.md`, `unreal/run_import_damos_walkers.sh`와 해당 CARLA 빌드 설정 참조. 준비된 CARLA에서도 `DamosVisibilityLockComponent.cpp` 변경 후에는 CARLA 모듈을 다시 빌드하고 서버를 재시작해야 배달로봇 카메라 정지가 해결됩니다. 기존 실행 바이너리나 Python 파일만으로 적용되지 않습니다. 전체 엔진 재빌드는 이번 수정 검증의 필수 수행 항목이 아니며, vvu에서는 해당 CARLA 모듈 재컴파일 후 재취득 검증했습니다.

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

BEV 실행에는 이어서 [BEV 추가 패키지·가중치](#실제-bev-추론zkp-공유ego-합병), [Node.js·ZKP 의존성](#실시간-정면-이미지-공유와-zkp)을 준비해야 합니다. 기본 Scenic 실행 성공만으로 BEV 환경 설치가 완료되지는 않습니다. 대용량 custom asset·후보 가중치는 별도 전달 필요합니다.

## 새 컴퓨터 세팅 후 최소 검증

```bash
cd ~/vv/DAMOS/Carla-0.9.16-source

python -m py_compile \
  _DAMOS/scripts/*.py \
  _DAMOS/unreal/*.py \
  _DAMOS/carla_import/*.py

for script in _DAMOS/scripts/*.sh _DAMOS/custom_walkers/*.sh _DAMOS/unreal/*.sh
do
  bash -n "$script" || exit 1
done
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
| BEV 공유·평가 | `_DAMOS/reports/live_bev_*` |
| 2×3 통과 영상 | 지정 `--report-root` 아래 `bev_video_*`, `passby_sweep_*` |
| 최신 표시 합본 | `_DAMOS/reports/bev_orange_ego_20261008/videos/S1-S9_orange_ego_2x3.mp4` |

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
| frame ID는 증가하지만 배달로봇 RGB 정지 | 과거 C++ 시각 숨김이 scene-capture에 전파 | 최신 `DamosVisibilityLockComponent.cpp` 반영·CARLA 모듈 재빌드·서버 재시작·새 취득 |
| CUDA/Mamba import 실패 | torch/CUDA/ABI/Python wheel 조합 불일치 | BEV 환경 조합 확인; 모의 출력으로 우회하지 않음 |
| GPU 메모리 부족 | 이웃마다 독립 모델 적재 | 단일 시나리오·적은 이웃으로 먼저 검증; 많은 이웃 성능 미보장 |
| 후보 모델을 못 찾음 | 가중치는 Git 제외 | 별도 가중치 전달·SHA 확인, 원본으로 자동 대체하지 않음 |
| 영상은 있지만 sweep 실패 | 종료 coverage·접촉·RPC 등 별도 오류 | `returncode`와 `video_complete`·실패 기록 함께 확인, 영상 완료를 전체 실행 통과로 해석하지 않음 |

## 수정 후 검증

코드를 수정했다면 최소한 문법 검증을 실행합니다.

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source

/home/vvu/anaconda3/envs/carla4/bin/python -m py_compile \
  _DAMOS/scripts/*.py \
  _DAMOS/unreal/*.py \
  _DAMOS/carla_import/*.py

for script in _DAMOS/scripts/*.sh _DAMOS/custom_walkers/*.sh _DAMOS/unreal/*.sh
do
  bash -n "$script" || exit 1
done
```

BEV·ZKP·영상 회귀 시험은 아래 한 명령으로 실행합니다. Node.js와 `vendor/damos_zkp/node_modules` 설치 필요. `DAMOS_TEST_REAL_ZKP=1`을 생략하면 실제 Groth16 시험이 skip될 수 있으므로 시험 결과의 skip도 확인합니다.

```bash
DAMOS_TEST_REAL_ZKP=1 /home/vvu/vv/DAMOS/.venvs/bev/bin/python \
  -m unittest discover -s _DAMOS/tests -p 'test_*.py'

git -C /home/vvu/vv/DAMOS/_git/STL_DAMOS diff --check
```

2026-10-08 표시 수정 후 확인: BEV 171개·이미지 공유 20개·공유 영상 8개, 총 **199개 통과**. 단위·통합 시험 수이며 CARLA 실행 횟수·인지 정확도·18초 합격 횟수가 아닙니다. C++ 모듈은 Python 시험으로 검증되지 않으며 별도 빌드·실제 카메라 갱신 확인 필요.

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
