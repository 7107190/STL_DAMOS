# 2번: CARLA 실구동 처리속도

```bash
cd /home/vv/vv/DAMOS
bash start_metric2.sh
```

- Goosebumps PC의 그래픽 데스크톱에서 실행. 가상환경 자동 설정.
- 결과 화면: `http://127.0.0.1:8762/`.
- 전체 신규 시험: `bash start_metric2.sh --full`.
- 대표 신규 시험: `bash start_metric2.sh --scenario S1`.
- 제출 ZIP 검증: 필요한 `metadata/inputs/`를 NAS에서 복원한 뒤 `bash start_metric2.sh --metadata`.
- 입력: 각 노드 RGB 6방향 x 새로운 4프레임 + 현재 LiDAR 1프레임.
- 처리: 전처리·추론·Groth16 생성/검증·TCP·Ego BEV 병합.
- 판정: S1~S9 x 3세션 x 5회, 총 135회 정상 완료 및 최대 18초 이하.
- 11월 11일 보존 실행: `live_metric2_20261111T101806_367bf489`.
- 결과: 135/135회, 최대 1.790749524초, 평균 1.667165869초.
- 보존 원본: NAS `Storage/Data/7107190/Damos/5090_completed_runs_20261114/completed_runs.tar.zst` 내부 `metric2/reports/`, `metric2/.metric2_ui_jobs/`. 서버 완료 데이터 삭제.
- 화면·평가 코드: `app/`, 평가 원본: `reports/`·`.metric2_ui_jobs/`.
- 현재 위치: `/home/vv/vv/DAMOS/metric/metric2`.
- 공통 엔진·센서 설정: `../../shared/runtime/`. 공통 Python: `../../shared/venvs/simulation/`.
- 모델·Node·추론 의존성: `../../shared/`. 이 평가 폴더에 엔진·가상환경 사본이나 링크 없음.
- 구 경로의 과거 결과는 뷰어에서 현재 위치로 변환. 홈 호환 링크 불필요.
