#!/bin/bash
# Perdix 판 Langflow 화면을 빌드한다. Langflow 소스는 저장소에 넣지 않고, 고정한 버전의 태그를 따로 받아
# overrides/의 파일만 덮어쓴 뒤 프런트엔드만 빌드한다. 백엔드는 PyPI의 같은 버전을 그대로 쓴다.
#
#   build.sh            ~/.perdix/langflow-ui/build 에 결과를 만든다
#   LANGFLOW_SOURCE=<저장소 주소 또는 경로>  기본은 https://github.com/langflow-ai/langflow
#   PERDIX_LANGFLOW_UI_WORK=<작업 폴더>      기본은 ~/.perdix/langflow-ui
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$HERE/LANGFLOW_VERSION")"
WORK="${PERDIX_LANGFLOW_UI_WORK:-$HOME/.perdix/langflow-ui}"
SOURCE="${LANGFLOW_SOURCE:-https://github.com/langflow-ai/langflow}"
CHECKOUT="$WORK/src"
FRONTEND="$CHECKOUT/src/frontend"

mkdir -p "$WORK"
if [ ! -d "$CHECKOUT/.git" ]; then
  git clone --quiet --depth 1 --branch "v$VERSION" "$SOURCE" "$CHECKOUT"
fi

# 받아 둔 소스가 고정한 버전의 태그인지 확인한다(백엔드와 어긋난 화면은 쓰지 않는다).
ACTUAL="$(git -C "$CHECKOUT" describe --tags --exact-match 2>/dev/null || true)"
if [ "$ACTUAL" != "v$VERSION" ]; then
  echo "build.sh: $CHECKOUT 가 v$VERSION 이 아닙니다(현재: ${ACTUAL:-태그 없음}). 폴더를 지우고 다시 실행하세요." >&2
  exit 1
fi

# 덮어쓰기를 되돌린 뒤 다시 적용해 같은 결과가 나오게 한다.
git -C "$CHECKOUT" checkout --quiet -- src/frontend/src/customization
cp -R "$HERE/overrides/." "$FRONTEND/src/"

cd "$FRONTEND"
npm ci --no-audit --no-fund
npm run build

mkdir -p "$WORK/build"
rsync -a --delete "$FRONTEND/build/" "$WORK/build/"
echo "$VERSION" > "$WORK/build/PERDIX_LANGFLOW_UI_VERSION"
echo "빌드 완료: $WORK/build  (langflow run --frontend-path $WORK/build)"
