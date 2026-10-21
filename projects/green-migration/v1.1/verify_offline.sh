#!/usr/bin/env bash
set -Eeuo pipefail
set +x
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
export PYTHONUTF8=1
if [[ -n "${PYTHON:-}" ]]; then py="$PYTHON"
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; assert sys.version_info >= (3, 8)' >/dev/null 2>&1; then py=python3
elif command -v python >/dev/null 2>&1 && python -c 'import sys; assert sys.version_info >= (3, 8)' >/dev/null 2>&1; then py=python
else echo 'BLOCKED: Python 3.8+ required for offline tests' >&2; exit 2
fi
for script in ./*.sh; do bash -n "$script"; done
echo BASH_SYNTAX=PASS
"$py" -m py_compile green_v11_image.py
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck --severity=warning ./*.sh
  echo SHELLCHECK=PASS
else
  echo SHELLCHECK=NOT_RUN_TOOL_UNAVAILABLE
fi
if command -v groovyc >/dev/null 2>&1; then
  temp="$(mktemp -d)"
  trap 'rm -rf -- "$temp"' EXIT
  groovyc -d "$temp" Jenkinsfile_green_migration_tool.groovy
  echo GROOVY_SYNTAX=PASS
else
  echo GROOVY_SYNTAX=NOT_RUN_COMPILER_UNAVAILABLE
fi
echo JENKINS_DECLARATIVE_RUNTIME=NOT_RUN
if [[ -f SHA256SUMS ]]; then sha256sum -c SHA256SUMS; fi
"$py" tests/run_matrix.py "$@"

