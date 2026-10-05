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

## 성능지표 2번 검증 준비

2026-09-16 확인 기준, 김기웅의 핵심 달성 항목은 **개별 모빌리티 시스템 시뮬레이션 처리 속도**이며, 1단계(2025~2026) 목표는 **18초 이내**, 평가 비중은 **25%**입니다. 2차년도는 공인 시험성적(확인)서 평가가 기재되어 있습니다. 출처: [Notion 목표 & 평가](https://www.notion.so/3337d69010ae804a8f87c6417232a9d1).

`--live-ego-sharing`으로 실제 정면 이미지의 공유·Groth16 검증·ego 부분 계측을 실행할 수 있습니다. 탐지 모델이 없으므로 탐지 포함 18초 합격 여부는 아직 판정하지 않습니다.

| 구분 | 의미 |
|---|---|
| `--scenic-time 20` | 일반 모드의 Scenic 실행 시간 제한; live sharing에서는 공유 횟수까지 수명 연장 |
| `--realtime-factor 1.0` | 시뮬레이션 진행 속도 조절; 18초 달성 증빙 아님 |
| `--runs 5` | 시나리오 실행 반복; 공식 평가 반복 횟수로 확정된 값 아님 |
| `--save-ego-fault-report` | 고장 효과 설명 이미지 생성; 처리시간·실제 모듈 정지 시험 아님 |
| 18초 처리시간 | ego 입력 수신부터 공유·검증·탐지 완료까지의 실제 경과시간; 탐지 미연결로 최종 판정 보류 |

원문의 평가 환경은 이동 정보, 환경 센서 데이터, 경로 최적화 정보의 공유 확인을 포함합니다. **아래는 2026-09-16 사용자와 합의한 구현 범위**이며, 경로계획 제외 범위와 공식 합격 통계는 평가 담당자에게도 확인해야 합니다.

| 항목 | 구현 기준 |
|---|---|
| 평가 대상 | ego만 대표 계측; 타 모빌리티의 동일 시간 달성 주장 아님 |
| 처리 단위 | 자기 정면 1장 + 해당 라운드의 모든 이웃 정면 1장씩 |
| 데이터 원천 | CARLA 실시간 센서, 처리 중에도 Scenic tick 유지 |
| 시작 | ego worker가 자기 raw BGRA 이미지 입력을 수신한 직후 |
| 현재 종료 | 이웃 전체 이미지 증명·무결성 검증과 PNG 디코딩 완료 후 빈 detector 호출 |
| 최종 종료 목표 | 실제 탐지 결과 생성 완료; 경로계획 제외 |
| 포함 | PNG 인코딩, 패킷·해시·증명 생성, TCP 송수신·대기, 검증, 디코딩 |
| 제외 | 센서 취득 대기·worker 초기화·키 등록·파일 저장; 아직 없는 탐지 시간은 null |
| 시간 기준 | ego 한 프로세스의 `perf_counter_ns`; CARLA 시각·타 PC 시각 차감 아님 |
| 후속 확정 | 공식 반복 수·합격 통계·하드웨어·실제 네트워크 환경 |

6방향 × 6프레임은 이예린의 인지 입력 규격 확정 후 반영합니다. ZKP 연동은 제공된 코드에 이미지용 패킷과 통신 계층을 직접 추가했습니다.

2026-10-05 합의 재확인: 목표는 **공유 처리만이 아니라 인지모델 탐지 완료까지 18초 이내**입니다. 모델 연결 후 최초 ego 입력 수신부터 탐지 완료까지 하나의 단조 시계로 계측하고, 그 사이의 전처리·대기·데이터 전달도 포함합니다. 현재 `ego sharing`과 추론 시간만 단순 합산하면 전체 구간의 시간이 누락될 수 있습니다. 탐지 모델 미연결 상태에서는 최종 합격을 표시하지 않습니다.

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

실행 파일: `_DAMOS/scripts/m2x/live.py`(CARLA adapter), `mesh.py`(노드·시간 계측), `security.py`(이미지 패킷·Groth16), `transport.py`(TCP). 탐지 모델 연결 지점은 `mesh.py`의 `pending_detector(local_image, shared_images)`입니다. 이후 모델 연결 시 이 함수를 실제 모델로 교체하고 추론 완료 동기화·탐지 시간 기록·최종 18초 판정을 함께 구현해야 합니다.

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

현재 고장상황은 모두 ego 기준으로 적용하거나 시각화합니다. Camera fault 선택 기능은 ego 차량의 `cam_front` 하나에만 적용하고, observer camera와 ego의 나머지 camera는 정상으로 유지합니다. 아래 9개 항목의 보고서 생성은 구현되어 있지만, 실제 협력 처리 파이프라인에서의 고장 주입·복구 검증은 완료되지 않았습니다.

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
| `none` | camera fault 없음 |

Camera fault 사용 예시:

```bash
--save-actor-camera-captures \
--ego-front-camera-fault random
```

## Ego fault report 저장

보고서용 이미지는 ego 기준으로 생성합니다. 다음 옵션을 추가하면 ego의 임시 LiDAR와 전방 RGB 센서(기존 센서가 없으면 임시 생성)에서 데이터를 수집해 고장 효과 이미지를 저장합니다.

카메라 3종은 수집 영상 변형, LiDAR 3종은 수집 point cloud 변형, 지연·정지·오래된 출력은 수집한 과거 프레임 비교로 재현합니다. 실제 인식 모듈을 중지하거나 제어 입력을 지연시키는 시험은 아닙니다. 3x3 이미지 생성 성공은 고장 복구 또는 18초 처리 성능의 달성 증빙이 아닙니다.

```bash
--save-ego-fault-report
```

저장되는 대표 파일:

| 파일 | 의미 |
|---|---|
| `01_ego_lidar_clean.png` | ego-local LiDAR 정상 point cloud |
| `02_ego_lidar_noise_dropout_outliers.png` | Gaussian noise, dropout, outlier가 섞인 LiDAR |
| `04_ego_sensor_delay_5_frames.png` | 현재 ego front RGB와 지연된 frame 비교 |
| `05_ego_module_stop_freeze.png` | 현재 입력과 고정된 과거 프레임을 비교한 모듈 출력 정지 재현 |
| `fault_09_stale_perception_output.png` | 현재 입력과 오래된 프레임을 비교한 출력 노후화 재현 |
| `ego_fault_report_3x3.png` | 고장 효과 9종의 3x3 보고서 이미지; LiDAR 확대·흰 여백 제거 반영 |
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
