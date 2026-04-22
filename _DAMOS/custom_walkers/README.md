# DAMOS Custom Walkers

이 폴더는 DAMOS용 커스텀 walker 관련 메모, 환경 파일, 실행 스크립트를 모아둔 곳입니다.

## 작업 기준

- 앞으로의 기준 작업 서버: `Host vvu`
- 기준 작업 루트: `/home/vvu/vv/DAMOS`
- 실제 DAMOS 작업 폴더: `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS`
- 호환용 링크:
  - `/home/vvu/vv/Carla-0.9.16/_DAMOS`
  - `/home/vvu/vv/Carla-0.9.16-source/_DAMOS`

즉, 실제 수정은 source 트리 기준 `_DAMOS`에서 진행하고, 예전 경로는 호환용 링크로만 유지합니다.

## 목표 매핑

- DeliveryBot
  - 원본 모델: `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/3d_model/delivery-bot-by-glowbox/source/DeliveryBot.fbx`
  - 목표 타입: `walker`
  - 베이스: 휠체어 사용 가능한 pedestrian walker
  - 의도: 인도 위를 이동하는 배달로봇

- Humanoid
  - 원본 모델: `/home/vvu/vv/DAMOS/Carla-0.9.16-source/_DAMOS/3d_model/mixamo-bot-character-lowpoly.zip`
  - 핵심 FBX: `source/CHR_R_Maxim.fbx`
  - 목표 타입: `walker`
  - 베이스: 일반 pedestrian walker
  - 의도: 인도 위를 이동하는 휴머노이드

## 현재 완료 상태

- CARLA 0.9.16 source build + Unreal Engine 4.26 환경 준비 완료
- `walker.pedestrian.damos_deliverybot` 등록 완료
- `walker.pedestrian.damos_humanoid` 등록 완료
- Town01 데모 실행 가능
- `S_.scenic`를 수정하지 않고 Town10HD_Opt에서 Scenic + custom walker 외부 주입 가능
- Scenic support actor 주변 anchor 기반 생성 가능
- trajectory PNG / focus PNG / JSON 리포트 생성 가능

## 현재 구조

- 실행 패키지: `/home/vvu/vv/DAMOS/Carla-0.9.16`
- source 작업 트리: `/home/vvu/vv/DAMOS/Carla-0.9.16-source`
- Unreal 엔진: `/home/vvu/vv/DAMOS/UnrealEngine_4.26`
- Scenic checkout: source 트리 안 `Scenic/` 는 packaged Scenic을 가리키는 링크

## 중요한 구분

`/home/vvu/vv/DAMOS/Carla-0.9.16/Import/DAMOSProps` 는 static prop import용입니다.
지금 진행 중인 DAMOS 작업은 prop import가 아니라 walker 제작과 Scenic 통합입니다.

## 관련 핵심 파일

- `_DAMOS/scripts/run_custom_walkers_demo.sh`
- `_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh`
- `_DAMOS/scripts/run_scenic_with_custom_walkers.py`
- `_DAMOS/scripts/custom_walker_runtime.py`
- `_DAMOS/scripts/scenic_custom_walker_injector.py`
- `_DAMOS/custom_walkers/ue4_env.sh`
- `Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/DamosWalkerFactory.cpp`
- `Unreal/CarlaUE4/Plugins/Carla/Source/Carla/Damos/DamosBoneMapPoseComponent.cpp`

## 현재 권장 검증 방법

headless 기준 Scenic 통합 검증:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
_DAMOS/scripts/run_scenic_custom_walkers_town10hd.sh --restart --headless
```

Town01 커스텀 walker 데모:

```bash
cd /home/vvu/vv/DAMOS/Carla-0.9.16-source
_DAMOS/scripts/run_custom_walkers_demo.sh
```

## 현재 남은 작업 성격

이제부터는 환경 구축보다는 DAMOS 로직 작업이 중심입니다.
예를 들면:

1. ego와 custom walker 사이 협력 정보 구조 설계
2. Scenic anchor 선택 정책 고도화
3. 실제 DAMOS 메인 파이프라인과 injector 연결
4. 리포트/로그를 실험 결과물 형식으로 정리
