#!/usr/bin/env bash
# Validation gate for agent-written Dockerfiles and Compose files. Pack: docker, rule docker-one-gate-command.
#
#   docker-validate.sh [ROOT]
#
# Order: docker-check.py (digests, USER, layers, secrets, restart, healthcheck, limits) -> hadolint (Dockerfiles)
#        -> trivy config (misconfiguration scan; Trivy does not scan Compose files).
# Every tool is checked before any step runs. A missing tool fails the gate with exit 127. Nothing is skipped.
# Pin Trivy by version and verify its provenance: the March 2026 compromise poisoned mutable tags.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-.}"
cd "$ROOT"

fail() { echo "docker-validate: $*" >&2; exit "${2:-1}"; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing tool: $1 (the gate never skips a step)" 127; }

prune=( \( -name node_modules -o -name vendor -o -name '.?*' \) -prune -o )
dockerfiles=()
while IFS= read -r f; do dockerfiles+=("$f"); done < <(
  find . -mindepth 1 "${prune[@]}" -type f \( -name 'Dockerfile' -o -name 'Dockerfile.*' -o -name '*.Dockerfile' -o -name '*.dockerfile' \) -print | sort)
composes=()
while IFS= read -r f; do composes+=("$f"); done < <(
  find . -mindepth 1 "${prune[@]}" -type f \( -name 'docker-compose*.yml' -o -name 'docker-compose*.yaml' -o -name 'compose*.yml' -o -name 'compose*.yaml' \) -print | sort)

if [ "${#dockerfiles[@]}" -eq 0 ] && [ "${#composes[@]}" -eq 0 ]; then
  echo "docker-validate: no Dockerfiles or Compose files, nothing to check"
  exit 0
fi

need python3
need trivy
[ "${#dockerfiles[@]}" -eq 0 ] || need hadolint
[ "${#composes[@]}" -eq 0 ] || need docker
[ -f "$HERE/docker-check.py" ] || fail "docker-check.py not found next to this script" 2

python3 "$HERE/docker-check.py" .
if [ "${#dockerfiles[@]}" -gt 0 ]; then hadolint "${dockerfiles[@]}"; fi
trivy config --severity HIGH,CRITICAL --exit-code 1 .
echo "docker-validate: all checks passed"
