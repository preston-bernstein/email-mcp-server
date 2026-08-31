---
name: proton-mcp-diagnostics-and-tooling
description: Load when you need to MEASURE proton-email-mcp system state instead of guessing — connectivity checks (can I reach Bridge IMAP 1143 / SMTP 1025 / MCP 3004), liveness probes ("is the bridge up", "is the MCP container up", "is :3004 answering"), source-drift checks (do the Mac repo and the two desktop copies still match), desktop stack health (docker ps / listening ports), or a read-only IMAP smoke test. Also load before claiming "Bridge is down" or "the server is broken" — run the script, cite the output.
---

# proton-mcp diagnostics and tooling

Measurement scripts for the proton-email-mcp system. Rule of this skill: **never assert
system state you have not measured this session.** Run the relevant script, paste its
PASS/FAIL conclusion as evidence, then act.

Jargon, defined once:

- **Proton Bridge** — Proton's local gateway app. Runs on the desktop in container
  `protonmail-bridge` and exposes Proton Mail as plain IMAP on port **1143** and SMTP on
  port **1025**.
- **MCP server** — `proton_email_server.py`, the Model Context Protocol server that wraps
  Bridge in 5 email tools. Runs twice: stdio on the Mac (Claude Code) and as desktop
  container `proton-email-mcp` serving **streamable-http on port 3004** (the HTTP
  transport where the client POSTs JSON-RPC to `/mcp`).
- **Desktop** — the Linux box at `$PROTON_MCP_HOST`, reachable read-only as `ssh $PROTON_MCP_SSH_HOST`
  (agent user, NOPASSWD sudo).
- **Source drift** — three copies of the source exist (Mac git repo = canonical, plus two
  desktop directories); "drift" means their file hashes no longer match.

All scripts live in `scripts/` next to this file, are executable, print explicit
`PASS`/`FAIL` lines plus a final `CONCLUSION:` line, and exit 0 only on full PASS.
All are read-only: no email is sent, nothing is restarted, no secrets are read or printed.

| Script | Question it answers | Needs |
|---|---|---|
| `scripts/check_connectivity.sh` | Can this machine reach Bridge IMAP/SMTP and is the MCP HTTP server alive? | network only |
| `scripts/drift_check.sh` | Do the 3 source copies still match? | `ssh $PROTON_MCP_SSH_HOST` |
| `scripts/check_desktop_stack.sh` | Are the desktop containers up and ports listening? | `ssh $PROTON_MCP_SSH_HOST` |
| `scripts/smoke_imap_readonly.py` | Does the full IMAP path (connect, login, select) work end-to-end? | Bridge credentials in env |

Start with `check_connectivity.sh` (cheapest, no SSH, no creds). Escalate only as needed.

## 1. check_connectivity.sh — port and liveness probes

**Measures:** TCP reachability of `BRIDGE_HOST:1143` (IMAP), `:1025` (SMTP),
`:3004` (MCP), each via `nc -z -w 3`; then an HTTP probe of
`http://HOST:3004/mcp`.

**Reading the HTTP probe:** streamable-http answers POSTed JSON-RPC, so a bare GET is
the "wrong" verb — the point is that **any HTTP status code (including 4xx) means the
server process is ALIVE** and parsing HTTP. Only connection refused / timeout means DOWN.
Do not read a 4xx here as a failure.

**Run:**

```bash
cd /Users/prestonbernstein/dev/proton-email-mcp
.claude/skills/proton-mcp-diagnostics-and-tooling/scripts/check_connectivity.sh
# override: BRIDGE_HOST=127.0.0.1 ... (defaults: BRIDGE_HOST=$PROTON_MCP_HOST, MCP_PORT=3004)
```

**Healthy output — real run from the Mac, 2026-07-02:**

```
== proton-email-mcp connectivity check ==
Bridge host: $PROTON_MCP_HOST | MCP host: $PROTON_MCP_HOST

PASS  Bridge IMAP                  $PROTON_MCP_HOST:1143 open
PASS  Bridge SMTP                  $PROTON_MCP_HOST:1025 open
PASS  MCP streamable-http          $PROTON_MCP_HOST:3004 open
PASS  MCP HTTP liveness            http://$PROTON_MCP_HOST:3004/mcp answered HTTP 421 (any status = server ALIVE; 4xx expected for GET)

CONCLUSION: PASS — Bridge IMAP/SMTP reachable and MCP server alive.
```

Note the measured status is **HTTP 421** (as of 2026-07-02, mcp 1.28.0 image) — that is
the expected healthy answer to a GET, not an error.

**Failure interpretation table:**

| 1143 | 1025 | 3004 TCP | 3004 HTTP | Implicates | Next step |
|---|---|---|---|---|---|
| FAIL | FAIL | FAIL | FAIL | Network / desktop host down (or wrong `BRIDGE_HOST`) | `ping $PROTON_MCP_HOST`; run `check_desktop_stack.sh` if SSH works |
| FAIL | FAIL | PASS | PASS | `protonmail-bridge` container down (MCP fine but its tools will all error) | `check_desktop_stack.sh`, then proton-mcp-debugging-playbook |
| FAIL | PASS | — | — | Bridge partially up (IMAP side wedged) — rare | proton-mcp-debugging-playbook |
| PASS | PASS | FAIL | FAIL | `proton-email-mcp` container down or crashed | `check_desktop_stack.sh`; restart procedure lives in proton-mcp-run-and-operate |
| PASS | PASS | PASS | FAIL | Port open but process not answering HTTP (hung container) | proton-mcp-debugging-playbook |
| PASS | PASS | PASS | PASS | Everything alive; if tools still fail, suspect credentials or app logic | `smoke_imap_readonly.py`, then proton-mcp-debugging-playbook |

## 2. drift_check.sh — source drift across the three copies

**Measures:** sha256 of `proton_email_server.py`, `Dockerfile`, `requirements.txt`
in each copy; per-file `MATCH`/`DRIFT` verdict against the canonical Mac repo.

| Copy | Path | Read via |
|---|---|---|
| Canonical | `/Users/prestonbernstein/dev/proton-email-mcp` | local `shasum -a 256` |
| Desktop A | `/home/preston/docker/proton-email-mcp` | `ssh $PROTON_MCP_SSH_HOST sudo sha256sum` |
| Desktop B | `/opt/docker/librechat-stack/proton-email-mcp` | `ssh $PROTON_MCP_SSH_HOST sudo sha256sum` |

**Run:**

```bash
cd /Users/prestonbernstein/dev/proton-email-mcp
.claude/skills/proton-mcp-diagnostics-and-tooling/scripts/drift_check.sh
```

**Worked example — real measured result, 2026-07-02:**

```
FILE                      MAC(canon)     DESKTOP_A      DESKTOP_B      VERDICT
proton_email_server.py    73a737e4434c   03f0fac70ff1   73a737e4434c   DRIFT: desktop_A differs
Dockerfile                ec4c77ecf095   ec4c77ecf095   ec4c77ecf095   MATCH (all 3 identical)
requirements.txt          08b51e4046fc   62c0e857694c   08b51e4046fc   DRIFT: desktop_A differs

CONCLUSION: FAIL — drift detected.
```

Characterization of that drift (diffed 2026-07-02): Desktop A is a **pre-fix snapshot**.
Its `proton_email_server.py` (327 lines vs 346 canonical) lacks the IMAP query
sanitization, the `settimeout(15)`, and the TEXT-to-SUBJECT search fallback — the exact
fixes recorded in commit d34a476 — and still starts streamable-http via
`mcp.run(transport="streamable-http", host=..., port=...)` instead of
`uvicorn.run(mcp.streamable_http_app(), ...)`. Its `requirements.txt` is missing
`uvicorn`. Desktop B matches the canonical repo exactly on all three files.

**Interpretation:**

| Verdict | Meaning | Next step |
|---|---|---|
| All MATCH | No drift; any prod/dev behavior difference is env/config, not source | — |
| DRIFT on desktop copy | Stale or hand-edited deploy artifact; the running image may embed old code | Do NOT hand-edit desktop copies; route to proton-mcp-drift-and-hardening-campaign |
| MISSING | Copy deleted or path moved | Re-verify paths, then proton-mcp-drift-and-hardening-campaign |
| Cannot ssh | No data — do not conclude anything about drift | Fix SSH first (`ssh $PROTON_MCP_SSH_HOST true`) |

## 3. check_desktop_stack.sh — container and port health on the desktop

**Measures:** via `ssh $PROTON_MCP_SSH_HOST`: `docker ps` filtered to
`protonmail-bridge` / `proton-email-mcp` / `librechat*`, and `ss -tln` for listeners on
1143 / 1025 / 3004. LibreChat is reported as INFO only — it consumes :3004 but is not
required for MCP health.

**Run:**

```bash
cd /Users/prestonbernstein/dev/proton-email-mcp
.claude/skills/proton-mcp-diagnostics-and-tooling/scripts/check_desktop_stack.sh
```

**Healthy baseline — real run, 2026-07-02:**

```
-- containers (docker ps) --
librechat	Up 5 days
proton-email-mcp	Up 5 days
protonmail-bridge	Up 5 days

PASS  container protonmail-bridge is Up
PASS  container proton-email-mcp is Up
INFO  LibreChat container(s) present (consumer of :3004, not required for MCP health)

-- listening ports (ss -tln) --
PASS  :1143 listening (Bridge IMAP)
PASS  :1025 listening (Bridge SMTP)
PASS  :3004 listening (MCP streamable-http)

CONCLUSION: PASS — desktop stack healthy (both containers Up, 1143/1025/3004 listening).
```

**Failure interpretation:**

| Symptom | Meaning | Next step |
|---|---|---|
| Container listed but "Restarting" / not Up | Crash loop (`proton-email-mcp` has restart `always`; bridge unless-stopped) | `docker logs` triage via proton-mcp-debugging-playbook |
| Container Up but its port not listening | Process inside is up but not bound (bad `MCP_PORT` env, or bridge not logged in) | proton-mcp-debugging-playbook |
| `protonmail-bridge` missing | Bridge stack down; all 5 MCP tools will return errors | Start procedure: proton-mcp-run-and-operate |
| Cannot ssh | No data — pair with `check_connectivity.sh` from the Mac to distinguish host-down vs SSH-broken | — |

## 4. smoke_imap_readonly.py — end-to-end IMAP path, read-only

**Measures:** the full IMAP path the MCP tools depend on: TCP connect, LOGIN with the
Bridge app password, `SELECT INBOX` **read-only** (`readonly=True` — never marks
messages seen), prints the message count, logs out. Socket timeout 15 s. This is the
only script that authenticates; it still sends nothing and modifies nothing. It never
prints the password.

**Run** (credentials come from the process environment; source the values from
`~/.claude.json` under `mcpServers.proton-mail.env` — never echo or paste them):

```bash
cd /Users/prestonbernstein/dev/proton-email-mcp
env PROTON_USERNAME=... PROTON_PASSWORD=... \
  python3 .claude/skills/proton-mcp-diagnostics-and-tooling/scripts/smoke_imap_readonly.py
# optional: PROTON_BRIDGE_HOST (default $PROTON_MCP_HOST), PROTON_BRIDGE_IMAP_PORT (1143), SMOKE_FOLDER (INBOX)
```

`PROTON_PASSWORD` is the **Bridge app password** (shown in the Bridge UI), not the
Proton account password.

**Expected healthy output** (shape; message count varies):

```
Connecting to $PROTON_MCP_HOST:1143 (plain IMAP, socket timeout 15s)...
PASS: IMAP login accepted.
PASS: selected 'INBOX' read-only; N messages present.
CONCLUSION: PASS — Bridge IMAP end-to-end path (connect, auth, select) is healthy.
```

**Verified graceful-failure output — real run with credentials unset, 2026-07-02
(exit code 2):**

```
FAIL: PROTON_USERNAME and/or PROTON_PASSWORD not set in the environment.
  Source them from ~/.claude.json (mcpServers.proton-mail.env) — do not paste values into files or chat.
CONCLUSION: FAIL — IMAP read-only smoke check did not complete.
```

**Failure interpretation:**

| Failure line | Meaning | Next step |
|---|---|---|
| `could not connect` | Network/Bridge port problem — same territory as `check_connectivity.sh` FAIL on 1143 | Section 1 table |
| `IMAP login rejected` | Wrong/rotated Bridge app password, or Bridge is running but not logged in to the Proton account | proton-mcp-debugging-playbook |
| `could not SELECT folder` | Auth fine, folder name wrong (Bridge folder names come from `list_folders`) | Retry with `SMOKE_FOLDER` set to a name from `list_folders` |
| Hang then timeout at 15 s | Bridge accepting TCP but wedged | proton-mcp-debugging-playbook |

Exit codes: `0` PASS, `2` credentials unset, `1` any connect/login/select failure.

## When NOT to use this skill

This skill only measures. Route everything else to a sibling:

| You actually want to... | Use instead |
|---|---|
| Diagnose WHY something failed (symptom-to-cause triage, incident history) | proton-mcp-debugging-playbook |
| Fix drift, harden the unauthenticated :3004, encrypt the IMAP path | proton-mcp-drift-and-hardening-campaign |
| Start/stop/redeploy the server or containers, full config table | proton-mcp-run-and-operate |
| Rebuild the venv or Docker image from scratch | proton-mcp-build-and-env |
| Understand design decisions and accepted known-weak points | proton-mcp-architecture-contract |
| Change code (classification, gates, review) | proton-mcp-change-control |
| Run/write the pytest suite, evidence standards | proton-mcp-validation-and-qa |
| Add or modify an MCP tool | proton-mcp-extending-tools |
| Background on Proton Bridge, IMAP/SMTP/MIME, MCP transports | proton-bridge-email-reference |

Also: this skill never sends email and never restarts anything. If a check fails, do not
"fix" from here — measure, cite, route.

## Provenance and maintenance

- Authored 2026-07-02 from the verified facts pack of the same date, the repo source
  (`proton_email_server.py` at commit d34a476), and live measurements.
- Every "real run" block above is actual captured output from 2026-07-02: connectivity
  from the Mac (all PASS, HTTP 421 on GET /mcp), drift check (desktop A stale on
  `proton_email_server.py` + `requirements.txt`; desktop B identical to canonical),
  desktop stack (all containers Up 5 days, all three ports listening), and the
  smoke script's creds-unset graceful failure. The smoke script's success path was NOT
  exercised with real credentials at authoring time.
- Volatile facts to re-verify before trusting after a system change: the HTTP 421
  liveness code (depends on the mcp package version in the image, 1.28.0 as of
  2026-07-02), the drift verdicts (re-run `drift_check.sh` — they change the moment
  anyone deploys), container uptimes, and the desktop copy paths.
- If the drift-and-hardening campaign eliminates the desktop copies or adds auth to
  :3004, update `drift_check.sh` paths and the HTTP-probe interpretation here in the
  same change.
- Hard rules baked into these scripts: read-only only; no credential values or the
  Proton account address may ever appear in this directory.
