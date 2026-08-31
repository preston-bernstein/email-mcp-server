#!/usr/bin/env bash
# check_connectivity.sh — probe Proton Bridge (IMAP/SMTP) and the MCP HTTP endpoint.
#
# Measures, from THIS machine:
#   1. TCP reachability of BRIDGE_HOST:IMAP_PORT   (Proton Bridge IMAP, default 1143)
#   2. TCP reachability of BRIDGE_HOST:SMTP_PORT   (Proton Bridge SMTP, default 1025)
#   3. TCP reachability of MCP_HOST:MCP_PORT       (proton-email-mcp container, default 3004)
#   4. HTTP liveness of http://MCP_HOST:MCP_PORT/mcp (streamable-http transport).
#      A GET is the "wrong" verb for MCP (it expects POSTed JSON-RPC), so ANY HTTP
#      status code — including 4xx — proves the server process is alive and parsing
#      HTTP. Only "connection refused"/timeout means DOWN.
#
# Read-only: sends no email, performs no IMAP/SMTP login, needs no credentials.
#
# Host config: this script never hardcodes a real LAN address. It defaults to
# loopback (127.0.0.1) and reads your real deployment host from a repo-root
# .env (gitignored, never committed — see .env.example) via PROTON_MCP_HOST,
# or from an explicit env var override on the command line.
#
# Usage:
#   ./check_connectivity.sh                          # loopback defaults
#   PROTON_MCP_HOST=<your-desktop-host> ./check_connectivity.sh
#   BRIDGE_HOST=127.0.0.1 ./check_connectivity.sh     # e.g. from the desktop itself
#
# Exit code: 0 if all probes pass, 1 otherwise.

set -uo pipefail   # no -e: probe failures are data, not script errors

# Load repo-root .env if present (gitignored; holds your real deployment values).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${PROTON_MCP_ENV_FILE:-$SCRIPT_DIR/../../../../.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

BRIDGE_HOST="${BRIDGE_HOST:-${PROTON_MCP_HOST:-127.0.0.1}}"
IMAP_PORT="${IMAP_PORT:-1143}"
SMTP_PORT="${SMTP_PORT:-1025}"
MCP_HOST="${MCP_HOST:-$BRIDGE_HOST}"
MCP_PORT="${MCP_PORT:-3004}"
NC_TIMEOUT="${NC_TIMEOUT:-3}"

overall=0

probe_tcp() {
  local label="$1" host="$2" port="$3"
  if nc -z -w "$NC_TIMEOUT" "$host" "$port" >/dev/null 2>&1; then
    printf 'PASS  %-28s %s:%s open\n' "$label" "$host" "$port"
  else
    printf 'FAIL  %-28s %s:%s unreachable (refused or timeout %ss)\n' \
      "$label" "$host" "$port" "$NC_TIMEOUT"
    overall=1
  fi
}

echo "== proton-email-mcp connectivity check =="
echo "Bridge host: $BRIDGE_HOST | MCP host: $MCP_HOST"
echo

probe_tcp "Bridge IMAP" "$BRIDGE_HOST" "$IMAP_PORT"
probe_tcp "Bridge SMTP" "$BRIDGE_HOST" "$SMTP_PORT"
probe_tcp "MCP streamable-http" "$MCP_HOST" "$MCP_PORT"

# HTTP liveness: any status code = process alive; connect failure = down.
url="http://$MCP_HOST:$MCP_PORT/mcp"
http_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null)" || http_code="000"
if [ "$http_code" = "000" ]; then
  printf 'FAIL  %-28s %s no HTTP response (connection refused/timeout)\n' "MCP HTTP liveness" "$url"
  overall=1
else
  printf 'PASS  %-28s %s answered HTTP %s (any status = server ALIVE; 4xx expected for GET)\n' \
    "MCP HTTP liveness" "$url" "$http_code"
fi

echo
if [ "$overall" -eq 0 ]; then
  echo "CONCLUSION: PASS — Bridge IMAP/SMTP reachable and MCP server alive."
else
  echo "CONCLUSION: FAIL — see failed probes above; use the interpretation table in SKILL.md."
fi
exit "$overall"
