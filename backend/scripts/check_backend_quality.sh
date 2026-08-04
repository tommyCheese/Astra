#!/usr/bin/env bash
set -euo pipefail

backend_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_root="$(cd "$backend_root/.." && pwd)"
python_command="${PYTHON:-python}"
openspec_change="${OPENSPEC_CHANGE:-refactor-backend-for-readability}"

cd "$backend_root"
"$python_command" scripts/check_backend_architecture.py
"$python_command" -m ruff check app benchmarks tests scripts
"$python_command" -m pytest -q \
  tests/test_refactoring_contracts.py \
  tests/test_runtime_events.py \
  tests/test_approvals.py

cd "$project_root"
openspec validate "$openspec_change" --type change --strict --no-interactive
