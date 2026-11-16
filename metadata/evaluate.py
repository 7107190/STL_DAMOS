"""Evaluate pre-extracted submissions; only explicit preparation opens a ZIP."""
import argparse
import ast
import csv
from datetime import datetime
import hashlib
from html import escape
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PACKAGES = {
    2: ("0d6a0a01cd6c1d455cbf485295518e7fcd90968ec5539479b3a01c114fc70202", "verify_submission.py"),
    3: ("ce0fdbe9567fcb55821199161ab9c488025db4248117c477b3060cff4888ddbf", "verify_metric3_retest.py"),
}
MANIFEST_HASHES = {
    2: "085a72cb01cf7900e6ffe8a8e6c6de4a6fc9b67a6f6d18b6ff3a7835d3b1e736",
    3: "811894e6f8ced0eee92baee4c0de8d81b16b4a03ffe76b14c34ed32dbcf4c4f3",
}


def check_input(pkg, metric):
    manifest = pkg / "manifest.json"
    if pkg.is_symlink() or manifest.is_symlink() or not manifest.is_file():
        raise ValueError(f"검사용 폴더를 확인하세요: {pkg}")
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_HASHES[metric]:
        raise ValueError("제출 원본 manifest와 불일치: 검증 중단")
    expected = json.loads(raw)["files"]
    found = set()
    for path in pkg.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"검사용 폴더 내 링크: {path}")
        if path.is_dir() or path == manifest:
            continue
        name = path.relative_to(pkg).as_posix()
        if (name not in expected or not path.is_file()
                or path.stat().st_size != expected[name]["bytes"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected[name]["sha256"]):
            raise ValueError(f"제출 원본과 다른 파일: {name}")
        found.add(name)
    if found != expected.keys():
        raise ValueError("제출 파일 누락: 검증 중단")


def prepare(pkg, metric):
    if pkg.exists() or pkg.is_symlink():
        check_input(pkg, metric)
        return
    raw = pkg.with_suffix(".zip").read_bytes()
    if hashlib.sha256(raw).hexdigest() != PACKAGES[metric][0]:
        raise ValueError("원본 ZIP SHA-256 불일치: 준비 중단")
    with tempfile.TemporaryDirectory(dir=pkg.parent) as tmp:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            archive.extractall(tmp)
        candidate, = Path(tmp).iterdir()
        check_input(candidate, metric)
        candidate.rename(pkg)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields=None):
    if fields is None:
        fields = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def timing_records(pkg):
    rows = read_csv(pkg / "analysis/measurements.csv")
    ordered = []
    for number, row in enumerate(rows, 1):
        elapsed = (int(row["ended_ns"]) - int(row["started_ns"])) / 1e9
        if elapsed <= 0 or not math.isclose(elapsed, float(row["elapsed_seconds"]), abs_tol=1e-9):
            raise ValueError("측정시간 대조 실패")
        ordered.append({"evaluation_number": number, **row, "elapsed_seconds": elapsed})
    if len(ordered) != 135 or len({(r["session"], r["scenario"], r["repeat"]) for r in ordered}) != 135:
        raise ValueError("135건 평가번호 대응 실패")
    return ordered


def anchor_records(pkg):
    rows, samples = [], []
    for number, path in enumerate(sorted((pkg / "results").glob("M3-*/result.json")), 1):
        row = json.loads(path.read_text())
        times = []
        for sample in sorted((path.parent / "recording").glob("round_*/peers.json")):
            ms = float(json.loads(sample.read_text())["ego"]["elapsed_ms"])
            if not math.isfinite(ms) or ms <= 0:
                raise ValueError("기록된 Ego 처리시간 오류")
            times.append(ms / 1000)
            samples.append({"evaluation_number": number, "case_id": row["case_id"],
                            "scenario": row["scenario"], "round_index": len(times), "ego_processing_seconds": ms / 1000})
        if len(times) != row["samples"] or not times:
            raise ValueError("앵커별 처리 시점 누락")
        rows.append({"evaluation_number": number, **row,
                     "mean_processing_seconds": math.fsum(times) / len(times)})
    if len(rows) != 100 or len({r["case_id"] for r in rows}) != 100:
        raise ValueError("100건 평가번호 대응 실패")
    return rows, samples


def table(headers, rows):
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def snippet(path, name):
    src = path.read_text(encoding="utf-8")
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == name)
    code = "\n".join(f"{i+1:3}  {line}" for i, line in enumerate(src.splitlines()) if node.lineno <= i+1 <= node.end_lineno)
    return f"<details><summary>{escape(path.name)} · {escape(name)} · {node.lineno}행</summary><pre>{escape(code)}</pre></details>"


def timestamp():
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="microseconds")


def capture_sources(metric, pkg, out):
    live = ROOT.parent
    paths = [live / f"start_metric{metric}.sh",
             live / "metric" / f"metric{metric}" / f"start_metric{metric}.sh"]
    if metric == 3:
        paths.append(live / "metric/metric2/start_metric3.sh")
    paths += [Path(__file__).resolve(), pkg / "tools" / PACKAGES[metric][1]]
    if metric == 3:
        paths.append(pkg / "tools/verify_metric3_100.py")
    saved = out / "sources"
    saved.mkdir()
    records = []
    for number, path in enumerate(paths, 1):
        raw = path.read_bytes()
        name = f"sources/{number:02d}_{path.name}"
        (out / name).write_bytes(raw)
        records.append({"file": str(path), "saved_copy": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    write_csv(out / "source_hashes.csv", records)
    (out / "source_hashes.sha256").write_text("".join(f"{r['sha256']}  {r['saved_copy']}\n" for r in records), encoding="utf-8")
    return records


def source_table(execution):
    return table(["가동 소스코드", "SHA-256"], ((Path(r["file"]).name, r["sha256"]) for r in execution["source_files"]))


def check_sources(execution):
    for row in execution["source_files"]:
        if hashlib.sha256(Path(row["file"]).read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("실행 도중 가동 소스코드 변경: 결과 확정 중단")


def open_result(path, disabled):
    opener = shutil.which("xdg-open")
    if opener and not disabled:
        subprocess.Popen([opener, str(path)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_page(execution, out):
    html = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DAMOS · 평가 실행 전</title>
<style>body{{font:15px/1.6 system-ui,sans-serif;color:#20333a;margin:30px}}main{{max-width:1380px;margin:auto}}h1{{font-size:24px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:10px;text-align:left;border-bottom:1px solid #d8e5e2;overflow-wrap:anywhere}}td:last-child{{font-family:monospace}}.scroll{{overflow:auto}}strong{{color:#12665b}}code{{overflow-wrap:anywhere}}</style>
<main><h1>{execution['metric']}번 제출 메타데이터 평가 · 실행 대기</h1>
<p><strong>재계산 전 · 이 화면에는 아직 오늘의 판정 결과가 없습니다.</strong></p>
<p>평가 ID: <code>{escape(execution['run_id'])}</code><br>실행 준비 시각(KST): {escape(execution['ready_at'])}<br>입력: {escape(execution['input_folder'])}</p>
<p>CARLA·인지모델 재실행이 아닌 제출 기록 재계산입니다. 첫 화면 캡처 후 실행 터미널에서 Enter를 누르면 검증을 시작합니다.</p>
<h2>오늘 가동 소스 SHA-256</h2>{source_table(execution)}<p>개별 평가번호: {'1~135' if execution['metric']==2 else '1~100'} · 시나리오별로 번호를 초기화하지 않습니다.</p></main></html>'''
    (out / "start.html").write_text(html, encoding="utf-8")


def report(metric, pkg, verified, out, checked_at, execution):
    if metric == 2:
        name = "개별 모빌리티 시스템 시뮬레이션 처리속도"
        evaluation = verified["evaluation"]
        rows = timing_records(pkg)
        mean = math.fsum(r["elapsed_seconds"] for r in rows) / len(rows)
        stats = [("정상 처리", f"{evaluation['normal_completions']} / {evaluation['expected_measurements']}회"),
                 ("전체 135건 평균 처리시간", f"{mean:.9f}초"),
                 ("Ego 최대 처리시간", f"{evaluation['representative_seconds']:.9f}초"),
                 ("18초 초과", f"{evaluation['time_limit_exceedances']}회")]
        formula = "T = (ended_ns − started_ns) ÷ 1,000,000,000 [초]. 135회 정상 완료 및 최댓값 18.0초 이하."
        note = "시작·종료 값은 원 시험의 단조시계 계측값(ns)이며 시각(KST)이 아닙니다."
        src = pkg / "analysis/measurements.csv"
        detail = table(["평가번호", "시나리오", "차수", "반복", "시작 계측값 [ns]", "종료 계측값 [ns]", "건별 처리시간 [초]", "정상 완료"],
                       ((r["evaluation_number"], r["scenario"], r["session"], r["repeat"], r["started_ns"], r["ended_ns"],
                         f"{(int(r['ended_ns'])-int(r['started_ns']))/1e9:.9f}", "정상" if r["functional_pass"] == "True" else "실패") for r in rows))
        detail += '<details><summary>Ego 단계별 처리시간 [ms]</summary>' + table(
            ["평가번호","시나리오","차수","반복","전처리","인지","증명 생성","송신","수신 대기","검증","병합"],
            ([r[key] for key in ("evaluation_number","scenario","session","repeat","preprocess_ms","inference_ms","protect_ms","send_ms","receive_wait_ms","verify_ms","fusion_ms")] for r in rows)) + "</details>"
        write_csv(out / "records.csv", rows)
        summary = [{"metric": 2, "evaluations": len(rows), "mean_processing_seconds": mean,
                    "max_processing_seconds": evaluation["representative_seconds"], "criterion_pass": evaluation["criterion_pass"]}]
        sources = snippet(pkg / "tools/verify_submission.py", "evaluate_measurements")
        passed = evaluation["criterion_pass"]
    else:
        name = "복합 모빌리티 환경 이상상황 탐지정확도"
        rows, samples = anchor_records(pkg)
        mean = math.fsum(r["ego_processing_seconds"] for r in samples) / len(samples)
        stats = [("정상 평가", f"{verified['evaluated_cases']} / {verified['planned_cases']}건"),
                 ("성공 / 탐지 실패", f"{verified['successful_cases']} / {verified['failed_detection_cases']}건"),
                 ("탐지 성공률", f"{verified['success_rate_percent']:g}%"), ("실행 오류", f"{verified['execution_error_cases']}건"),
                 ("전체 처리시점 평균 처리시간", f"{mean:.9f}초")]
        formula = "목표 영역과 겹치는 8방향 연결 장애물 셀 ≥ 3. 센서 수집 시각 기준 0.5초 이상 연속 탐지하면 앵커 성공. 성공 앵커 수 ÷ 100 × 100 ≥ 70%."
        note = f"판정 시점 {len(samples)}개. 독립 평가는 앵커 100건입니다. 건별 평균은 해당 앵커의 기록된 Ego elapsed_ms 평균, 전체 평균은 {len(samples)}개 처리시점 전체 평균입니다. 연속 탐지시간과 다른 지표이며 성공률 판정에는 사용하지 않습니다."
        detail = table(["평가번호","앵커 ID","상황","처리 시점","유효 시점","건별 평균 처리시간 [초]","최장 연속 탐지 [초]","판정"],
                       ((r["evaluation_number"], r["case_id"], r["scenario"], r["samples"], r["eligible_samples"], f"{r['mean_processing_seconds']:.9f}", f"{int(r['fused_longest_span_ns'])/1e9:.9f}", "성공" if r["successful"] else "탐지 실패") for r in rows))
        src = pkg / "anchor_cases.csv"
        shutil.copyfile(pkg / "frames.csv", out / "frames.csv")
        detail += '<p><a href="frames.csv" download>시점별 탐지 기록 CSV</a></p>'
        sources = snippet(pkg / "tools/verify_metric3_100.py", "independent_duration") + snippet(pkg / "tools/verify_metric3_retest.py", "verify_retest")
        fields = ("evaluation_number", "case_id", "scenario", "status", "successful", "samples", "eligible_samples", "mean_processing_seconds", "fused_longest_span_seconds")
        write_csv(out / "records.csv", [{k:r[k] for k in fields} for r in rows])
        write_csv(out / "processing_samples.csv", samples)
        summary = [{"metric": 3, "evaluations": len(rows), "processing_samples": len(samples),
                    "mean_processing_seconds": mean, "successes": verified["successful_cases"],
                    "success_rate_percent": verified["success_rate_percent"], "criterion_pass": verified["criterion_pass"]}]
        passed = verified["criterion_pass"]
    write_csv(out / "summary.csv", summary)
    values = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k,v in stats)
    verdict = "기준 충족" if passed else "기준 미충족"
    html = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DAMOS · {metric}번 제출 데이터 평가</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#20333a;background:#fff;font:15px/1.55 system-ui,sans-serif}}main{{margin:auto;max-width:1380px;padding:30px 24px}}header{{border-bottom:2px solid #09796e}}small,dt{{color:#536b75}}h1{{font-size:23px;margin:6px 0 14px}}h2{{font-size:17px}}dl{{display:flex;flex-wrap:wrap;gap:20px 50px;margin:22px 0}}dd{{margin:3px 0;font-size:25px;font-weight:650}}.scroll{{overflow:auto;max-height:48vh;border-block:1px solid #cddcdb}}table{{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}}th,td{{padding:7px 12px;text-align:left;border-bottom:1px solid #e4eceb}}th{{position:sticky;top:0;background:#eff5f3}}summary{{cursor:pointer;padding:10px 0}}pre{{overflow:auto;background:#f4f7f8;padding:12px;font-size:12px}}a,b{{color:#12665b}}footer{{overflow-wrap:anywhere;color:#5d7078;font-size:12px;margin-top:20px}}</style>
<main><header><small>DAMOS / 제출 ZIP 데이터 평가 / 지표 {metric:02}</small><h1>{name}</h1></header>
<p>평가 ID: <code>{escape(execution['run_id'])}</code><br>재계산 시작(KST): {escape(execution['started_at'])}<br>검증 완료(KST): {escape(checked_at)}</p>
<p><b>{verdict}</b> · 메타데이터 재계산 / CARLA·인지모델 미실행</p><dl>{values}</dl>
<h2>판정 기준</h2><p>{formula}</p><p>{note}</p><h2>건별 판정 기록</h2>{detail}
<p><a href="records.csv" download>평가번호별 전체 기록 CSV</a> · <a href="summary.csv" download>전체 평균·집계 CSV</a> · <a href="verification.json">검증 결과 JSON</a> · <a href="execution.json">오늘 실행 기록</a></p>
<h2>오늘 가동 소스 SHA-256</h2>{source_table(execution)}<p><a href="source_hashes.csv" download>소스 SHA-256 CSV</a> · <a href="source_hashes.sha256" download>소스 사본 해시 목록</a></p>
<h2>제출 ZIP의 판정 코드</h2>{sources}<footer>재계산 시각 {escape(checked_at)} · 원본: {metric}번항목_메타데이터.zip<br>원본 ZIP SHA-256: {PACKAGES[metric][0]}</footer></main></html>"""
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"{verdict}: " + " · ".join(f"{k} {v}" for k,v in stats))


def main():
    parser = argparse.ArgumentParser(description="DAMOS 제출 ZIP 재계산")
    parser.add_argument("metric", type=int, choices=PACKAGES)
    parser.add_argument("--no-open", action="store_true", help="브라우저 없이 검증·저장")
    parser.add_argument("--prepare", action="store_true", help="원본 ZIP에서 검사용 폴더를 한 번 준비")
    parser.add_argument("--evidence", action="store_true", help="첫 화면 캡처 후 Enter로 검증 시작")
    args = parser.parse_args()
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    m = args.metric
    pkg = ROOT / "inputs" / f"metric{m}"
    if args.prepare:
        prepare(pkg, m)
        print(f"{m}번 검사용 폴더 준비 완료: {pkg}")
        return
    verifier = PACKAGES[m][1]
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    output = reports / f"metric{m}_{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
    output.mkdir()
    execution = {"run_id": output.name, "metric": m, "mode": "metadata_recalculation",
                 "ready_at": timestamp(), "status": "ready", "input_folder": str(pkg),
                 "source_files": capture_sources(m, pkg, output)}
    start_page(execution, output)
    (output / "execution.json").write_text(json.dumps(execution, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"평가 ID: {output.name}\n첫 화면: {output/'start.html'}", flush=True)
    if args.evidence:
        open_result(output / "start.html", args.no_open)
        input("첫 화면 캡처 후 Enter: ")
    started = time.monotonic()
    execution.update(started_at=timestamp(), status="running")
    try:
        check_sources(execution)
        check_input(pkg, m)
        print(f"[1/3] {m}번 사전 압축 해제본 확인: {pkg} · CARLA/GPU 미사용", flush=True)
        print("[2/3] 원본 기록 대조·산식 재계산", flush=True)
        proc = subprocess.run([sys.executable, "-E", "-s", "-B", str(pkg / "tools" / verifier), str(pkg)],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode:
            raise ValueError(proc.stderr.strip() or "ZIP 검증 실패")
        verified = json.loads(proc.stdout)
        if not verified.get("metadata_consistency_pass" if m == 2 else "verified"):
            raise ValueError("검증 완료 확인 불가")
        check_sources(execution)
        (output / "verification.json").write_text(json.dumps(verified, ensure_ascii=False, indent=2), encoding="utf-8")
        report(m, pkg, verified, output, timestamp(), execution)
        execution.update(status="complete", completed_at=timestamp(), recalculation_seconds=time.monotonic()-started)
    except BaseException as exc:
        execution.update(status="failed", completed_at=timestamp(), error=str(exc))
        raise
    finally:
        (output / "execution.json").write_text(json.dumps(execution, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3/3] 결과 저장: {output/'index.html'}\n검증·보고서 생성: {time.monotonic()-started:.2f}초", flush=True)
    open_result(output / "index.html", args.no_open)


if __name__ == "__main__":
    main()
