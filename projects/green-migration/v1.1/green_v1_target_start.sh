#!/usr/bin/env bash
# Safe compatibility entry: process control is owned only by the lifecycle helper.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
op="${1:-check}"
(( $# == 0 )) || shift
case "$op" in
  check|target-check) exec bash "$HERE/green_v1_services.sh" target-check "$@" ;;
  resume|target-resume) exec bash "$HERE/green_v1_services.sh" target-resume "$@" ;;
  complete|target-complete) exec bash "$HERE/green_v1_services.sh" target-complete "$@" ;;
  *) printf '%s\n' 'Use check, resume TOKEN [--desired-file FILE], or complete TOKEN after the controlled restore workflow.' >&2; exit 2 ;;
esac
