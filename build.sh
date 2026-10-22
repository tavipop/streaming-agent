#!/usr/bin/env bash
# Project task runner. Usage: ./build.sh <target> [args...]
set -euo pipefail

cd "$(dirname "$0")"

run_tests() {
    uv run pytest "$@"
}

run_app() {
    uv run streaming-agent "$@"
}

phoenix() {
    # Local trace UI + OTLP receiver on http://localhost:6006. To export to it, set in .env:
    #   OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:6006/v1/traces
    uvx --from arize-phoenix phoenix serve "$@"
}

format() {
    uv run ruff check --fix .
    uv run ruff format .
}

usage() {
    cat <<USAGE
Usage: $0 <target> [args...]

Targets:
  run_tests   Run offline unit tests (extra args go to pytest, e.g. -m live)
  run_app     Run the agent REPL
  format      Lint with autofix and format code (ruff)
  phoenix     Start a local Phoenix trace UI at http://localhost:6006
              (export to it: OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:6006/v1/traces)
USAGE
}

target="${1:-}"
[[ $# -gt 0 ]] && shift

case "$target" in
    run_tests|run_app|format|phoenix) "$target" "$@" ;;
    -h|--help|help|"") usage ;;
    *) echo "Unknown target: $target" >&2; usage >&2; exit 1 ;;
esac
