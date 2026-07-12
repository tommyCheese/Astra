#!/usr/bin/env bash
set -euo pipefail
for runtime in python echarts; do
  image="${RUNTIME_REGISTRY:-local}/astra-runtime-${runtime}:${RUNTIME_VERSION:-0.1.0}"
  docker build --pull -t "$image" "runtimes/$runtime"
  docker image inspect "$image" --format '{{.Id}}'
  if command -v syft >/dev/null; then syft "$image" -o cyclonedx-json > "runtimes/$runtime/sbom.cdx.json"; fi
  if command -v trivy >/dev/null; then trivy image --exit-code 1 --severity HIGH,CRITICAL "$image"; fi
done
