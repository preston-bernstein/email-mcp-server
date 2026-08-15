---
name: proton-mcp-drift-and-hardening-campaign
description: Decision-gated campaign runbook for proton-email-mcp's two hardest live problems. Load when asked to fix source drift between the Mac repo and the desktop copies, set up or run a deploy/sync pipeline, rebuild and redeploy the prod container, secure or harden the IMAP/SMTP/HTTP transports, add TLS/STARTTLS to the Bridge connections, lock down or authenticate the :3004 streamable-http endpoint, or remove vestigial dependencies. Triggers - "drift", "out of sync", "redeploy", "deploy pipeline", "canonical copy", "harden", "TLS", "STARTTLS", "lock down 3004", "LAN-open", "unauthenticated endpoint", "cert pinning".
---

# proton-email-mcp — Drift & Hardening Campaign

Facts current as of **2026-07-02** (facts pack + repo inspection). Anything marked
**UNVERIFIED** must be measured during execution — never assumed. This is a runbook
to EXECUTE, phase by phase, with decision gates. Do not skip gates. Do not judge any
step "by eye" — every phase ends with a measurable success criterion.

Two tracks, independent but ordered: **Track A (kill source drift) first**, because
every Track B change ships through the same build/deploy path Track A establishes.

## Ground rules (binding for the whole campaign)

1. **Canonical source is the Mac git repo** `/Users/prestonbernstein/dev/proton-email-mcp`
   (single commit `d34a476`, 2026-06-20). Desktop copies are deploy artifacts. Never
   hand-edit a desktop copy as a "fix".
2. **All behavior-affecting changes route through `proton-mcp-change-control`** —
   code edits, requirements.txt edits, Dockerfile edits, container env changes,
   librechat.yaml changes. Read that skill before Phase A1 or any Track B option.
3. **Credentials:** never copy PROTON_USERNAME/PROTON_PASSWORD values or the Proton
   account email address into any command output you keep, doc, commit, or chat
   transcript. `docker inspect` of the prod container PRINTS credentials — when you
   must capture it, pipe to a root-only file **on the desktop** (procedure in A2.1).
4. **Desktop access:** `ssh desktop-agent` (agent user, key `~/.ssh/agent_ed25519`,
   NOPASSWD sudo). Never `preston@`.
5. **No live email sends** during this campaign. Validation uses read-only probes only.
6. **Standard validation gate** (referenced as "STANDARD GATE" below) — every phase
   ends with ALL of:
   - `cd /Users/prestonbernstein/dev/proton-email-mcp && ./venv/bin/python -m pytest tests/ -q`
     → `12 passed` (or more if tests were added; never fewer).
   - Read-only live smoke from the Mac:
     `python3 .claude/skills/proton-mcp-diagnostics-and-tooling/scripts/smoke_imap_readonly.py`
     with `PROTON_BRIDGE_HOST=10.0.0.243` and credentials sourced from
     `~/.claude.json → mcpServers.proton-mail.env` (export in-shell; do not echo) → exit 0.
   - `.claude/skills/proton-mcp-diagnostics-and-tooling/scripts/drift_check.sh` → exit 0, all MATCH.
   - The phase-specific measurable criterion stated in that phase.

## When NOT to use this skill

| You actually want | Go to sibling skill |
|---|---|
| Understand design decisions / invariants / why things are the way they are | `proton-mcp-architecture-contract` |
| Classify/gate/review a change (this campaign ROUTES THROUGH it, it doesn't replace it) | `proton-mcp-change-control` |
| Diagnose a live failure (tool errors, hangs, login failures) | `proton-mcp-debugging-playbook` |
| Recreate the venv or Docker image from scratch; env-var traps | `proton-mcp-build-and-env` |
| Just run the server / routine redeploy with no drift or hardening intent | `proton-mcp-run-and-operate` |
| Run measurement scripts standalone (connectivity, smoke, drift check) | `proton-mcp-diagnostics-and-tooling` |
| Evidence standards, test anatomy, adding tests | `proton-mcp-validation-and-qa` |
| Add or change an MCP tool | `proton-mcp-extending-tools` |
| Proton Bridge / IMAP / SMTP / MIME domain background | `proton-bridge-email-reference` |

---

# TRACK A — KILL SOURCE DRIFT

**Problem (as of 2026-07-02):** three copies of the source exist —

1. Mac git repo (canonical): `/Users/prestonbernstein/dev/proton-email-mcp`
2. Desktop copy A: `/home/preston/docker/proton-email-mcp`
3. Desktop copy B: `/opt/docker/librechat-stack/proton-email-mcp`

Divergence was **MEASURED 2026-07-02** by `drift_check.sh` (full worked example in
`proton-mcp-diagnostics-and-tooling`): **copy A is a stale pre-fix snapshot** (327-line
`proton_email_server.py` lacking the sanitizer/settimeout/TEXT-fallback fixes and the
uvicorn startup; `requirements.txt` missing `uvicorn`); **copy B matched canonical
exactly** on all three checked files; `Dockerfile` matched everywhere. Re-measure at
execution time — verdicts change the moment anyone deploys. No deploy pipeline exists.
The prod image `proton-email-mcp:latest` was built **2026-06-20T23:59** — 12 days stale
at time of writing. Which desktop copy the prod image was built from is UNVERIFIED, but
the measurement is strong evidence for copy B (INFERENCE: copy B carries the post-fix
uvicorn startup the running container needs; copy A's old `mcp.run(transport=...)` code
predates it) — still confirm via the A2.1 capture before acting.

## Phase A0 — MEASURE

A recorded measurement exists: 2026-07-02, copy A DRIFT (stale pre-fix snapshot),
copy B MATCH — see the worked example in `proton-mcp-diagnostics-and-tooling`. If
today is 2026-07-02 you may reuse it; otherwise (or if anything has been deployed
since) re-run:

```bash
cd /Users/prestonbernstein/dev/proton-email-mcp
.claude/skills/proton-mcp-diagnostics-and-tooling/scripts/drift_check.sh
```

It hashes `proton_email_server.py`, `Dockerfile`, `requirements.txt` in all three
locations (desktop via `ssh desktop-agent sudo sha256sum`, read-only).

**DECISION GATE A0:**
- Exit 0, `CONCLUSION: PASS` (all MATCH) → source drift does not exist; **skip to
  Phase A2** (you still need a designated build context and a fresh image — the
  prod image is dated 2026-06-20 regardless).
- Exit 1 with any `DRIFT` verdict → **Phase A1**.
- Exit 1 with `FAIL: cannot ssh` → fix SSH first (`ssh desktop-agent true` must
  succeed); see `proton-mcp-diagnostics-and-tooling`. Do not proceed blind.
- Any file `MISSING` in a desktop copy → treat as DRIFT → Phase A1 (a missing file
  is still a difference to reconcile, not a license to overwrite).

## Phase A1 — RECONCILE (only if drift found)

**Never blindly clobber a desktop copy.** A desktop-side difference may be an
uncommitted production patch (this project's pre-git history proves live fixes
happen at the deployment first). Diff each drifted file, desktop → stdin, Mac copy
as the file argument, so `<` lines are DESKTOP content and `>` lines are MAC content:

```bash
# Copy A vs Mac repo (repeat per drifted file: Dockerfile, requirements.txt)
ssh desktop-agent sudo cat /home/preston/docker/proton-email-mcp/proton_email_server.py \
  | diff - /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py

# Copy B vs Mac repo
ssh desktop-agent sudo cat /opt/docker/librechat-stack/proton-email-mcp/proton_email_server.py \
  | diff - /Users/prestonbernstein/dev/proton-email-mcp/proton_email_server.py
```

Read every hunk. Classify each desktop-side difference:

**DECISION GATE A1:**
- **Desktop has changes the Mac repo lacks** (new logic, changed values, extra
  files referenced) → these are **UNCOMMITTED PROD PATCHES**. Port them INTO the
  Mac repo via `proton-mcp-change-control` (classify, review, run the test suite,
  commit — attribution: Preston Bernstein only, no AI co-author trailers) BEFORE
  any sync. Only after the Mac repo contains everything of value may a desktop copy
  be overwritten.
- **Desktop differences are strictly stale** (desktop = older version of what the
  Mac repo already superseded, or whitespace-only) → record that finding in the
  change-control log, then proceed to A2. Confirm "strictly stale" by checking the
  desktop-side hunks contain nothing absent from Mac history:
  `cd /Users/prestonbernstein/dev/proton-email-mcp && git log --oneline` (expect
  only `d34a476` unless later work added commits) and `git diff` (expect clean tree).
- **Cannot tell** (e.g. both sides changed the same region) → stop; escalate to the
  owner with both hunks quoted. Do not guess.

## Phase A2 — CANONICALIZE (designate build context, sync, rebuild, redeploy)

**Recommended designated desktop build context: `/opt/docker/librechat-stack/proton-email-mcp` (copy B).**
Criteria: (a) the LibreChat stack in that directory is the consumer of the :3004
endpoint; (b) keeping build context inside the stack directory keeps one stack =
one directory; (c) `/home/preston/docker/` locations violate the owner convention
that desktop services live under service/stack paths, not preston's home.

**DECISION GATE A2-location:** accept copy B unless Phase A1 produced concrete
evidence the prod image is built from copy A by an automated process (a cron entry,
a script, a compose `build:` stanza pointing there — check
`ssh desktop-agent "sudo crontab -l -u preston; sudo grep -rn 'proton-email-mcp' /opt/docker/ /etc/systemd/system/ 2>/dev/null | grep -v Binary"`).
If such automation exists → update the automation to copy B as part of this phase,
via change control. If you cannot determine → still choose copy B, but keep copy A
synced (step A2.3 syncs both) so nothing referencing it breaks.

### A2.1 — Capture the running container's exact launch config (DO THIS BEFORE ANY rm)

**UNVERIFIED (load-bearing):** how the `proton-email-mcp` container was launched.
It is NOT in the librechat-stack or auth-stack compose files inspected 2026-07-02 —
presumed a manual `docker run`, but presumption is not capture. What IS verified
(2026-07-02): `--network host`, restart `always`, env `MCP_TRANSPORT=streamable-http`,
`PROTON_BRIDGE_HOST=localhost`, `MCP_PORT=3004`. Re-capture anyway; do not trust
this paragraph over a live inspect:

```bash
# Full inspect to a root-only file ON THE DESKTOP (contains credentials — never cat it into your transcript)
ssh desktop-agent "sudo docker inspect proton-email-mcp | sudo tee /root/proton-email-mcp.inspect.$(date +%Y%m%d).json >/dev/null && sudo chmod 600 /root/proton-email-mcp.inspect.$(date +%Y%m%d).json"

# Credential-free summary you MAY read (redacts values of PROTON_* vars)
ssh desktop-agent "sudo docker inspect proton-email-mcp --format 'Image={{.Config.Image}} Net={{.HostConfig.NetworkMode}} Restart={{.HostConfig.RestartPolicy.Name}} Mounts={{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}' && sudo docker inspect proton-email-mcp --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -E 's/^(PROTON_USERNAME|PROTON_PASSWORD)=.*/\1=<REDACTED-present>/'"

# Env file for recreation (root-only, on desktop; values never leave the machine)
ssh desktop-agent "sudo docker inspect proton-email-mcp --format '{{range .Config.Env}}{{println .}}{{end}}' | sudo tee /root/proton-email-mcp.env >/dev/null && sudo chmod 600 /root/proton-email-mcp.env"
```

**EXPECTED:** `Net=host`, `Restart=always`, env list containing
`MCP_TRANSPORT=streamable-http`, `PROTON_BRIDGE_HOST=localhost`, `MCP_PORT=3004`,
both `PROTON_*` vars `<REDACTED-present>`, no Mounts.
**If you see instead:** a compose project label
(`{{index .Config.Labels "com.docker.compose.project"}}` non-empty) → the container
IS compose-managed after all; find that compose file
(`ssh desktop-agent "sudo docker inspect proton-email-mcp --format '{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}'"`)
and do the rebuild/recreate through `docker compose` in that directory instead of
the `docker run` in A2.4. **If Mounts is non-empty** → the container bind-mounts
source or config; identify what before proceeding (a source bind-mount changes the
whole drift story — the container would run desktop files directly, and rebuild
alone won't pick up changes; record and adapt). **If either PROTON_ var missing**
→ stop; the container config differs materially from the facts pack; re-verify
before touching it.

### A2.2 — Sync canonical source from the Mac repo

Precondition: Phase A1 complete (Mac repo contains everything of value), working
tree clean (`git -C /Users/prestonbernstein/dev/proton-email-mcp status --porcelain`
→ empty output).

The agent user likely cannot write `/opt/docker/` or `/home/preston/` directly
(UNVERIFIED permissions) — stage in the agent home, then `sudo cp`:

```bash
cd /Users/prestonbernstein/dev/proton-email-mcp
rsync -av proton_email_server.py Dockerfile requirements.txt .env.example \
  desktop-agent:proton-email-mcp-staging/

ssh desktop-agent "sudo mkdir -p /opt/docker/librechat-stack/proton-email-mcp /home/preston/docker/proton-email-mcp \
  && sudo cp ~/proton-email-mcp-staging/* /opt/docker/librechat-stack/proton-email-mcp/ \
  && sudo cp ~/proton-email-mcp-staging/* /home/preston/docker/proton-email-mcp/ \
  && rm -rf ~/proton-email-mcp-staging"
```

(Both desktop copies are synced so `drift_check.sh` passes unchanged; deprecating
copy A is a separate ratchet step — A3.2.)

Verify immediately: rerun `drift_check.sh` → must exit 0, all MATCH. If not, stop
and diff again; do not build from an unverified context.

### A2.3 — Rebuild the image from the designated context

```bash
ssh desktop-agent "sudo docker build -t proton-email-mcp:latest /opt/docker/librechat-stack/proton-email-mcp"
```

**EXPECTED:** build succeeds; final line reports the image tagged. Then confirm
freshness:
`ssh desktop-agent "sudo docker inspect proton-email-mcp:latest --format '{{.Created}}'"`
→ today's date. **If build fails** on `pip install` → network/PyPI issue or a
requirements.txt change from Track B4 landed early; see `proton-mcp-build-and-env`.

### A2.4 — Recreate the container (capture-then-recreate)

Skip this subsection and use `docker compose up -d --build` in the discovered
compose dir if A2.1 revealed compose management. Otherwise:

```bash
ssh desktop-agent "sudo docker rm -f proton-email-mcp \
  && sudo docker run -d --name proton-email-mcp \
       --network host --restart always \
       --env-file /root/proton-email-mcp.env \
       proton-email-mcp:latest"
```

(`--env-file` replays the exact captured env, including credentials, without the
values ever appearing in your transcript. Image-baked vars like PATH being replayed
is harmless — identical values.)

Verify the process:

```bash
ssh desktop-agent "sudo docker ps --filter name=proton-email-mcp --format '{{.Status}}' && sudo docker logs proton-email-mcp --tail 20 2>&1 | grep -v -i password"
```

**EXPECTED:** status `Up ...`; logs show
`Starting Proton Bridge Email MCP server...` and a uvicorn line
`Uvicorn running on http://0.0.0.0:3004` (0.0.0.0 until Track B1 changes it) with
NO `PROTON_USERNAME not set` / `PROTON_PASSWORD not set` warnings.
**If you see the credential warnings instead** → the env-file capture missed the
vars; restore from the inspect JSON at `/root/proton-email-mcp.inspect.<date>.json`
(on-desktop root shell only) and recreate again.

Endpoint liveness (from the desktop; measurable, no UI):

```bash
ssh desktop-agent "curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:3004/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-03-26\",\"capabilities\":{},\"clientInfo\":{\"name\":\"probe\",\"version\":\"0\"}}}'"
```

**EXPECTED:** `200`. Any HTTP status proves the server parses HTTP (see
`check_connectivity.sh` header for the rationale); `000`/refused means DOWN — check
container logs.

### A2.5 — Restart LibreChat and verify tools

LibreChat's container name is **UNVERIFIED** — discover, then restart:

```bash
ssh desktop-agent "sudo docker ps --format '{{.Names}}' | grep -i -E 'librechat|chat'"
ssh desktop-agent "sudo docker restart <discovered-name>"
ssh desktop-agent "sudo docker logs <discovered-name> --since 3m 2>&1 | grep -i -E 'proton|mcp' | head -20"
```

**EXPECTED:** log lines indicating the `proton-email` MCP server initialized /
tools loaded (exact wording UNVERIFIED — any "proton-email ... initialized/
connected/tools" line counts; an "error/failed/refused" line for proton-email does
not). **If errors mention SSRF/domain** → confirm `librechat.yaml` still has
`mcpSettings.allowedDomains` including `http://localhost:3004` (required for
localhost MCP URLs). **If no proton lines at all** → confirm `librechat.yaml` still
contains `mcpServers.proton-email: url http://localhost:3004/mcp, type
streamable-http`, then re-restart.

**Phase A2 success criterion (measurable):** A2.4 curl returns `200`; image
`Created` date == today; LibreChat logs show proton-email MCP init with no errors;
STANDARD GATE passes.

## Phase A3 — RATCHET (make drift impossible to reintroduce silently)

### A3.1 — Post-deploy check + promotion checklist

- Run `drift_check.sh` → exit 0, all MATCH.
- Add to the promotion checklist in `proton-mcp-change-control` (via that skill's
  own procedure): "every deploy = sync from Mac repo → drift_check MATCH → rebuild
  → recreate → curl-initialize 200 → LibreChat log check". No deploy is complete
  until drift_check passes.

### A3.2 — (Optional ratchet, separate change-control item) Deprecate copy A

End state: exactly two copies (Mac canonical + one desktop build context). Steps:
(1) update `drift_check.sh` (diagnostics skill, via change control) to drop or
make optional `DESKTOP_A`; (2) confirm nothing references copy A (rerun the A2
grep for automation); (3)
`ssh desktop-agent "sudo mv /home/preston/docker/proton-email-mcp /home/preston/docker/proton-email-mcp.DEPRECATED-$(date +%Y%m%d)"`.
Order matters: script first, or drift_check starts failing with MISSING.

**Phase A3 / Track A success criterion:** `drift_check.sh` exit 0 all-MATCH AND
`docker inspect proton-email-mcp:latest --format '{{.Created}}'` date == the deploy
date. Both machine-checkable.

---

# TRACK B — TRANSPORT HARDENING (ranked menu)

All four options are **OPEN/CANDIDATE as of 2026-07-02** — none implemented. Each
follows: theory obligation → verification experiment → implementation sketch →
validation. Every one routes through `proton-mcp-change-control`. Do them in order
of rank unless the owner directs otherwise. Track A must be green first (you need a
trustworthy build path before shipping code/config changes).

Current weak points being attacked (verified 2026-07-02):
- `:3004` streamable-http is **unauthenticated and bound 0.0.0.0** on the desktop —
  any LAN device can send email as the account. (B1, B3)
- IMAP Mac→desktop is **plaintext** `imaplib.IMAP4` over the LAN — the Bridge
  password crosses the LAN unencrypted. (B2)
- SMTP uses STARTTLS but `CERT_NONE` — encrypted, server identity unauthenticated. (B2)
- requirements.txt carries vestigial deps. (B4)

## B1 — Stop :3004 being LAN-open (cheapest, biggest win)

**Theory obligation:** the MCP container is `--network host` (verified), so
`MCP_HOST` controls which desktop interface uvicorn binds. Binding `127.0.0.1`
closes LAN access. The Mac's Claude Code deployment is stdio direct to the Bridge
(ports 1143/1025), NOT :3004 — so closing :3004 to the LAN cannot break the Mac.
The only :3004 consumer is LibreChat via `http://localhost:3004/mcp`. Whether
"localhost" in librechat.yaml resolves to the desktop host depends on LibreChat's
own network mode — **UNVERIFIED**.

**Verification experiment (PREREQUISITE — decision gate):**

```bash
ssh desktop-agent "sudo docker inspect <librechat-container-name> --format '{{.HostConfig.NetworkMode}}'"
```

(discover the name as in A2.5 first)

- **EXPECTED case 1 — `host`:** localhost inside LibreChat == desktop loopback.
  Binding `MCP_HOST=127.0.0.1` works and `librechat.yaml`'s localhost URL keeps
  working unchanged. → Proceed to implementation.
- **Case 2 — bridge / a named network:** then `localhost` inside the LibreChat
  container is the LibreChat container itself — which contradicts the observed fact
  that LibreChat reaches :3004 today. **Observation contradicts theory → investigate
  before changing anything.** Candidate explanations to check in
  `/opt/docker/librechat-stack/docker-compose.yml` (filename UNVERIFIED — `ls` the
  dir): `extra_hosts: host.docker.internal:host-gateway` with the yaml actually
  saying `host.docker.internal` not `localhost` (re-read `librechat.yaml` — the
  facts pack recorded `localhost`, dated 2026-07-02, but re-verify);
  `network_mode: host` on just the api service; a proxy/sidecar. Resolve the
  contradiction, THEN decide: if LibreChat reaches the host via the docker bridge
  gateway (e.g. 172.17.0.1), binding 127.0.0.1 **breaks LibreChat** — instead bind
  `MCP_HOST` to the docker bridge gateway IP, or better, attach the MCP container
  to LibreChat's compose network and remove host networking entirely (bigger
  change; classify accordingly).
- **Case 3 — cannot reach :3004 from inside LibreChat at all** (tools were actually
  broken before you started): record it, fix Track A first, re-run this gate.

**Implementation sketch (case 1):** append/override `MCP_HOST=127.0.0.1` in the
container env — with manual `docker run`, add `-e MCP_HOST=127.0.0.1` AFTER
`--env-file` in the A2.4 recreate command (later `-e` wins over env-file); with
compose, set it in the compose stanza. Do NOT edit the Dockerfile's
`ENV MCP_HOST=0.0.0.0` default — the image should stay bind-configurable; the
deployment sets the binding. Record the override in change control so the next
A2.1-style capture preserves it (the env-file recapture will now contain it —
self-preserving).

**Validation (measurable):**
- From the Mac: `nc -z -w 3 10.0.0.243 3004` → **FAILS** (non-zero exit).
- From the desktop: the A2.4 curl-initialize against `http://localhost:3004/mcp` → `200`.
- LibreChat log check (A2.5 procedure) shows proton-email initialized, no errors.
- STANDARD GATE.

## B2 — TLS to the Bridge for IMAP (and SMTP cert decision)

**Theory obligation:** Bridge's 1143 is plain IMAP with STARTTLS *optionally*
available. **UNVERIFIED whether this Bridge build accepts STARTTLS on 1143.** Must
be measured before writing any code.

**Verification experiment (run from the Mac):**

```bash
openssl s_client -connect 10.0.0.243:1143 -starttls imap </dev/null
```

- **EXPECTED (supported):** certificate details print (subject/issuer — Bridge
  generates a self-signed cert), handshake completes, `Verify return code` will be
  self-signed-related (18/19/21) — that is fine, it proves STARTTLS works.
- **If instead:** immediate error / `unable to negotiate` / connection closed after
  the STARTTLS command → this Bridge build does not offer STARTTLS on 1143 →
  **B2-IMAP is dead for now**; record the finding (dated) in the architecture
  contract and skip to the SMTP decision below.

**Implementation sketch (if supported):** in `proton_email_server.py`, replace each
of the four bare `imaplib.IMAP4(PROTON_BRIDGE_HOST, PROTON_BRIDGE_IMAP_PORT)` call
sites (read_recent_emails L85, search_emails L150, list_folders L261,
get_email_stats L294) with a single helper, e.g.:

```python
def _imap_connect():
    mail = imaplib.IMAP4(PROTON_BRIDGE_HOST, PROTON_BRIDGE_IMAP_PORT)
    mail.starttls(ssl_context=_imap_ctx)
    return mail
```

where `_imap_ctx` is built once at module level. **Cert-trust decision (applies to
BOTH the new IMAP context and SMTP's existing `_ctx` at L230-232, currently
`CERT_NONE`):**

| Option | Properties | Choose when |
|---|---|---|
| `CERT_NONE` + encryption | Stops passive LAN sniffing of the Bridge password; no MITM protection | Minimum acceptable step; smallest diff |
| Pin the Bridge cert | `load_verify_locations(<bridge-cert>.pem)`, `verify_mode=CERT_REQUIRED`, `check_hostname=False` (CN is UNVERIFIED, likely not 10.0.0.243); stronger — MITM-resistant | Preferred end state; adds maintenance (cert lives in the Bridge volume and changes if Bridge regenerates it) |

To pin: the cert lives in the Bridge data volume
`/opt/docker/auth-stack/data/protonmail-bridge` (exact filename UNVERIFIED —
locate with `ssh desktop-agent "sudo find /opt/docker/auth-stack/data/protonmail-bridge -name '*.pem' 2>/dev/null"`;
the cert is public material and safe to copy — the KEY file is not; copy only the
cert). Ship it to both deployments and reference via a new env var
(e.g. `PROTON_BRIDGE_CA_FILE`, default empty = CERT_NONE behavior) so the change is
config-gated and reversible.

Keep the constructor discipline: FastMCP takes the name string only (line-26
"NO PROMPT PARAMETER!" incident) — this change must not touch it.

**Validation (measurable):**
- `pytest tests/ -q` → 12+ passed. Theory: tests mock `imaplib.IMAP4`, so the added
  `starttls()` lands on a MagicMock and passes silently — **verify, don't assume**;
  if a test asserts exact call sequences it may break and needs a change-control-
  reviewed test update.
- Read-only live smoke (STANDARD GATE) still exits 0 — note
  `smoke_imap_readonly.py` itself uses plain IMAP; it still proves Bridge login
  works. Additionally prove the server path: run the Mac stdio server against the
  live Bridge and call `list_folders` (read-only) → folder list returned, no TLS
  errors.
- Packet-level proof (optional but decisive): on the desktop,
  `ssh desktop-agent "sudo timeout 15 tcpdump -i any -c 20 -A port 1143 2>/dev/null | grep -c LOGIN"`
  while a read tool runs from the Mac → expect `0` (credentials no longer visible
  in cleartext). Pre-change, the same capture shows the LOGIN command.
- STANDARD GATE.

## B3 — Auth in front of :3004 (only if B1 is insufficient)

Trigger: remote/off-LAN access to the LibreChat MCP endpoint is wanted, or B1's
loopback binding is impossible (B1 case 2 with no clean network fix).

**Theory obligation / facts:** a `Caddyfile` exists in
`/opt/docker/librechat-stack/` (verified present 2026-07-02; **contents
UNVERIFIED**). LibreChat supports custom headers on MCP server entries —
**UNVERIFIED; check current LibreChat docs (librechat.ai → MCP servers config)
before choosing this option.** If header support is absent in the deployed
LibreChat version, B3 is not viable without upgrading LibreChat — a much bigger
change; stop and re-scope.

**Verification experiments:**
1. `ssh desktop-agent "sudo cat /opt/docker/librechat-stack/Caddyfile"` — learn
   what Caddy already serves and whether it's even running
   (`sudo docker ps --format '{{.Names}}' | grep -i caddy`).
2. LibreChat docs check for `headers:` under `mcpServers` (and env-var
   interpolation for the token so no secret lands in librechat.yaml — if
   interpolation isn't supported, B3 violates the no-credentials-in-yaml rule →
   reject or redesign).

**Implementation sketch:** Caddy route `:3005 → localhost:3004` requiring a bearer
token (Caddy `header` matcher) or basic auth; MCP container stays loopback-bound
(B1); librechat.yaml points at the Caddy route with the auth header; token stored
in the LibreChat stack's env file, never in the repo, never in librechat.yaml
literally, never in this skill.

**Validation (measurable):** curl-initialize WITHOUT the header → 401/403;
WITH the header → 200; `nc -z -w 3 10.0.0.243 3004` from the Mac fails (B1 still
holds); LibreChat tools work; STANDARD GATE.

## B4 — Dependency hygiene (cheap, do alongside any image rebuild)

Verified 2026-07-02: `proton_email_server.py` imports stdlib `smtplib` only —
`secure-smtplib` is vestigial; `httpx` and `python-dotenv` are never imported
(python-dotenv is never loaded — the server reads process env only). Behavior-
affecting (changes the Docker build) → change control.

**Implementation sketch:**
- `requirements.txt` → exactly: `mcp[cli]>=1.2.0` and `uvicorn`. (httpx remains
  available transitively via mcp if anything ever needs it; do not pin it yourself.)
- New `requirements-dev.txt` → `pytest`, `pytest-asyncio` (venv versions as of
  2026-07-02: pytest 9.1.1, pytest-asyncio 1.4.0 — pin `>=` those).

**Validation gate (measurable):** fresh image builds
(`ssh desktop-agent "sudo docker build -t proton-email-mcp:latest /opt/docker/librechat-stack/proton-email-mcp"`
after syncing per A2.2) → success; container recreated per A2.4 → curl-initialize
200; `./venv/bin/python -m pytest tests/ -q` → 12+ passed; STANDARD GATE. If the
image build fails on a missing transitive dep → revert requirements.txt (the diff
is two removed lines) and record which package was load-bearing after all.

---

## FENCED-OFF WRONG PATHS (do not do these)

- **Do NOT switch to `imaplib.IMAP4_SSL` on port 1143.** Bridge's 1143 is
  plain-with-optional-STARTTLS, not implicit TLS — IMAP4_SSL speaks the wrong
  handshake and will fail or hang. STARTTLS upgrade (B2) is the correct shape.
- **Do NOT expose Bridge ports (1143/1025) beyond the LAN** — no port-forwards, no
  tailscale-serve of raw Bridge ports.
- **Do NOT put credentials in librechat.yaml, this repo, or any skill/doc.**
  Credential locations stay: Mac `~/.claude.json`, desktop container env, Bridge
  volume.
- **Do NOT "fix" send_email's Bcc handling.** `smtplib.send_message` does not
  transmit Bcc headers (verified against Python docs) — setting `msg['Bcc']` does
  not leak BCC recipients. It only looks like a bug.
- **Do NOT hand-edit desktop copies** to make drift_check pass — sync flows Mac → desktop only.

## VALIDATION & PROMOTION (applies to every phase)

No phase is done "by eye". Every phase ends with the STANDARD GATE (pytest 12+
green, read-only live smoke exit 0, drift_check all-MATCH) plus that phase's own
measurable criterion. Any code/config/dependency change additionally follows
`proton-mcp-change-control` classification and review before it ships. Live email
sends are never part of validation.

## Provenance and maintenance

- Sources: verified facts pack dated **2026-07-02** (topology §5, security §8,
  incidents §7); direct read of `proton_email_server.py` (346 lines, line numbers
  cited are from commit `d34a476`), `Dockerfile`, `requirements.txt`, and
  `proton-mcp-diagnostics-and-tooling/scripts/` (drift_check.sh,
  check_connectivity.sh, smoke_imap_readonly.py) on 2026-07-02.
- Load-bearing UNVERIFIED facts this runbook gates on (re-verify at execution
  time; each has an explicit decision gate above): container launch method (A2.1);
  LibreChat container name and NetworkMode (A2.5, B1); Bridge STARTTLS support on
  1143 (B2); Bridge cert filename/CN in the Bridge volume (B2); Caddyfile contents
  and LibreChat MCP header support (B3); desktop write permissions for the agent
  user (A2.2); current drift state (A0 — last measured 2026-07-02: copy A stale,
  copy B matching canonical; re-measure at execution time).
- Volatile facts are date-stamped inline; if today is materially later than
  2026-07-02, re-run Phase A0 and re-verify §5-derived claims before executing
  anything destructive.
- Update this skill when: a Track B option is implemented (move it from
  OPEN/CANDIDATE to DONE with date and evidence), copy A is deprecated (rewrite the
  three-copies framing), a deploy pipeline is automated (Phase A2 becomes "run the
  pipeline"), or the container becomes compose-managed (A2.1/A2.4 simplify).
- On conflict with `proton-mcp-change-control` (process) or
  `proton-mcp-architecture-contract` (invariants), those win; this skill is the
  campaign sequencing layer.
