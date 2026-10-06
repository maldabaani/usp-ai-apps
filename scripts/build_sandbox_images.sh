#!/usr/bin/env bash
# Builds every DevCrew sandbox image, in the required dependency order.
# gatetools MUST build first: python/java/node/mixed all COPY --from it.
#
# Build context is the shared `sandbox/` directory (not each family's own
# subdirectory) for every image, including gatetools -- node/Dockerfile and
# mixed/Dockerfile both `COPY common/chromium-no-sandbox ...`, and Docker
# resolves COPY paths relative to the build context, not the Dockerfile's
# own directory. `common/` is a sibling of the per-family directories, so
# the context has to be their shared parent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SANDBOX_DIR="$SCRIPT_DIR/../usp-ai-ba/backend/devcrew_resources/sandbox"
PREFIX="${SANDBOX_IMAGE_PREFIX:-devcrew-sandbox}"

echo "==> Building ${PREFIX}-gate-tools"
docker build -t "${PREFIX}-gate-tools:latest" \
  -f "$SANDBOX_DIR/gatetools/Dockerfile" "$SANDBOX_DIR"

for family in python java node; do
  echo "==> Building ${PREFIX}-${family}"
  docker build \
    --build-arg "GATE_TOOLS_IMAGE=${PREFIX}-gate-tools:latest" \
    -t "${PREFIX}-${family}:latest" \
    -f "$SANDBOX_DIR/${family}/Dockerfile" \
    "$SANDBOX_DIR"
done

echo "==> Building ${PREFIX}-mixed"
docker build \
  --build-arg "GATE_TOOLS_IMAGE=${PREFIX}-gate-tools:latest" \
  -t "${PREFIX}-mixed:latest" \
  -f "$SANDBOX_DIR/mixed/Dockerfile" \
  "$SANDBOX_DIR"

echo "==> All sandbox images built: ${PREFIX}-gate-tools, ${PREFIX}-python, ${PREFIX}-java, ${PREFIX}-node, ${PREFIX}-mixed"
