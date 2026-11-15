# 보존 메타데이터 ZIP 검증

- `inputs/`: 검증할 제출 ZIP·압축 해제본 위치. 현재 기존 자료는 NAS로 이관되어 비어 있음.
- `evaluate.py`: 해당 ZIP의 계측값·탐지 기록을 다시 집계하는 도구.
- `reports/`: 새 검증 화면·CSV·소스 해시 기록 저장 위치.
- CARLA 신규 실행·모델 재추론과 별개. 저장된 ZIP의 원 시험 결과를 검증.

## 기존 자료 재검증

NAS `Storage/Data/7107190/Damos/5090_completed_runs_20261114/completed_runs.tar.zst`의
`metadata/inputs/metric2.zip` 또는 `metric3.zip`을 현재 `inputs/`로 선택 복원한 뒤 실행.
압축 해제본 `metadata/inputs/metric2/`, `metric3/`을 복원한 경우 `--prepare` 생략 가능.

```bash
cd /home/vv/vv/DAMOS
bash start_metric2.sh --metadata --prepare
bash start_metric2.sh --metadata
bash start_metric3.sh --metadata --prepare
bash start_metric3.sh --metadata
```

2026-11-11 실구동 원본도 같은 NAS 압축파일의 `metric2/reports/`, `metric3/reports/`,
`results/full_evaluation_export_20261111/`에 보존. 과거 ZIP과 최신 실구동 결과는 별개.
