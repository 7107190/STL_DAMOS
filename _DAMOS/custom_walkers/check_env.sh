#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAMOS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_ROOT="$(cd "$DAMOS_ROOT/.." && pwd)"
WORKSPACE_ROOT="$(cd "$SOURCE_ROOT/.." && pwd)"
PACKAGE_ROOT="$WORKSPACE_ROOT/Carla-0.9.16"

echo "== Paths =="
for path in \
  "$PACKAGE_ROOT" \
  "$SOURCE_ROOT" \
  "$DAMOS_ROOT" \
  "$DAMOS_ROOT/3d_model/delivery-bot-by-glowbox/source/DeliveryBot.fbx" \
  "$DAMOS_ROOT/3d_model/mixamo-bot-character-lowpoly.zip"
do
  if [ -e "$path" ]; then
    echo "OK   $path"
  else
    echo "MISS $path"
  fi
done

echo
echo "== Build Tools =="
for tool in git g++-12 cmake ninja python3 unzip; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "OK   $tool -> $(command -v "$tool")"
  else
    echo "MISS $tool"
  fi
done

echo
echo "== Unreal Access =="
echo "Check manually:"
echo "  git ls-remote git@github.com:CarlaUnreal/UnrealEngine.git HEAD"

echo
echo "== Wheelchair Runtime Test =="
echo "Use:"
echo "  conda activate carla4"
echo "  python $DAMOS_ROOT/scripts/spawn_wheelchair_pedestrian.py"
