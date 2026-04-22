import subprocess, sys, shlex, pathlib

# 실행할 파일들을 여기에 지정 (원하는 것만!)
SELECTED = [
    "S_.scenic",
    "S_1.scenic"
]

# 공통 옵션 (필요에 맞게 수정)
COMMON_OPTS = [
    "--simulate",          # 시뮬레이션 모드
    "--count", "10",       # 각 시나리오를 10회 생성/실행
    # "--seed", "42",      # 재현성 필요하면 시드 고정
    # "--maxSteps", "1000" # 시뮬레이터에 따라 지원 옵션
]

def run_one(path: pathlib.Path):
    cmd = ["scenic", str(path), *COMMON_OPTS]
    print(">>", " ".join(shlex.quote(c) for c in cmd))
    # 실패 시에도 다음 항목으로 넘어가고, 종료코드만 표시
    try:
        res = subprocess.run(cmd, check=False)
        if res.returncode != 0:
            print(f"[WARN] {path.name} 종료코드 {res.returncode}")
    except FileNotFoundError:
        print("scenic 명령을 찾을 수 없습니다. Scenic 설치/환경변수 확인 필요.")
        sys.exit(1)

def main():
    root = pathlib.Path(".").resolve()
    for fname in SELECTED:
        p = (root / fname)
        if not p.exists():
            print(f"[SKIP] {fname} (파일 없음)")
            continue
        run_one(p)

if __name__ == "__main__":
    main()
