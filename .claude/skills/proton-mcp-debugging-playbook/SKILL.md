---
name: proton-mcp-debugging-playbook
description: >
  Symptom-to-triage playbook and incident history for the proton-email-mcp server.
  Load this when a Proton email tool hangs, errors, times out, or returns a "❌ Error:" string;
  when read_recent_emails / search_emails / send_email / list_folders / get_email_stats
  misbehave or return empty results; when "Proton credentials not configured" appears;
  when IMAP login fails or the connection is refused on 1143/1025/3004; when proton tools
  are missing from Claude Code or LibreChat; when search finds nothing it should find;
  when email stats look wrong; when the stdio server exits at startup; or for any
  "why is the email server broken" question.
---

# Proton MCP Debugging Playbook

Runbook for diagnosing failures in `proton-email-mcp` — a single-file Python MCP server
(`/Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py`, 346 lines) that exposes
Proton Mail as 5 MCP tools by talking to **Proton Bridge** (Proton's local daemon that translates
Proton's encrypted mail into ordinary IMAP on port 1143 and SMTP on port 1025).

Two production deployments of the same source (as of 2026-07-02):

| Deployment | Transport | Where configured | Bridge host it uses |
|---|---|---|---|
| Mac / Claude Code | stdio (server spawned as a subprocess, speaks MCP over stdin/stdout) | `~/.claude.json` → `mcpServers["proton-mail"]` | `$PROTON_MCP_HOST` (the desktop, over LAN) |
| Desktop Docker container `proton-email-mcp` | streamable-http on `0.0.0.0:3004` (consumed by LibreChat) | container env; `--network host` | `localhost` |

Proton Bridge itself runs as desktop container `protonmail-bridge`
(compose file `/opt/docker/auth-stack/docker-compose.yml`, state volume
`/opt/docker/auth-stack/data/protonmail-bridge`).

**Credential rule:** never print, copy, or paste credential values or the Proton account email
address. They live in `~/.claude.json` (Mac) and the container env (desktop). Reference locations only.

**Command conventions in this playbook:**

- Plain commands: run on the Mac, safe and read-only.
- Commands marked **[desktop]**: run from the desktop maintenance context via `ssh $PROTON_MCP_SSH_HOST`.
  Do not run them from an agent session that is barred from SSH.
- **Never call `send_email` while debugging.** Read-path tools (`list_folders`,
  `get_email_stats`, `read_recent_emails` with small count) are the safe probes.

## Core facts you need before triage

- The server reads configuration **only from the process environment, at import time**
  (`proton_email_server.py` lines 29–33). `python-dotenv` is in `requirements.txt` but is
  **never imported** — a `.env` file on disk does nothing. This explains most
  "but I set the variable!" confusion.
- Every tool returns human-readable strings starting with `❌ Error:` instead of raising
  (e.g. lines 78, 126, 137). A `❌` reply means the tool ran and caught something — the MCP
  plumbing itself is fine.
- `PROTON_PASSWORD` must be the **Bridge app password** (shown inside the Proton Bridge UI/CLI),
  **not** the Proton account password. The account password will always fail IMAP login.
- Only `search_emails` sets a socket timeout (`mail.sock.settimeout(15)`, line 151).
  `read_recent_emails`, `list_folders`, `send_email`, and `get_email_stats` have **no timeout**
  and can hang indefinitely.

---

## A. Triage: symptom → likely causes → discriminating experiment

Master index (details below):

| # | Symptom | Most likely cause first |
|---|---|---|
| 1 | Tool call hangs forever | No socket timeout on read path; Bridge wedged |
| 2 | `❌ Error: Proton credentials not configured` | Env vars not reaching the process |
| 3 | IMAP login failed | Account password instead of Bridge app password; Bridge unauthenticated |
| 4 | Connection refused on 1143/1025 | Bridge container down; wrong `PROTON_BRIDGE_HOST` |
| 5 | `❌ Error: Could not select folder '...'` | Folder name case/path wrong |
| 6 | Search returns nothing it should find | Sanitizer stripped the query; SUBJECT fallback masking TEXT failure |
| 7 | LibreChat doesn't show proton tools | Container down; :3004 not listening; `allowedDomains` missing; no restart after yaml edit |
| 8 | Tools missing in Claude Code session | `~/.claude.json` entry wrong; venv python path; server dies at spawn |
| 9 | Stats numbers look wrong | "Recent (7 days)" is a known open bug (counts today only) |
| 10 | stdio server exits at startup | Import error; stray stdout; FastMCP constructor args |

### 1. Tool call hangs / never returns

Likely causes, ranked:

1. **`read_recent_emails` (or `list_folders` / `get_email_stats` / `send_email`) hit a wedged
   IMAP/SMTP connection.** These tools set **no socket timeout** — verified: `read_recent_emails`
   opens `imaplib.IMAP4(...)` at line 85 with no `settimeout` anywhere in its body. This is the
   open remnant of the special-char hang incident (§B.1). `search_emails` is the only tool
   protected (`settimeout(15)`, line 151).
2. Proton Bridge container up but stuck (needs re-login or restart).
3. Network path Mac→desktop broken mid-connection (accepted TCP, then silence).

Discriminating experiment:

- Call `search_emails` with a harmless query on the same folder.
  - **Returns within ~15 s (results, "No emails found", or `❌ Error searching emails: timed out`)**
    → the hang is specific to the untimed read path; Bridge is responsive-ish or slow.
    A `timed out` error means Bridge accepted the connection but stopped answering → restart Bridge.
  - **`search_emails` also hangs past 20 s** → connection is accepted but Bridge is wedged
    pre-login, or the hang is in your MCP client, not the server. Check Bridge:

  **[desktop]** (run from desktop maintenance context via `ssh $PROTON_MCP_SSH_HOST`):

  ```
  docker ps
  docker logs --tail 50 protonmail-bridge
  ```

  Expected healthy: `protonmail-bridge` container `Up`, logs without repeated auth/sync errors.
  Restart of a wedged Bridge is an operational action — see `proton-mcp-run-and-operate`.

### 2. `❌ Error: Proton credentials not configured`

Meaning: `PROTON_USERNAME` or `PROTON_PASSWORD` was empty **in the server process's environment
at import time** (lines 29–30; check at line 77 for reads, 136/210/257/290 for the other tools).

Ranked causes:

1. **Env block not reaching the process.**
   - Mac stdio: env must be in `~/.claude.json` under `mcpServers["proton-mail"].env`.
     Shell exports and `.env` files are irrelevant — Claude Code spawns the subprocess with
     the JSON env block.
   - Desktop Docker: env must be set on the container (`docker inspect` shows it — but that
     prints values; only do this in a trusted terminal).
2. **A `.env` file was created and assumed to work.** The server never loads `.env` —
   `python-dotenv` is installed but unused (verified: no `dotenv` import in
   `proton_email_server.py`).

Discriminating experiment (Mac, prints key names only, never values):

```
jq '.mcpServers["proton-mail"] | {command, args, env_keys: (.env | keys)}' ~/.claude.json
```

- **`env_keys` includes `PROTON_USERNAME` and `PROTON_PASSWORD`** → config file is right;
  the failing process is a different deployment (are you talking to the desktop container via
  LibreChat?) or the session predates the config — restart the Claude Code session.
- **Entry missing or keys absent** → fix `~/.claude.json`, restart session.

For the desktop container: **[desktop]** `docker logs --tail 50 proton-email-mcp` — the server
logs `WARNING - PROTON_USERNAME not set` / `WARNING - PROTON_PASSWORD not set` at startup
(lines 328–331) if env was missing when the container started.

### 3. IMAP login failed (`❌ Error reading emails: ... LOGIN failed` or similar)

Ranked causes:

1. **Proton account password used instead of the Bridge app password.** Bridge generates its own
   local password; the account password never works against 1143. `.env.example` line 2 hints at
   this (`your-bridge-app-password`).
2. **Bridge container running but not authenticated** (logged out, first-run not completed, or
   state volume wiped). Bridge auth state lives in
   `/opt/docker/auth-stack/data/protonmail-bridge` on the desktop.
3. Username mismatch (must be the address Bridge is configured for).

Discriminating experiment:

**[desktop]** (via `ssh $PROTON_MCP_SSH_HOST`):

```
docker logs --tail 50 protonmail-bridge
```

- **Logs show successful sync / logged-in user activity** → Bridge is authenticated; the problem
  is the password value in the client env → re-copy the Bridge app password into the config
  location (never into a doc).
- **Logs show "not logged in", auth prompts, or a fresh first-run** → Bridge lost its session;
  re-authenticate Bridge (operational procedure: `proton-mcp-run-and-operate`).

### 4. Connection refused on 1143 or 1025

Ranked causes:

1. **`protonmail-bridge` container down** (it publishes `1025:25` and `1143:143`).
2. **Wrong `PROTON_BRIDGE_HOST`.** Known trap: `.env.example` says `127.0.0.1`, but the real Mac
   stdio deployment must use `$PROTON_MCP_HOST` (the desktop) — the example is a template, not a record
   of deployment (as of 2026-07-02). `127.0.0.1` on the Mac means "connect to the Mac itself",
   where no Bridge runs → instant refusal.
3. Port published differently after a compose change.

Discriminating experiment (Mac; UNVERIFIED — standard `nc` syntax, not executed during authoring):

```
nc -vz $PROTON_MCP_HOST 1143
nc -vz $PROTON_MCP_HOST 1025
```

- **Both succeed** → Bridge is reachable; your failing process has the wrong
  `PROTON_BRIDGE_HOST` or port. Check the env (symptom 2 procedure).
- **Refused/timeout** → check the desktop side. **[desktop]**:

  ```
  docker ps
  ss -tln | grep -E "1143|1025|3004"
  ```

  Expected healthy: `protonmail-bridge` up; listeners on 1143 and 1025 (and 3004 for the MCP
  container).

### 5. `❌ Error: Could not select folder '<name>'`

Returned by `search_emails` when `mail.select(folder)` is non-OK (lines 153–156).
`read_recent_emails` and `get_email_stats` do **not** check select status — a bad folder there
surfaces as a downstream `❌ Error reading emails: ...` exception instead.

Cause: folder name wrong — IMAP folder names are effectively case- and path-sensitive as Bridge
exposes them (e.g. `Folders/Receipts`, not `receipts`).

Discriminating experiment: call the `list_folders` tool first and copy the folder name exactly
as printed. `list_folders` parses names from the quoted segments of the IMAP LIST response
(lines 274–277).

### 6. Search returns nothing when matches clearly exist

Ranked causes:

1. **The sanitizer gutted your query.** Line 141:

   ```python
   safe_query = re.sub(r'[^\w\s@.\-]', '', query).strip()
   ```

   Everything except word chars, whitespace, `@`, `.`, `-` is silently removed — quotes, colons,
   slashes, `+`, `#`, parentheses all vanish. `"order #1234"` becomes `order 1234`;
   `from:alice` becomes `fromalice` (the colon is stripped and the words merge —
   IMAP-style operators do not work here anyway; the query is a plain literal).
   If **nothing** survives, you get `❌ Error: Query '...' contains no searchable characters`
   (lines 142–143) — but partial stripping is silent.
2. **TEXT→SUBJECT silent fallback masking a TEXT failure.** Lines 159–164: if the `TEXT` search
   raises or returns non-OK, the code silently retries as `SUBJECT`-only. A body-text match then
   returns "No emails found" with no hint that only subjects were searched.
3. The term genuinely isn't in that folder (search is per-folder, default `INBOX`).

Discriminating experiment:

- Re-run the search using only letters, digits, spaces, `@ . -` in the query.
  - **Now it matches** → sanitizer was the problem; rewrite queries to plain keywords.
  - **Still nothing** → test the fallback hypothesis: search a string you know is in a message
    **subject** in that folder.
    - **Subject term found, body-only term not** → you are in SUBJECT-fallback territory; the
      TEXT search path is failing against Bridge. Check server logs for the underlying error
      (stderr for stdio; **[desktop]** `docker logs --tail 50 proton-email-mcp` for the container).

### 7. LibreChat doesn't show the proton tools

Ranked causes:

1. **Container `proton-email-mcp` down on the desktop**, or :3004 not listening.
2. **`librechat.yaml` missing `mcpSettings.allowedDomains` entry `http://localhost:3004`.**
   LibreChat's SSRF protection blocks private-LAN/localhost MCP URLs unless allow-listed —
   required, verified present in `/opt/docker/librechat-stack/librechat.yaml` as of 2026-07-02
   alongside `mcpServers.proton-email: url http://localhost:3004/mcp, type streamable-http`.
3. **LibreChat not restarted after a `librechat.yaml` edit** — yaml changes need a restart.

Discriminating experiment — **[desktop]** (via `ssh $PROTON_MCP_SSH_HOST`):

```
docker ps
ss -tln | grep -E "1143|1025|3004"
docker logs --tail 50 proton-email-mcp
```

- **`proton-email-mcp` not in `docker ps` or no `:3004` listener** → start the container
  (procedure: `proton-mcp-run-and-operate`).
- **Container up, :3004 listening, tools still absent** → inspect
  `/opt/docker/librechat-stack/librechat.yaml` for both the `mcpServers.proton-email` block and
  the `allowedDomains` entry; then restart LibreChat.
- **Container logs show credential warnings or a crash loop** → fall back to symptoms 2/10.

### 8. Tools missing in a Claude Code session (Mac)

Expected when healthy: tools appear as `mcp__proton-mail__read_recent_emails`, etc.

Ranked causes:

1. `mcpServers["proton-mail"]` block missing/typo'd in `~/.claude.json`.
2. **Wrong python path** — the entry must point at the repo venv:
   command `/Users/prestonbernstein/dev/proton-email-mcp/venv/bin/python3.14`,
   args `["/Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py"]`
   (absolute path; as of 2026-07-02). A system python without `mcp` installed
   dies on import and the tools silently never register.
3. Server exits at spawn (see symptom 10).

Discriminating experiment: run the server manually to see its stderr (safe, read-only; with
credentials unset it cannot touch mail, and stdio mode exits cleanly when stdin closes):

```
cd /Users/prestonbernstein/dev/proton-email-mcp && MCP_TRANSPORT=stdio PROTON_USERNAME= PROTON_PASSWORD= ./venv/bin/python proton_email_server.py </dev/null
```

Verified output (2026-07-02) — three log lines on stderr, then clean exit:

```
... - proton-email-server - INFO - Starting Proton Bridge Email MCP server...
... - proton-email-server - WARNING - PROTON_USERNAME not set
... - proton-email-server - WARNING - PROTON_PASSWORD not set
```

- **You see this** → the server itself starts fine; the problem is the `~/.claude.json` entry or
  a stale session. Check with the `jq` command from symptom 2, then restart the session.
- **Traceback instead** → import-time failure (wrong venv, broken dependency); note the traceback
  and see symptom 10.
- If you run it interactively (without `</dev/null`) it will sit waiting on stdin — that is
  correct stdio-server behavior; Ctrl-C to leave.

### 9. `get_email_stats` numbers look wrong

**"Recent emails (7 days)" is a KNOWN OPEN BUG** (as of 2026-07-02 — do not treat as fixed,
do not re-diagnose). Line 307:

```python
status, recent_msgs = mail.search(None, "SINCE", datetime.now().strftime("%d-%b-%Y"))
```

`SINCE <today's date>` counts messages from **today only**, not 7 days, while line 315 labels it
"Recent emails (7 days)". Total/unread/read counts are computed correctly (ALL and UNSEEN
searches, lines 299–304). Fixing this is a behavior change — route through
`proton-mcp-change-control`, don't hotfix it mid-debug.

### 10. stdio server exits immediately at startup

Ranked causes:

1. **Import-time error** — bad venv, missing dependency, or a syntax error from an edit.
2. **Extra constructor args on `FastMCP`.** Line 25–26 carries the scar:
   `# Initialize MCP server - NO PROMPT PARAMETER!` / `mcp = FastMCP("proton-email")`.
   Passing extra kwargs (a prompt/instructions parameter) broke startup on this mcp version
   in the past (§B.2). Keep the constructor to the name string.
3. **Stray stdout output.** In stdio transport, stdout **is the MCP protocol channel**; any
   `print()` or library chatter on stdout corrupts framing and the client drops the server.
   All logging here is deliberately routed to stderr (lines 17–22) — keep it that way.

Discriminating experiment: the manual run from symptom 8 (same command). A clean three-line
stderr start means the module imports and starts; a traceback pinpoints the import failure;
anything appearing on **stdout** before the client speaks is a framing bug.

---

## B. Incident archaeology

History of what has actually broken here, so you don't re-litigate it. Evidence: code comments,
the single git commit `d34a476` ("Initial release ... Fixes: IMAP query sanitization, socket
timeout, TEXT/SUBJECT fallback. Adds: pytest suite (12 tests, no live bridge required)"), and
the verified facts pack of 2026-07-02.

| Incident | Root cause | Evidence | Status |
|---|---|---|---|
| IMAP special-char hang | Queries containing characters like `~` hung the IMAP connection indefinitely | Commit message of `d34a476`; the three defenses in code | **Partially fixed** — see below |
| FastMCP constructor breakage | Extra constructor arg (prompt parameter) to `FastMCP(...)` broke startup on this mcp version | Line 25 comment `NO PROMPT PARAMETER!` | Fixed; guard by convention |
| Vestigial dependencies | `secure-smtplib`, `httpx`, `python-dotenv` listed in `requirements.txt` but never imported (server uses stdlib `smtplib`; `.env` never loaded) | Verified imports in `proton_email_server.py`, 2026-07-02 | Open; removal is a Docker-build-affecting change → `proton-mcp-change-control` |
| Stats "7 days" mislabel | `SINCE <today>` counts today only; label says 7 days | Line 307 vs line 315 | **Open bug** (as of 2026-07-02) |
| `.env.example` host divergence | Template says `PROTON_BRIDGE_HOST=127.0.0.1`; real Mac deployment uses `$PROTON_MCP_HOST` | `.env.example` line 3 vs `~/.claude.json` deployment | Standing trap; template ≠ deployment record |
| Bcc "leak" false alarm | `msg['Bcc']` is set (line 223) but `smtplib.send_message` (line 244) does **not** transmit Bcc headers | Python stdlib documented behavior, verified against docs 2026-07-02 | **Not a bug — do NOT "fix" it.** Rewriting send to transmit raw headers would *create* the leak |

**The hang saga in full (incident 1):** the fix landed as three parts, all in `search_emails`
only:

1. Sanitize before touching the network — `re.sub(r'[^\w\s@.\-]', '', query)`, line 141.
2. Socket timeout — `mail.sock.settimeout(15)`, line 151, the only timeout in the file.
3. TEXT→SUBJECT fallback — lines 159–164, because Bridge rejected some TEXT searches.

**The read path was never protected.** `read_recent_emails` (line 85), `list_folders`
(line 261), `get_email_stats` (line 294), and `send_email` all run without timeouts —
the hang risk from this incident is still live there (open, as of 2026-07-02). Related
known-weak point: no tool closes its IMAP connection in a `finally` — `mail.logout()` sits on
success paths only, so connections leak on exceptions.

## Traps that cost time

- **Test env injection is import-order-sensitive.** `tests/test_proton_email_server.py` calls
  `os.environ.setdefault(...)` for all five PROTON_* vars (lines 14–18) **before**
  `import proton_email_server` (line 21), because the module reads env at import time.
  Move the import above the env block and every credential-gated test breaks. Copy this pattern
  when writing new tests.
- **pytest needs the repo venv.** Run
  `cd /Users/prestonbernstein/dev/proton-email-mcp && ./venv/bin/python -m pytest tests/ -q`
  → `12 passed` (verified 2026-07-02). System pytest lacks `mcp`/`pytest-asyncio` and fails at
  collection. There is no pytest config file; tests rely on `@pytest.mark.asyncio` markers.
- **`.env` files do nothing** (see symptom 2). Setting env in your shell also does nothing for
  the Claude Code deployment — the subprocess env comes from `~/.claude.json`.
- **`@mcp.tool()` returns the original function unchanged** (mcp 1.28.0, verified) — which is
  why tests import the module and `await` the tools directly. Handy for debugging too:
  you can call a tool function from a REPL without any MCP client.
- **All tool parameters are `str` on purpose** (MCP-client compatibility). Passing an int for
  `count`/`max_results` from hand-rolled callers is the caller's bug, not the server's.
- **Three copies of the source exist** (Mac repo = canonical; desktop
  `/home/preston/docker/proton-email-mcp` and `/opt/docker/librechat-stack/proton-email-mcp`
  are deploy artifacts; prod image built 2026-06-20). When container behavior contradicts the
  code you're reading, suspect drift — see `proton-mcp-drift-and-hardening-campaign`.

## When NOT to use this skill

This playbook is for **diagnosing failures**. Route elsewhere for:

| Need | Skill |
|---|---|
| Design decisions, invariants, known-weak points (why it is built this way) | `proton-mcp-architecture-contract` |
| Making any code/dependency/config change (incl. fixing the bugs named here) | `proton-mcp-change-control` |
| Recreating the dev env or Docker image from scratch | `proton-mcp-build-and-env` |
| Starting/stopping/redeploying either transport; full config table | `proton-mcp-run-and-operate` |
| Running connectivity/smoke/drift measurement scripts | `proton-mcp-diagnostics-and-tooling` |
| Test anatomy, adding tests, live-verification protocol | `proton-mcp-validation-and-qa` |
| Adding or changing MCP tools | `proton-mcp-extending-tools` |
| The drift-elimination and transport-hardening campaign | `proton-mcp-drift-and-hardening-campaign` |
| Proton Bridge / IMAP / SMTP / MIME / MCP-transport background | `proton-bridge-email-reference` |

## Provenance and maintenance

Written 2026-07-02 from direct code reading, two locally executed verifications, and the
verified facts pack of 2026-07-02. Desktop-side facts (container names, ports, compose paths,
librechat.yaml contents) come from the facts pack; they were not re-observed by this author.
The `nc -vz` probes in symptom 4 are standard syntax but were not executed (labeled UNVERIFIED).

Re-verify before trusting volatile claims:

- Line numbers / code claims: `sed -n '25,26p;85p;141p;151p;159,164p;307p;315p' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py`
- Test count still 12: `cd /Users/prestonbernstein/dev/proton-email-mcp && ./venv/bin/python -m pytest tests/ -q`
- Startup stderr behavior: `cd /Users/prestonbernstein/dev/proton-email-mcp && MCP_TRANSPORT=stdio PROTON_USERNAME= PROTON_PASSWORD= ./venv/bin/python proton_email_server.py </dev/null`
- Mac MCP registration (keys only, never values): `jq '.mcpServers["proton-mail"] | {command, args, env_keys: (.env | keys)}' ~/.claude.json`
- Vestigial deps still unused: `grep -nE 'import (httpx|dotenv)|secure' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` (expect no output)
- Stats bug still open: `sed -n '307p' /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py` (bug present if it says `SINCE ... datetime.now()`)
- Desktop state **[desktop]** via `ssh $PROTON_MCP_SSH_HOST`: `docker ps && ss -tln | grep -E "1143|1025|3004"`
