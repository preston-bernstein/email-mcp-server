#!/usr/bin/env bash
# check_desktop_stack.sh — is the email stack up on the desktop (10.0.0.243)?
#
# Via `ssh desktop-agent` (read-only), checks:
#   1. docker ps — containers protonmail-bridge, proton-email-mcp, and LibreChat*
#      (name/status/uptime)
#   2. ss -tln — listening TCP sockets for ports 1143 (IMAP), 1025 (SMTP),
#      3004 (MCP streamable-http)
#
# Runs no restarts, reads no secrets, changes nothing.
#
# Usage:
#   ./check_desktop_stack.sh
#   SSH_TARGET=desktop-agent ./check_desktop_stack.sh
#
# Exit code: 0 = both containers Up and all three ports listening, 1 otherwise.

set -uo pipefail

SSH_TARGET="${SSH_TARGET:-desktop-agent}"
overall=0

echo "== proton-email-mcp desktop stack check (via ssh $SSH_TARGET) =="
echo

if ! ssh -o ConnectTimeout=10 "$SSH_TARGET" true 2>/dev/null; then
  echo "FAIL: cannot ssh to '$SSH_TARGET'. Is the desktop up? Check ~/.ssh/config and key."
  echo "CONCLUSION: FAIL — no data gathered."
  exit 1
fi

echo "-- containers (docker ps) --"
ps_out="$(ssh "$SSH_TARGET" \
  "docker ps --format '{{.Names}}\t{{.Status}}' | grep -Ei 'proton|librechat' || true")"
if [ -n "$ps_out" ]; then
  printf '%s\n' "$ps_out"
else
  echo "(no matching containers running)"
fi
echo

for c in protonmail-bridge proton-email-mcp; do
  if printf '%s\n' "$ps_out" | grep -q "^$c[[:space:]].*Up"; then
    echo "PASS  container $c is Up"
  else
    echo "FAIL  container $c is NOT running"
    overall=1
  fi
done
# LibreChat is informational only — the email path works without it.
if printf '%s\n' "$ps_out" | grep -qi 'librechat'; then
  echo "INFO  LibreChat container(s) present (consumer of :3004, not required for MCP health)"
else
  echo "INFO  no LibreChat container running (only matters for LibreChat users)"
fi
echo

echo "-- listening ports (ss -tln) --"
ss_out="$(ssh "$SSH_TARGET" "ss -tln" 2>/dev/null)"
for spec in "1143:Bridge IMAP" "1025:Bridge SMTP" "3004:MCP streamable-http"; do
  port="${spec%%:*}"; label="${spec#*:}"
  if printf '%s\n' "$ss_out" | awk '{print $4}' | grep -Eq "[:.]${port}\$"; then
    echo "PASS  :$port listening ($label)"
  else
    echo "FAIL  :$port NOT listening ($label)"
    overall=1
  fi
done

echo
if [ "$overall" -eq 0 ]; then
  echo "CONCLUSION: PASS — desktop stack healthy (both containers Up, 1143/1025/3004 listening)."
else
  echo "CONCLUSION: FAIL — see failures above; route triage to proton-mcp-debugging-playbook."
fi
exit "$overall"
