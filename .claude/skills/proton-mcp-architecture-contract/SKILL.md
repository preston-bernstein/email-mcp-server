---
name: proton-mcp-architecture-contract
description: >
  The architecture contract for proton-email-mcp: load-bearing design decisions with their
  rationale, invariants that must never break, and known-weak points stated plainly.
  Load this skill BEFORE changing how connections, transport, error handling, logging,
  tool signatures, or credentials work in proton_email_server.py. Load it when asking
  "why is X designed this way" (e.g. why all params are str, why errors are returned as
  strings, why IMAP connects fresh per call, why CERT_NONE, why single file), when asking
  "is it safe to refactor Y" (split the file, pool connections, add types, raise exceptions,
  print to stdout, pass args to FastMCP), when reviewing a diff against this server, or when
  something in the code looks wrong/vestigial and you are tempted to "fix" it. Do NOT load it
  for how-to-run, how-to-debug, or how-to-add-a-tool tasks — see "When NOT to use this skill".
---

# proton-email-mcp — Architecture Contract

This is the contract, not a tour. It records the design decisions that are load-bearing,
the invariants whose violation breaks production, and the weaknesses we know about and
have chosen (so far) to live with. If a change you are planning touches anything below,
treat this document as the review checklist.

**Scope of truth:** everything here was verified against the repo at
`/Users/prestonbernstein/dev/proton-email-mcp` and a verified facts pack dated 2026-07-02.
Volatile facts are date-stamped. Anything not directly verifiable is labeled ASSUMPTION
or UNVERIFIED.

## System in one paragraph

`proton_email_server.py` (346 lines, the entire server) is an MCP server — MCP =
Model Context Protocol, the tool-calling protocol used by Claude Code and LibreChat —
exposing 5 email tools (`read_recent_emails`, `search_emails`, `send_email`,
`list_folders`, `get_email_stats`). It talks ONLY to Proton Bridge (Proton's local
daemon that translates IMAP/SMTP to the encrypted Proton API) on
`PROTON_BRIDGE_HOST:1143` (IMAP) and `:1025` (SMTP). It runs in two places from the
same source: stdio transport under Claude Code on the Mac, and streamable-http in a
Docker container on the desktop (10.0.0.243:3004) for LibreChat. One commit in history
(`d34a476`, 2026-06-20, as of 2026-07-02); 12 mocked pytest tests; no CI.

## Design decisions and WHY

Each of these was deliberate. Reversing one is an architecture change — route it through
the `proton-mcp-change-control` skill, not a drive-by refactor.

| # | Decision | Rationale | What it means for changes |
|---|---|---|---|
| D1 | Single file, no package | 346 lines total; a package adds import paths, packaging config, and a second thing to deploy for zero benefit at this size. The Dockerfile copies exactly one .py file. | Splitting into modules breaks the Dockerfile `COPY proton_email_server.py .`, the test import `import proton_email_server as srv`, and the `~/.claude.json` args. Don't split without touching all three. |
| D2 | `FastMCP` from `mcp[cli]` (installed 1.28.0 as of 2026-07-02) | Official SDK; decorator-based tools; provides both stdio runner and a streamable-http ASGI app from one object. | SDK upgrades can change `@mcp.tool()` and `streamable_http_app()` behavior — see invariants I4, I5. |
| D3 | ALL tool params typed `str` (even `count`, `max_results`) | MCP-client compatibility choice: some clients serialize every argument as a string; `str` params with in-body `int(...)` conversion never produce schema-validation failures at the protocol layer. | Do not "improve" signatures to `int`. If you must, that is a protocol-facing change across two clients (Claude Code + LibreChat) — change control. |
| D4 | Tools return `"❌ Error: ..."` strings, never raise | Client UX: a raised exception surfaces to the LLM as an opaque protocol error; a returned string is readable, actionable text the model can relay or react to. | Every failure path must end in a returned string. `except Exception` at tool bottom is intentional, not sloppy. |
| D5 | Stateless per-call IMAP: fresh `IMAP4(...)` + `login` + `logout` in every tool call | Simplicity over pooling. No connection state to go stale when Bridge restarts, no keepalive logic, no locking across concurrent tool calls. Bridge is on the LAN; connect cost is negligible at human tool-call rates. | Do not introduce a shared/pooled connection. It adds staleness and concurrency bugs to save milliseconds nobody feels. |
| D6 | Dual transport selected by `MCP_TRANSPORT` env: `stdio` (default, Claude Code) or `streamable-http` via `uvicorn.run(mcp.streamable_http_app(), ...)` (LibreChat/Docker) | One source file serves both deployments; the env var is the only fork point (lines 333–343). | Transport-selection changes affect BOTH deployments. Test both. Defaults: host `0.0.0.0`, port `3004`. |
| D7 | 50-result safety cap in `read_recent_emails` and `search_emails` | Each result fetches a full RFC822 message; without a cap one tool call could pull thousands of messages into an LLM context. | Keep the cap. Raising it is a cost/latency decision, not a code cleanup. |
| D8 | Sanitize query BEFORE network: `re.sub(r'[^\w\s@.\-]', '', query)` in `search_emails` (line 141) | Incident-driven: pre-git, queries with chars like `~` hung the IMAP connection indefinitely. Sanitization + `settimeout(15)` + TEXT→SUBJECT fallback are a three-part fix recorded in the initial commit message. | Sanitization must stay ahead of any socket use. Tests enforce it (`test_search_strips_special_chars`, `test_search_empty_after_sanitization`). |
| D9 | Proton Bridge is the ONLY Proton surface | Bridge handles Proton's end-to-end encryption and auth; this server speaks plain IMAP/SMTP to it. Never call the Proton REST API directly — that would take on crypto and auth this design deliberately outsources. | Any "talk to Proton directly" proposal is out of contract. |
| D10 | Docker host networking on the desktop container | Container reaches Bridge at `localhost:1143/1025` (Bridge's ports are published on the desktop host) without cross-network Docker plumbing. Verified running with `--network host`, restart `always` (as of 2026-07-02). | Switching to bridge networking requires re-pointing `PROTON_BRIDGE_HOST` and re-publishing 3004. Coordinate with `proton-mcp-run-and-operate`. |
| D11 | SMTP `starttls()` with `check_hostname=False`, `verify_mode=CERT_NONE` (lines 229–234) | Proton Bridge presents a self-signed certificate; default verification would reject it and break every send. Traffic is encrypted; the server's identity is not verified. | This is a documented weakness (W2), not an accident. Do not delete the context object "to simplify" (silently breaks send) and do not enable verification without provisioning the Bridge cert — that fix belongs to the hardening campaign. |

## INVARIANTS — break one of these and production breaks

Check every diff against this list. Each row states the consequence of violation.

| # | Invariant | Consequence if broken |
|---|---|---|
| I1 | **stdout belongs to the MCP protocol.** In stdio mode, the MCP client reads JSON-RPC from the process's stdout. All logging is configured `stream=sys.stderr` (lines 18–22). | A single stray `print()` (or a logger routed to stdout) injects garbage into the protocol stream and corrupts the stdio transport — the Claude Code deployment stops working, often with confusing parse errors rather than a clean failure. |
| I2 | **Every tool checks credentials first** and returns an `"❌ Error: ..."` string if `PROTON_USERNAME`/`PROTON_PASSWORD` are unset. | A tool that reaches `IMAP4(...).login("", "")` raises instead of degrading; the client gets a protocol error instead of a readable message (violates D4 too). |
| I3 | **Credentials are never logged.** Log lines include counts, folders, recipients — never `PROTON_PASSWORD` or the username. Credential VALUES live only in `~/.claude.json` (Mac) and the desktop container env; never copy them into code, logs, docs, or skills. | Bridge app password lands in log files / container logs / this repo. Rotation and cleanup incident. |
| I4 | **`FastMCP` constructor takes ONLY the name string:** `mcp = FastMCP("proton-email")`. The line-26 comment `NO PROMPT PARAMETER!` records a real breakage — passing an extra kwarg broke startup on this mcp version. | Server fails at import/startup in both deployments simultaneously. |
| I5 | **Tool functions remain directly callable.** Tests import the module and call `srv.search_emails(...)` etc.; this works because `@mcp.tool()` in mcp 1.28.0 returns the original function unchanged (verified 2026-07-02). | Wrapping tools (extra decorators, moving them behind a class, an SDK upgrade that changes decorator return) breaks all 12 tests and the whole no-live-Bridge test strategy. Re-verify after any `mcp` upgrade (command in Provenance). |
| I6 | **Module reads env at import time** (lines 29–33, module-level constants). | Tests MUST set env vars before `import proton_email_server` (they use `os.environ.setdefault` at the top of the test file — import-order-sensitive). Also means the server never picks up env changes without a restart, and moving config reads inside functions changes testability and restart semantics — change control. |
| I7 | **Failure paths return strings** (restating D4 as an invariant because it is the most tempting one to "clean up"). | Raising converts readable tool errors into opaque client-side protocol errors for both Claude Code and LibreChat. |

Quick self-test before merging any change:

```bash
cd /Users/prestonbernstein/dev/proton-email-mcp && ./venv/bin/python -m pytest tests/ -q
# expected as of 2026-07-02: 12 passed
grep -n 'print(' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py
# expected: no matches (I1)
```

## Known-weak points — all OPEN as of 2026-07-02

State these plainly; do not paper over them and do not silently "fix" them either.
Fix work is owned by the `proton-mcp-drift-and-hardening-campaign` skill; whether a given
fix is allowed and how it is reviewed is owned by `proton-mcp-change-control`.

| # | Weakness | Detail | Status |
|---|---|---|---|
| W1 | Plaintext IMAP over LAN | Mac deployment sets `PROTON_BRIDGE_HOST=10.0.0.243`; `imaplib.IMAP4` is plaintext, so the Bridge password crosses the LAN unencrypted on every tool call. (Whether this Bridge build accepts STARTTLS on 1143 is UNVERIFIED.) | open |
| W2 | `CERT_NONE` on SMTP | STARTTLS encrypts but does not verify the Bridge's self-signed cert (see D11). MITM on the LAN path could impersonate the SMTP server. | open |
| W3 | Unauthenticated 0.0.0.0:3004 | Desktop streamable-http endpoint has no auth and binds all interfaces: any LAN device can read mail and send email as the account. | open |
| W4 | No socket timeout in `read_recent_emails` | Only `search_emails` sets `mail.sock.settimeout(15)` (line 151). A hung IMAP connection in `read_recent_emails` (line 85 area) blocks indefinitely — same failure class as the pre-git search hang. | open |
| W5 | `logout()` not in `finally` | Every tool calls `mail.logout()` on success paths only; an exception mid-tool leaks the IMAP connection. | open |
| W6 | Stats "7 days" bug | `get_email_stats` labels a count "Recent emails (7 days)" but line 307 searches `SINCE <today>` (`datetime.now().strftime("%d-%b-%Y")`) — it counts TODAY only. Known bug, not fixed. | open |
| W7 | Vestigial requirements | `secure-smtplib`, `httpx`, `python-dotenv` are in requirements.txt but never imported; the server NEVER loads `.env` (config must come from the process environment — `.env.example` is a template only, and its `127.0.0.1` host does not match the real Mac deployment's `10.0.0.243`). Removal changes the Docker build — change control. | open |
| W8 | 3-copy source drift | Same source exists at the Mac repo (canonical), `/home/preston/docker/proton-email-mcp`, and `/opt/docker/librechat-stack/proton-email-mcp` on the desktop. No deploy pipeline; prod image built 2026-06-20. Drift MEASURED 2026-07-02: the `/home/preston` copy is a stale pre-fix snapshot; the librechat-stack copy matched canonical (worked example in `proton-mcp-diagnostics-and-tooling`; re-measure before acting). Edit the Mac repo first, always. | open |
| W9 | Dev/prod Python skew | Dev venv is Python 3.14.6; prod image is `python:3.12-slim`. "Passes locally" does not prove "runs in the image". | open |

One non-bug to leave alone: `send_email` sets `msg['Bcc']`, which looks like a BCC leak
but is not — `smtplib.send_message` strips Bcc headers before transmission (verified
against Python docs, per facts pack). Do not "fix" it.

## Is it safe to refactor Y? — fast answers

| Proposed refactor | Verdict |
|---|---|
| Split the single file into a package | No, not casually — breaks Dockerfile, tests, and client config (D1). Change control. |
| Change tool params from `str` to `int`/typed | No — deliberate client-compat choice (D3). Change control. |
| Raise exceptions instead of returning error strings | No — violates D4/I7. |
| Pool or share the IMAP connection | No — violates D5 for negligible gain. |
| Add `settimeout` to `read_recent_emails`, wrap logout in `finally` | Yes in principle (fixes W4/W5), but it is a behavior change — route via change control + hardening campaign. |
| Remove unused deps from requirements.txt | Fixes W7, but rebuilds the prod image — change control. |
| Print startup banner to stdout | Never (I1). |
| Pass `instructions=`/`prompt=` to `FastMCP(...)` | Never on this mcp version (I4). |
| Upgrade the `mcp` package | Allowed via change control; re-verify I5 immediately after. |

## When NOT to use this skill

This skill answers "why is it built this way" and "what must not break". For everything
else, route to the sibling skill:

| You want to... | Use instead |
|---|---|
| Classify/gate/review a specific change | `proton-mcp-change-control` |
| Diagnose a live failure (hangs, auth errors, transport weirdness) | `proton-mcp-debugging-playbook` |
| Recreate the venv or Docker image; env-var traps | `proton-mcp-build-and-env` |
| Start/stop/redeploy either transport; full config table | `proton-mcp-run-and-operate` |
| Run connectivity checks, smoke tests, drift measurement | `proton-mcp-diagnostics-and-tooling` |
| Write or extend tests; live-verification protocol | `proton-mcp-validation-and-qa` |
| Add or modify an MCP tool | `proton-mcp-extending-tools` |
| Actually FIX W1–W9 (drift, auth, timeouts, TLS) | `proton-mcp-drift-and-hardening-campaign` |
| Understand Proton Bridge / IMAP / SMTP / MIME / MCP transports as concepts | `proton-bridge-email-reference` |

## Provenance and maintenance

Sources: repo at `/Users/prestonbernstein/dev/proton-email-mcp` (read directly) and the
verified facts pack dated 2026-07-02. Facts about the desktop (10.0.0.243) come from the
facts pack, not fresh SSH; re-verify them from a machine with `ssh desktop-agent` access.

Re-verification one-liners for every drift-prone fact:

| Fact | Re-verify with |
|---|---|
| Tests still pass (12) | `cd /Users/prestonbernstein/dev/proton-email-mcp && ./venv/bin/python -m pytest tests/ -q` |
| mcp version (1.28.0) | `/Users/prestonbernstein/dev/proton-email-mcp/venv/bin/pip show mcp \| grep -i version` |
| I5: `@mcp.tool()` returns original function | `cd /Users/prestonbernstein/dev/proton-email-mcp && PROTON_USERNAME=x PROTON_PASSWORD=x ./venv/bin/python -c "import proton_email_server as s, inspect; print(inspect.iscoroutinefunction(s.search_emails))"` (expect `True`) |
| I1: no stdout prints | `grep -n 'print(' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` (expect empty) |
| I4: constructor still name-only | `grep -n 'FastMCP(' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` |
| W4: read still has no timeout | `grep -n 'settimeout' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` (expect one hit, in search_emails) |
| W6: stats SINCE-today bug still present | `grep -n 'SINCE' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` (line ~307) |
| W7: deps still vestigial | `grep -nE 'import (httpx\|dotenv\|secure)' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` (expect empty) |
| Dev Python (3.14.6) | `/Users/prestonbernstein/dev/proton-email-mcp/venv/bin/python --version` |
| Prod image Python (3.12) | `grep -n 'FROM' /Users/prestonbernstein/dev/proton-email-mcp/Dockerfile` |
| Git history (single commit d34a476) | `git -C /Users/prestonbernstein/dev/proton-email-mcp log --oneline` |
| Desktop container config (host net, :3004) | from a machine with access: `ssh desktop-agent "docker inspect proton-email-mcp --format '{{.HostConfig.NetworkMode}} {{.Config.Env}}'"` (do not paste credential env values anywhere) |
| W8: source drift between the 3 copies | `ssh desktop-agent "md5sum /home/preston/docker/proton-email-mcp/proton_email_server.py /opt/docker/librechat-stack/proton-email-mcp/proton_email_server.py"` vs `md5 -q /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` |

Maintenance rule: any commit touching `proton_email_server.py`, `Dockerfile`, or
`requirements.txt` should re-run at least the first four commands and update this file's
date stamps if a "(as of 2026-07-02)" fact changed.
