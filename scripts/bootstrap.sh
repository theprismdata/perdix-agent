#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
pnpm install
(cd server && uv sync)
