"""Package newly generated metadata without relabelling the prior nine cases."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from bev_accuracy_batch import sha256, write_json
from verify_metric3_100 import verify


def application_text(summary):
    return """# 3. 복합 모빌리티 환경 이상상황 탐지정확도

## 시험의뢰항목

- 평가방법
CARLA Town10HD_Opt에서 생성한 S1~S9 이상상황의 신규 앵커 100건과 동일 시점의 Ego·감시자 인지정보 공유 및 병합 BEV 메타데이터 통합 분석.
앵커별 비정상 객체의 실제 actor ID·위치·시뮬레이션 시각과 병합 BEV의 장애물 존재·위치 교차 검증. Ego BEV 평가 범위 내에서 정답 영역과 겹치는 연결 장애물 셀 3개 이상 확인 및 0.5초 이상 연속 탐지 시 해당 앵커 성공 판정.

- 평가산식
이상상황 탐지정확도 = (성공 앵커 수 A / 전체 시험 앵커 수 B) × 100 [%]
B = 100건. 성공 기준 = 연속 탐지시간 0.5초 이상. 목표 = 70% 이상.
앵커의 멤버 객체 수 및 연속 프레임 수는 독립 시험 건수에서 제외. 미탐지·평가 불가·데이터 오류 건은 전체 시험 건수에 포함.

## 상세시험절차

- 시험절차
1. 사전 지정된 S1~S9 앵커 100건의 시험계획 및 JSON 메타데이터 유효성 확인·로드
2. 앵커별 Ego·감시자·비정상 객체의 actor ID, 관측 프레임, 시뮬레이션 시각 및 좌표계 일치 여부 교차 검증
3. 동일 시점의 병합 BEV 장애물 셀과 비정상 객체 정답 영역 대조. 정답 셀 3개 이상, 정답 영역과 겹치는 8방향 연결 장애물 셀 3개 이상 확인 시 해당 프레임 탐지 판정
4. 연속 탐지 구간의 최초·최종 관측 시각 차이 계산. 미탐지·관측 누락·정답 부족·평가 범위 이탈·차도 이탈 시 해당 연속 구간 종료
5. 0.5초 이상 연속 탐지가 확인된 앵커의 성공 수 합계 산출. 전체 100건 대비 성공률 계산 및 70% 이상 여부 최종 기록

성공 조건 최초 충족 시 해당 앵커 판정 종료. 미충족 시 지정 관측 구간 전체 샘플 분석.
"""


def prepare(trial, output, archive, shared_directory=None):
    checked = verify(trial)
    if output.exists() or archive.exists():
        raise FileExistsError("Use new delivery paths; previous evidence must remain intact")
    summary = json.loads((trial / "summary.json").read_text())
    output.mkdir(parents=True)
    for name in ("protocol.json", "protocol_lock.json", "summary.json"):
        shutil.copy2(trial / name, output / name)
    if (trial / "zkp_spotcheck.json").exists():
        shutil.copy2(trial / "zkp_spotcheck.json", output / "zkp_spotcheck.json")
    shutil.copytree(trial / "cases", output / "cases")
    source_index = {}
    frame_rows = []
    for source in sorted((trial / "results").rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(trial)
        source_index[str(relative)] = {"original_path": str(source), "bytes": source.stat().st_size,
                                      "sha256": sha256(source)}
        if source.suffix in (".json", ".log"):
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if source.name == "evidence.json":
                row = json.loads(source.read_text())
                frame_rows.append({k: v for k, v in row.items() if not k.endswith("cell_indices")
                                   and k != "ego_pose_model_world"})
    write_json(output / "source_file_index.json", source_index)
    (output / "tools").mkdir()
    scripts = Path(__file__).parent
    for name in ("verify_metric3_100.py", "test_metric3_100.py", "bev_accuracy_batch.py",
                 "run_metric3_100.py", "prepare_metric3_100.py"):
        shutil.copy2(scripts / name, output / "tools" / name)
    (output / "frozen_core").mkdir()
    protocol = json.loads((trial / "protocol.json").read_text())
    for name, record in protocol["source_hashes"].items():
        source = Path(name)
        if sha256(source) != record["sha256"]:
            raise ValueError("Frozen source changed during delivery: " + name)
        if source.suffix in (".py", ".sh", ".json", ".scenic", ".txt"):
            destination = output / "frozen_core" / record["snapshot_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    case_rows = [{key: case.get(key) for key in ("case_id", "scenario", "status", "successful",
        "samples", "eligible_samples", "fused_longest_span_seconds", "returncode", "error")}
        for case in summary["cases"]]
    for filename, rows in (("anchor_cases.csv", case_rows), ("frames.csv", frame_rows)):
        if rows:
            with (output / filename).open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    write_json(output / "anchor_cases.json", summary["cases"])
    (output / "APPLICATION_TEXT.md").write_text(application_text(summary), encoding="utf-8")
    table = "\n".join(f"| {name} | {row['planned']} | {row['successful']} | {row['successful']/row['planned']*100:.2f}% |"
        for name, row in summary["per_scenario"].items())
    report = f"""# 3번항목 신규 100건 결과

결론: 성공 {summary['successful_cases']}/100건, 성공률 {summary['success_rate_percent']:.2f}%. 목표 70% {'충족' if summary['criterion_pass'] else '미충족'}.

| 시나리오 | 시험 건수 | 성공 | 성공률 |
|---|---:|---:|---:|
{table}

- 신규 CARLA 장면 100회, 각 장면에서 추론 전 기하학적 규칙으로 지정한 앵커 1곳 평가
- S1~S8 각 11건, S9 12건. S9 추가 1건은 결과 확인 전 지정
- 서로 다른 seed·날씨·Ego 접근 조건. 고정 시나리오의 고정 배치 유지
- 최초 20m 이내 접근부터 6초·5Hz, 정상 취득 시 30샘플. 시험 건수와 샘플 수 구분
- 성공 조건 최초 충족 시 해당 회차 추론 종료. 미충족 시 30샘플 전부 처리. 전체 구간의 성공 여부와 동일한 이진 판정
- 취득 샘플 수와 처리 샘플 수 분리. 연속 탐지시간은 처리 구간에서 확인된 값이며 전체 취득 구간의 최대값 주장 없음
- 모델 및 후처리: 기존 고정 체크포인트. 시험 정답으로 모델·threshold 변경 없음
- 실제 RGB 6방향·4시점 및 일반 LiDAR 입력, 실제 Groth16 증명·전송·검증 및 Ego BEV 병합
- 센서 취득 후 실제 모델·ZKP·병합의 오프라인 처리. 이 자료는 18초 처리속도 시험과 별도
- 실제 실행 호스트·CPU·GPU·Python 패키지 버전은 `protocol.json/environment` 기록
- 판정: 해당 actor-ID 정답 영역 내 8방향 연결 장애물 셀 3개 이상, 연속 시각 차이 0.5초 이상
- 실패·평가 불가·오류도 전체 100건에 포함. 성공 장면만 선별하거나 seed 교체 없음
- 기존 9건 예비자료와 135회 처리속도 자료는 이 100건에 합산하지 않음
- {summary['samples']}개 분석 샘플. 상태별 건수: {json.dumps(summary['statuses'], ensure_ascii=False)}

## 파일

- `protocol.json`, `cases/`: 결과 확인 전 고정한 계획·코드·모델 해시
- `summary.json`, `anchor_cases.csv`: 앵커별 판정 및 전체 성공률
- `results/*/recording/round_*/evidence.json`: 시각·GT 셀·실제 장애물 셀·연결 수·판정
- `ground_truth.json`, `peers.json`, `*_packet.json`: 동일 프레임 actor ID·좌표·실제 ZKP 처리 기록
- `source_file_index.json`: 별도 보존 중인 원시 입력 NPZ·BEV 등의 원본 경로·SHA-256
- `tools/verify_metric3_100.py`: Python 표준 라이브러리만 사용한 독립 셀·연속 시간·성공률 재계산

원시 입력·BEV NPZ는 원본 저장소 별도 보존. 이 ZIP에는 판정 재계산용 JSON과 실행 로그 포함.
"""
    (output / "REPORT.md").write_text(report, encoding="utf-8")
    (output / "README.md").write_text("# 3번항목 메타데이터 100건\n\n" +
        "`REPORT.md` 결과, `APPLICATION_TEXT.md` 신청서 문구 참조.\n\n" +
        "검증: `python3 -B tools/verify_metric3_100.py .`\n" +
        "시각 단위: 나노초. 초 환산: 시각 차이 / 1,000,000,000.\n" +
        "셀: 200×200, 0.4m, index=x_index×200+y_index, 보간 없는 실제 예측.\n", encoding="utf-8")
    write_json(output / "validation.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
        "independent_cell_and_duration_verification": checked})
    entries = {str(p.relative_to(output)): {"bytes": p.stat().st_size, "sha256": sha256(p)}
               for p in sorted(output.rglob("*")) if p.is_file()}
    write_json(output / "manifest.json", {"files": entries})
    verify(output)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as stream:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                stream.write(path, str(Path("3번항목_메타데이터_100건") / path.relative_to(output)))
    with tempfile.TemporaryDirectory(prefix="metric3_100_verify_") as temporary:
        with zipfile.ZipFile(archive) as stream:
            if stream.testzip() is not None:
                raise ValueError("ZIP CRC failure")
            stream.extractall(temporary)
        verify(Path(temporary) / "3번항목_메타데이터_100건")
    delivery = {"archive": str(archive), "bytes": archive.stat().st_size,
                "sha256": sha256(archive), "result": checked, "zip_crc_and_unpacked_verification": True}
    if shared_directory:
        shared = shared_directory / archive.name
        if shared.exists():
            raise FileExistsError("Shared delivery already exists; no overwrite")
        shutil.copy2(archive, shared)
        if sha256(shared) != delivery["sha256"]:
            raise ValueError("Shared copy differs")
        delivery["shared_archive"] = str(shared)
    write_json(archive.with_suffix(".delivery.json"), delivery)
    print(json.dumps(delivery, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--shared-directory", type=Path)
    args = parser.parse_args()
    prepare(args.trial, args.output, args.archive, args.shared_directory)


if __name__ == "__main__":
    main()
