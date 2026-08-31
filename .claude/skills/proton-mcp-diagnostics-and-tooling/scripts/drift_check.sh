#!/usr/bin/env bash
# drift_check.sh — detect source drift across the three copies of proton-email-mcp.
#
# Compares sha256 of proton_email_server.py, Dockerfile, requirements.txt across:
#   1. Mac git repo (canonical):  $MAC_REPO
#   2. Desktop copy A:            $DESKTOP_A
#   3. Desktop copy B:            $DESKTOP_B
#
# Desktop hashes are read over `ssh $PROTON_MCP_SSH_HOST sudo sha256sum` (read-only).
# Prints per-file MATCH/DRIFT verdicts against the Mac canonical copy.
#
# Host config: this script never hardcodes a real SSH alias/host. It defaults
# to loopback (127.0.0.1, which will simply fail to ssh — a safe no-op) and
# reads your real deployment target from a repo-root .env (gitignored, never
# committed — see .env.example) via PROTON_MCP_SSH_HOST, or an explicit
# override on the command line.
#
# Usage:
#   ./drift_check.sh                                    # defaults above
#   PROTON_MCP_SSH_HOST=<your-desktop-ssh-alias> ./drift_check.sh
#   SSH_TARGET=<your-desktop-ssh-alias> ./drift_check.sh
#
# Exit code: 0 = all files match everywhere, 1 = drift or missing files.

set -uo pipefail

# Load repo-root .env if present (gitignored; holds your real deployment values).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${PROTON_MCP_ENV_FILE:-$SCRIPT_DIR/../../../../.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

MAC_REPO="${MAC_REPO:-/Users/prestonbernstein/dev/proton-email-mcp}"
DESKTOP_A="${DESKTOP_A:-/home/preston/docker/proton-email-mcp}"
DESKTOP_B="${DESKTOP_B:-/opt/docker/librechat-stack/proton-email-mcp}"
SSH_TARGET="${SSH_TARGET:-${PROTON_MCP_SSH_HOST:-127.0.0.1}}"
FILES=(proton_email_server.py Dockerfile requirements.txt)

overall=0

mac_hash() {  # $1 = relative file
  if [ -f "$MAC_REPO/$1" ]; then
    shasum -a 256 "$MAC_REPO/$1" 2>/dev/null | awk '{print $1}'
  else
    echo "MISSING"
  fi
}

remote_hash() {  # $1 = absolute remote path
  local out
  out="$(ssh -o ConnectTimeout=10 "$SSH_TARGET" "sudo sha256sum '$1'" 2>/dev/null | awk '{print $1}')"
  if [ -n "$out" ]; then echo "$out"; else echo "MISSING"; fi
}

short() { case "$1" in MISSING) echo "MISSING";; *) echo "${1:0:12}";; esac; }

echo "== proton-email-mcp source drift check =="
echo "Canonical: $MAC_REPO (Mac git repo)"
echo "Desktop A: $DESKTOP_A  Desktop B: $DESKTOP_B  (via ssh $SSH_TARGET)"
echo

if ! ssh -o ConnectTimeout=10 "$SSH_TARGET" true 2>/dev/null; then
  echo "FAIL: cannot ssh to '$SSH_TARGET' — check SSH config/key, and set PROTON_MCP_SSH_HOST"
  echo "  in a repo-root .env (see .env.example). No drift data gathered."
  exit 1
fi

printf '%-25s %-14s %-14s %-14s %s\n' "FILE" "MAC(canon)" "DESKTOP_A" "DESKTOP_B" "VERDICT"
for f in "${FILES[@]}"; do
  h_mac="$(mac_hash "$f")"
  h_a="$(remote_hash "$DESKTOP_A/$f")"
  h_b="$(remote_hash "$DESKTOP_B/$f")"

  if [ "$h_mac" = "MISSING" ]; then
    verdict="ERROR: missing in canonical repo"
    overall=1
  elif [ "$h_a" = "$h_mac" ] && [ "$h_b" = "$h_mac" ]; then
    verdict="MATCH (all 3 identical)"
  else
    verdict="DRIFT:"
    [ "$h_a" != "$h_mac" ] && verdict="$verdict desktop_A${h_a:+ differs}"
    [ "$h_a" = "MISSING" ] && verdict="$verdict(missing)"
    [ "$h_b" != "$h_mac" ] && verdict="$verdict desktop_B differs"
    [ "$h_b" = "MISSING" ] && verdict="$verdict(missing)"
    overall=1
  fi
  printf '%-25s %-14s %-14s %-14s %s\n' "$f" "$(short "$h_mac")" "$(short "$h_a")" "$(short "$h_b")" "$verdict"
done

echo
if [ "$overall" -eq 0 ]; then
  echo "CONCLUSION: PASS — no source drift; all three copies identical."
else
  echo "CONCLUSION: FAIL — drift detected. Canonical is the Mac git repo; fixes go through"
  echo "the proton-mcp-drift-and-hardening-campaign skill (do NOT hand-edit desktop copies)."
fi
exit "$overall"
