#!/usr/bin/env python3
"""smoke_imap_readonly.py — read-only IMAP integration check against Proton Bridge.

Connects to the Bridge IMAP port, logs in, SELECTs a folder READ-ONLY,
prints the message count, and logs out. Sends nothing, modifies nothing,
never marks messages read (readonly SELECT), never prints the password.

Host config: this script never hardcodes a real LAN address. It defaults to
loopback (127.0.0.1) and reads your real deployment host from a repo-root
.env (gitignored, never committed — see .env.example) via PROTON_MCP_HOST,
or PROTON_BRIDGE_HOST directly.

Env vars (credentials from the process environment only — sourced from
~/.claude.json mcpServers.proton-mail.env by the operator; never echo them):
    PROTON_USERNAME             required
    PROTON_PASSWORD             required (Bridge app password, NOT the Proton
                                account password)
    PROTON_BRIDGE_HOST          default: $PROTON_MCP_HOST, else 127.0.0.1
    PROTON_BRIDGE_IMAP_PORT     default 1143
    SMOKE_FOLDER                default INBOX

Usage:
    env PROTON_USERNAME=... PROTON_PASSWORD=... \
        python3 scripts/smoke_imap_readonly.py

Exit codes: 0 = PASS, 2 = credentials unset, 1 = any connection/login/select failure.
"""

import imaplib
import os
import socket
import sys
from pathlib import Path

TIMEOUT_SECONDS = 15


def load_repo_root_env() -> None:
    """Load a gitignored repo-root .env (if present) into os.environ without
    overriding values already set in the real environment. No external
    dependency — this script must run standalone."""
    env_file = os.environ.get("PROTON_MCP_ENV_FILE")
    if env_file:
        candidate = Path(env_file)
    else:
        # scripts/ -> proton-mcp-diagnostics-and-tooling/ -> skills/ -> .claude/ -> repo root
        candidate = Path(__file__).resolve().parents[4] / ".env"
    if not candidate.is_file():
        return
    for line in candidate.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def fail(msg: str, code: int = 1) -> "None":
    print(f"FAIL: {msg}")
    print("CONCLUSION: FAIL — IMAP read-only smoke check did not complete.")
    sys.exit(code)


def main() -> None:
    load_repo_root_env()

    username = os.environ.get("PROTON_USERNAME", "")
    password = os.environ.get("PROTON_PASSWORD", "")
    host = os.environ.get(
        "PROTON_BRIDGE_HOST", os.environ.get("PROTON_MCP_HOST", "127.0.0.1")
    )
    port = int(os.environ.get("PROTON_BRIDGE_IMAP_PORT", "1143"))
    folder = os.environ.get("SMOKE_FOLDER", "INBOX")

    if not username or not password:
        fail(
            "PROTON_USERNAME and/or PROTON_PASSWORD not set in the environment.\n"
            "  Source them from ~/.claude.json (mcpServers.proton-mail.env) — "
            "do not paste values into files or chat.",
            code=2,
        )

    print(f"Connecting to {host}:{port} (plain IMAP, socket timeout {TIMEOUT_SECONDS}s)...")
    socket.setdefaulttimeout(TIMEOUT_SECONDS)
    try:
        mail = imaplib.IMAP4(host, port)
    except (OSError, imaplib.IMAP4.error) as e:
        fail(f"could not connect to {host}:{port}: {e}")

    mail.sock.settimeout(TIMEOUT_SECONDS)

    try:
        mail.login(username, password)
    except imaplib.IMAP4.error as e:
        fail(f"IMAP login rejected (bad Bridge app password, or Bridge not logged in): {e}")
    print("PASS: IMAP login accepted.")

    try:
        status, data = mail.select(folder, readonly=True)
    except imaplib.IMAP4.error as e:
        fail(f"SELECT {folder} raised: {e}")
    if status != "OK":
        fail(f"could not SELECT folder '{folder}' read-only (status {status}).")

    count = int(data[0]) if data and data[0] else 0
    print(f"PASS: selected '{folder}' read-only; {count} messages present.")

    try:
        mail.logout()
    except Exception:
        pass  # logout failure after a successful check is cosmetic

    print("CONCLUSION: PASS — Bridge IMAP end-to-end path (connect, auth, select) is healthy.")
    sys.exit(0)


if __name__ == "__main__":
    main()
