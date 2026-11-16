#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
root="$PWD"
if [[ ${1:-} == --metadata ]]; then
  shift
  exec /usr/bin/python3 "$root/../../metadata/evaluate.py" 2 "$@"
fi
if [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  echo 'Goosebumps PC에 vv로 로그인한 뒤 데스크톱 터미널에서 실행하세요.' >&2
  exit 1
fi
source "$root/env.sh"
exec "$DAMOS_PYTHON" "$root/app/launch_local.py" --root "$root" "$@"
