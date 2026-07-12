#!/usr/bin/env bash
set -euo pipefail
engine="${SANDBOX_EXECUTOR:-docker}"
"$engine" info >/dev/null
if [[ "${SANDBOX_REQUIRE_GVISOR:-false}" == "true" ]]; then
  "$engine" info --format '{{json .Runtimes}}' | grep -q 'runsc'
fi
for image in "${SANDBOX_PYTHON_IMAGE:-astra-runtime-python:0.1.0}" "${SANDBOX_ECHARTS_IMAGE:-astra-runtime-echarts:0.1.0}"; do
  "$engine" image inspect "$image" --format '{{index .RepoDigests 0}}'
done
