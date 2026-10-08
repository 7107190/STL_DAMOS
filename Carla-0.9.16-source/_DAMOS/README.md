# DAMOS 런타임

**현재 기준: 비정상 상황 anchor당 감시자 1대, ego ↔ 근처 감시자 BEV 공유.**

프로젝트 상태·권장 명령은 [루트 README](../../README.md), 설치·옵션·결과 파일은 [사용법](../../docs/usage.md) 기준입니다. 이 파일은 런타임 내부 경로만 안내합니다.

## 작업 위치

| 항목 | 경로 |
|---|---|
| 런타임 | `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS` |
| 명령 실행 위치 | `/home/vvu/vv/DAMOS/Carla-0.9.16-source` |
| Git mirror | `/home/vvu/vv/DAMOS/_git/STL_DAMOS` |
| 기본 Python | `/home/vvu/anaconda3/envs/carla4/bin/python` |
| BEV Python | `/home/vvu/vv/DAMOS/.venvs/bev/bin/python` |

## 구성

| 디렉터리 | 역할 |
|---|---|
| `_scenarios/` | S1~S9 Scenic 정의·동시 compose |
| `scripts/` | 서버 실행·actor 배치·센서·추론·공유·평가·보고서 |
| `tests/` | BEV·공유·시각화 회귀 시험 |
| `vendor/yerin_bev/` | 제공 인지 원본·requirements·출처 해시 |
| `vendor/damos_zkp/` | Groth16 원본 회로·키·WASM·Node backend |
| `custom_walkers/`, `unreal/`, `carla_import/` | 환경·asset import·CARLA helper |
| `models/`, `datasets/`, `reports/`, `logs/` | 운영/생성 자료, Git 제외 |

Unreal C++ overlay는 `../Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/`에 있습니다. 배달로봇 카메라 갱신 수정은 `DamosVisibilityLockComponent.cpp`; CARLA 모듈 재컴파일·서버 재시작 필요. Python 복사만으로 적용되지 않습니다.

## 실행 경로

`run_scenic_custom_walkers_town10hd.sh` → `scenic_custom_walker_injector.py`:

1. Town10HD_Opt 서버 준비, Scenic 원래 ego·비정상 actor 생성 완료 대기.
2. 원래 actor ID로 anchor 확정, anchor당 휴머노이드/배달로봇 중 1대 인도 배치·방향 설정.
3. `sensor_config.txt`에 따라 RGB 6개 부착. BEV 모드에는 일반 LiDAR 추가.
4. 선택 모드 수행 후 report 저장·자원 해제.

| 모드 | 옵션·역할 |
|---|---|
| 기본 | 배치·카메라·coverage 확인 |
| 입력 저장 | `--save-bev-inputs`, 동일 4프레임 모델 입력 저장 |
| 이미지 공유 | `--live-ego-sharing`, 정면 1장 공유·ZKP |
| BEV 공유 | `--live-bev-fusion`, 추론·ZKP·ego 합병·존재 판정 |
| 평가 추가 | `--evaluate-bev`, 별도 GT 센서로 원래 actor·프레임 대조 |
| 영상 | `run_bev_passby_sweep.py`, 센서 취득 후 오프라인 모델/ZKP 재생 |

이미지 공유·BEV 공유·고장 보고서의 모드 제약은 wrapper와 사용법 참조. 감시자끼리 통신 없음. `--max-anchor-pairs`는 legacy 이름이며 최대 anchor 수 제한, 두 감시자 쌍을 뜻하지 않습니다.

## 보고서 해석

- 기본 저장 위치: `_DAMOS/reports/`.
- BEV `pipeline_budget_pass`: 합병·존재 판정까지 18초 이내 여부만 표시.
- `metric2_pass=null`: 공식 성능지표 합격 미확정.
- 영상의 흰 대상 윤곽·확대창: GT 안내, 모델 검출 상자 아님.
- 주황색: 장애물 예측. 빨간 EGO: 기록 위치·방향, 인지 출력 아님.
- 과거 배달로봇 RGB 정지 결과는 최신 협력 성능 증빙에서 제외. 정정·재취득 범위는 루트 README 참조.
