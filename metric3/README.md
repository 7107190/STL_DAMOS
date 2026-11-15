# 3번: CARLA 실구동 탐지정확도

```bash
cd /home/vv/vv/DAMOS
bash start_metric3.sh
```

- Goosebumps PC의 그래픽 데스크톱에서 실행. 가상환경 자동 설정.
- 결과 화면: `http://127.0.0.1:8763/`.
- 전체 신규 시험: `bash start_metric3.sh --full`.
- 대표 신규 시험: `bash start_metric3.sh --scenario S1`.
- 제출 ZIP 검증: 필요한 `metadata/inputs/`를 NAS에서 복원한 뒤 `bash start_metric3.sh --metadata`.
- 평가 단위: 신규 장면의 이상상황 앵커 1개당 1건.
- 탐지: 정답 영역과 겹치는 예측 장애물 셀 3개 이상 연결.
- 성공: 0.5초 이상 연속 탐지. 전체 100건의 성공률 70% 이상.
- 11월 11일 보존 실행: `live_metric3_20261111T101806_78b40c40`.
- 결과: 100/100건 성공, 100%, 실행 오류 0건.
- 보존 원본: NAS `Storage/Data/7107190/Damos/5090_completed_runs_20261114/completed_runs.tar.zst` 내부 `metric3/reports/`, `metric3/jobs/`. 서버 완료 데이터 삭제.
- 엔진·센서 설정은 `../shared/runtime/`, Python은 `../shared/venvs/simulation/` 사용.
- 이 폴더에는 평가 코드·설정·결과만 보관. 엔진·가상환경 사본이나 링크 없음.
- 구 경로의 과거 결과는 뷰어에서 현재 위치로 변환. 홈 호환 링크 불필요.
