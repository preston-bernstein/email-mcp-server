# proton-email-mcp

MCP server exposing Proton Mail (via Proton Bridge) as tools for an LLM client. Speaks IMAP/SMTP to a locally running Proton Bridge — no direct Proton API calls.

[![CI](https://github.com/preston-bernstein/proton-email-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/preston-bernstein/proton-email-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Tools

| Tool | Description |
|---|---|
| `read_recent_emails(count, folder)` | Read the N most recent emails from a folder (max 50) |
| `search_emails(query, folder, max_results)` | IMAP `TEXT` search (falls back to `SUBJECT`), sanitized query |
| `send_email(to_email, subject, body, cc, bcc)` | Send via SMTP with STARTTLS |
| `list_folders()` | List mailbox folders |
| `get_email_stats(folder)` | Total / unread / last-7-days counts for a folder |

## Transports

Two transports, selected by `MCP_TRANSPORT`:

- `stdio` (default) — for Claude Code and other stdio-based MCP clients
- `streamable-http` — for LibreChat and other HTTP-based MCP clients; serves on `MCP_HOST:MCP_PORT` (default `0.0.0.0:3004`)

## Requirements

- Python 3.12
- [Proton Bridge](https://proton.me/mail/bridge) running locally (or reachable), with IMAP/SMTP enabled and a bridge app password generated

## Configuration

Copy [`.env.example`](.env.example) to `.env` and fill in your Bridge credentials:

| Variable | Default | Description |
|---|---|---|
| `PROTON_USERNAME` | — | Proton Mail address |
| `PROTON_PASSWORD` | — | Proton Bridge app password (not your Proton account password) |
| `PROTON_BRIDGE_HOST` | `127.0.0.1` | Host where Proton Bridge is running |
| `PROTON_BRIDGE_IMAP_PORT` | `1143` | Bridge IMAP port |
| `PROTON_BRIDGE_SMTP_PORT` | `1025` | Bridge SMTP port |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MCP_HOST` | `0.0.0.0` | Bind host for `streamable-http` |
| `MCP_PORT` | `3004` | Bind port for `streamable-http` |

## Running

```bash
pip install -r requirements.txt
python proton_email_server.py
```

### Docker (streamable-http)

```bash
docker build -t proton-email-mcp .
docker run --env-file .env -p 3004:3004 proton-email-mcp
```

The [`Dockerfile`](Dockerfile) hardcodes `MCP_TRANSPORT=streamable-http` for container use.

### Claude Code (stdio)

Add to your MCP server config with `command: python`, `args: ["proton_email_server.py"]`, and the env vars above.

## Tests

```bash
pip install -r requirements.txt pytest pytest-asyncio
pytest
```

Tests in [`tests/test_proton_email_server.py`](tests/test_proton_email_server.py) mock `imaplib.IMAP4` and `smtplib.SMTP` — no live Bridge required.

## License

MIT — see [LICENSE](LICENSE).
