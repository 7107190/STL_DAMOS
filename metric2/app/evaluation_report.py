"""Read-only aggregate and all-row evidence for a selected live execution."""
import csv
from datetime import datetime
import html
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo


def escape(value):
    return html.escape(str(value if value is not None else "미기록"), quote=True)


def fixed(value, places=9):
    return f"{float(value):.{places}f}" if value is not None else "미기록"


def read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def kst(value):
    if not value:
        return "미기록"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Seoul")).isoformat(timespec="milliseconds")


def aggregate(metric, state):
    if metric == 2:
        rows = state["rows"]
        return {"metric": 2, "run_id": state["id"], "status": state["status"],
                "planned": state["expected"], "processed": state["processed"], "normal": state["normal"],
                "mean_processing_seconds": state["mean_seconds"], "maximum_processing_seconds": state["max_seconds"],
                "over_18_seconds": sum(float(r["elapsed_ms"]) > 18000 for r in rows),
                "all_completed": state["normal"] == state["expected"] and state["status"] == "complete"}
    if metric == 3:
        return {"metric": 3, "run_id": state["id"], "status": state["status"],
                "planned": state["planned"], "evaluated": state["evaluated"], "successful": state["successful"],
                "detection_failures": state["detection_failures"], "execution_errors": state["execution_errors"],
                "success_rate_percent": state["success_rate_percent"],
                "mean_continuous_detection_seconds": state["mean_continuous_detection_seconds"],
                "all_completed": state["all_cases_complete"] and state["status"] == "complete"}
    raise ValueError("Unsupported metric")


def summary_csv(metric, state):
    data = aggregate(metric, state)
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(data))
    writer.writeheader()
    writer.writerow(data)
    return stream.getvalue().encode("utf-8-sig")


def render_report(metric, state, root, view):
    if view not in ("summary", "records"):
        raise ValueError("Unsupported report view")
    root = Path(root)
    run = state["id"]
    # The controller validates the run identifier before reaching this renderer.
    folder = root / "reports" / run
    environment = read(folder / "environment.json")
    job = root / (".metric2_ui_jobs" if metric == 2 else "jobs") / run
    job_state = read(job / "state.json")
    protocol = read(folder / "protocol.json") or state["protocol"]
    data = aggregate(metric, state)
    title = "개별 모빌리티 시스템 시뮬레이션 처리속도" if metric == 2 else "복합 모빌리티 이상상황 탐지정확도"
    heading = "전체 집계 결과" if view == "summary" else "전체 건별 평가 기록"
    query = urlencode({"run": run})
    hashes = environment.get("measured_code_sha256", {}) if metric == 2 else protocol.get("source_hashes", {})
    names = {"run_live.py", "bev_mesh.py", "report_data.py", "server.py", "m3_run.py", "m3_rules.py", "m3_stream.py", "m3_server.py", "evaluation_report.py"}
    hash_rows = "".join(f"<tr><td>{escape(Path(p).name)}</td><td class='digest'>{escape(h)}</td></tr>" for p,h in sorted(hashes.items()) if Path(p).name in names)
    started = job_state.get("started_utc") or protocol.get("created_utc")
    finished = job_state.get("ended_utc") or job_state.get("ended_at")
    renderer_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    status = {"complete": "완료", "running": "실행 중", "failed": "실행 오류", "cancelled": "중단"}.get(state["status"], state["status"])
    if metric == 2:
        stats = [("정상 완료", f"{state['normal']} / {state['expected']}회"),
                 ("전체 평균 처리시간", fixed(state["mean_seconds"]) + "초"),
                 ("Ego 최대 처리시간", fixed(state["max_seconds"]) + "초"),
                 ("18초 초과", f"{data['over_18_seconds']}회")]
        rule = "T = (ended_ns − started_ns) ÷ 1,000,000,000 [초]. 대표값: 정상 처리시간 최댓값. 기준: 모든 평가 정상 완료 및 최대 18초 이내."
        scope = f"이번 실행 {state['expected']}회. " + ("S1~S9 × 3차수 × 새 입력 5회." if state['full_scope'] else "누적 시험 실행. 전체 135회 시험과 구분.")
        columns = ["평가번호", "시나리오", "차수", "반복", "시작 시각 [KST]", "종료 시각 [KST]", "처리시간 [초]", "판정"]
        rows = [[r["evaluation_number"], r["scenario"], r["session"], r["repeat"], r.get("start_kst"), r.get("end_kst"),
                 fixed(r["elapsed_ms"] / 1000), "정상" if r["status"] == "passed" and r["audit_pass"] else r["status"]]
                for r in state["rows"]]
        measurements = f"현재 원시 센서 입력 준비 후 IPC 전달 직전 → 전처리·인지·증명 생성·송수신·검증 → Ego BEV 병합 완료. 센서 취득 대기·모델 초기화·증빙 저장·사후 검증·경로계획 제외. 실제 증명 재검증 {state['audited'] * 2}개."
    else:
        stats = [("정상 평가", f"{state['evaluated']} / {state['planned']}건"),
                 ("탐지 성공률", fixed(state["success_rate_percent"], 3) + "%"),
                 ("성공 / 탐지 실패", f"{state['successful']} / {state['detection_failures']}건"),
                 ("실행 오류", f"{state['execution_errors']}건")]
        rule = "성공률 = 성공 앵커 수 ÷ 이번 실행의 전체 앵커 수 × 100 [%]. 목표 위치와 겹치는 예측 장애물 셀 3개 이상 연결, 수집 시각 기준 0.5초 이상 연속 탐지 시 앵커 1건 성공."
        scope = f"이번 실행 앵커 {state['planned']}건. " + ("전체 기준: 100건 정상 평가 및 성공률 70% 이상." if state['planned'] == 100 else "부분 시험 성공률. 전체 100건 시험과 구분.")
        columns = ["평가번호", "시나리오", "앵커 ID", "날씨", "처리 시점 수", "연속 탐지시간 [초]", "판정"]
        names_status = {"success": "성공", "failure": "탐지 실패", "execution_error": "실행 오류", "not_run": "대기", "not_evaluable": "평가 불가"}
        rows = [[r["evaluation_number"], r["scenario"], r["case_id"], r["weather"], r.get("samples"),
                 fixed(r.get("fused_longest_span_seconds")), names_status.get(r["status"], r["status"])] for r in state["cases"]]
        measurements = "D = (연속 구간 마지막 capture_sim_ns − 첫 capture_sim_ns) ÷ 1,000,000,000 [초]. 미탐지·누락 시 연속 구간 종료. 평균 연속 탐지시간: " + fixed(state["mean_continuous_detection_seconds"]) + "초."
    cells = "".join(f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>" for label,value in stats)
    table = "<table id='allRecords'><thead><tr>" + "".join(f"<th>{escape(c)}</th>" for c in columns) + "</tr></thead><tbody>"
    table += "".join("<tr data-evaluation='" + str(row[0]) + "'>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>" for row in rows) + "</tbody></table>"
    content = f"<section><h2>전체 건별 결과 · {len(rows)}건</h2>{table}<p>평가번호 1~{len(rows)} · 모든 행 표시</p></section>" if view == "records" else f"<section><h2>집계 범위</h2><p>{escape(scope)}</p><p>상태: {escape(status)} · 정상 완료 여부: {'완료' if data['all_completed'] else '진행 중 또는 오류'}</p><p>건별 결과 {len(rows)}건 · <a href='report?{query}&amp;view=records'>전체 기록 보기</a></p></section>"
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>DAMOS 지표 {metric:02d} · {heading}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:white;color:#20343e;font:15px/1.65 Arial,'Noto Sans CJK KR',sans-serif;letter-spacing:0}}main{{max-width:1440px;margin:0 auto;padding:30px 44px}}header{{border-bottom:2px solid #11877f;padding-bottom:16px}}h1{{font-size:25px;margin:6px 0}}h2{{font-size:18px;margin:0 0 12px}}.eyebrow{{color:#587681;font-size:13px;margin:0}}.identity{{margin:18px 0;overflow-wrap:anywhere}}.identity p{{margin:2px 0}}nav{{display:flex;gap:22px;flex-wrap:wrap;margin:12px 0}}a{{color:#137c77}}.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-block:1px solid #dce5e7;padding:20px 0;gap:20px}}.stats span{{display:block;color:#527280}}.stats strong{{display:block;font-size:26px;line-height:1.4;overflow-wrap:anywhere}}section{{margin:25px 0}}p{{margin:8px 0}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:10px 9px;text-align:left;border-bottom:1px solid #dce5e7}}th{{background:#edf4f3;font-weight:700}}#allRecords td{{white-space:nowrap}}.digest{{font:12px/1.6 monospace;overflow-wrap:anywhere}}.model{{overflow-wrap:anywhere}}footer{{border-top:1px solid #dce5e7;padding-top:12px;color:#627e89;font-size:12px;overflow-wrap:anywhere}}@media(max-width:900px){{main{{padding:20px}}.stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}.stats strong{{font-size:22px}}#allRecords td{{white-space:normal}}}}
</style></head><body><main data-run='{escape(run)}' data-metric='{metric}' data-view='{view}'>
<header><p class='eyebrow'>DAMOS / CARLA 실구동 시험 / 지표 {metric:02d}</p><h1>{title}</h1><h2>{heading}</h2></header>
<div class='identity'><p>평가 ID: <strong>{escape(run)}</strong></p><p>실행 시작 [KST]: {escape(kst(started))}</p><p>실행 종료 [KST]: {escape(kst(finished))}</p><p>실행 서버: {escape(environment.get('hostname'))} · vv · 상태: {escape(status)}</p></div>
<nav><a href='index.html?{query}'>CARLA·BEV 실행 화면</a><a href='report?{query}&amp;view=summary'>전체 집계</a><a href='report?{query}&amp;view=records'>전체 건별 기록</a><a href='api/download?{query}&amp;format=csv'>건별 CSV</a><a href='api/summary-csv?{query}'>집계 CSV</a></nav>
<div class='stats'>{cells}</div>{content}<section><h2>평가 기준·측정 범위</h2><p>{escape(rule)}</p><p>{escape(measurements)}</p></section>
<section><h2>실행 소스 SHA-256</h2><table><thead><tr><th>소스코드</th><th>SHA-256</th></tr></thead><tbody>{hash_rows}</tbody></table><p class='model'>AI 모델 SHA-256: <span class='digest'>{escape(protocol.get('checkpoint_sha256'))}</span></p></section>
<footer>시험 기록: {escape(folder)}<br>RGB 6방향 × 4프레임 + 현재 LiDAR 1프레임 / 독립 노드 인지·Groth16·TCP·Ego BEV 병합<br>집계 화면 표시 코드 SHA-256: {renderer_digest}</footer>
</main></body></html>"""
