#!/usr/bin/env bash
# Test-only adapter. Do not install with runtime files. Production has no fixture flag.
set -Eeuo pipefail
FIXTURE_TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TOOL:-$FIXTURE_TEST_DIR/../green_migration_tool.sh}"
eval "$(declare -f v11_validate | sed '1s/v11_validate/v11_runtime_validate/')"
v11_validate() {
  local requested_mode=$TARGET_MODE
  if [[ $TARGET_MODE == fixture ]]; then TARGET_MODE=offline; fi
  v11_runtime_validate
  TARGET_MODE=$requested_mode
}
v11_python() {
  "${GREEN_MIG_PYTHON:-python3}" "$FIXTURE_TEST_DIR/fixture_driver.py" "$@"
}
main "$@"
