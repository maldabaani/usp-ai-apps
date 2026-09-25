#!/usr/bin/env bash
# Build the DevCrew sandbox images. Extra args are passed to `docker build`, e.g.
#   scripts/build_sandbox_images.sh --build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.12-slim
# Usage: build_sandbox_images.sh [python|java|node|mixed ...] [-- docker build args]
set -euo pipefail
cd "$(dirname "$0")/../sandbox"
PREFIX="${SANDBOX_IMAGE_PREFIX:-devcrew-sandbox}"

stacks=()
while [[ $# -gt 0 && "$1" != "--" ]]; do stacks+=("$1"); shift; done
[[ "${1:-}" == "--" ]] && shift
[[ ${#stacks[@]} -eq 0 ]] && stacks=(python java node mixed)

for stack in "${stacks[@]}"; do
  echo ">> building ${PREFIX}-${stack}"
  docker build -f "${stack}/Dockerfile" -t "${PREFIX}-${stack}:latest" "$@" .
done
