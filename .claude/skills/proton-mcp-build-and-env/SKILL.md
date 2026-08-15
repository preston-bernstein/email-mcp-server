---
name: proton-mcp-build-and-env
description: >
  Build and environment runbook for proton-email-mcp. Load this skill when: setting up the
  dev environment from scratch (fresh clone, new machine, recreating venv); the venv is
  broken or missing; pip install fails; pytest fails with import errors, "module not found",
  ModuleNotFoundError for pytest/pytest-asyncio/mcp; async tests are skipped or error with
  "async def functions are not natively supported"; building or rebuilding the Docker image;
  questions about which Python version to use (3.12 vs 3.14); questions about what
  requirements.txt actually contains vs what the code needs; or confusion about why .env is
  not being read.
---

# proton-mcp-build-and-env

Recreate the `proton-email-mcp` development environment and Docker image from scratch,
and avoid the known traps. Facts date-stamped **as of 2026-07-02** unless noted.

**Jargon, defined once:**
- **MCP** = Model Context Protocol. This repo is a single-file MCP server (`proton_email_server.py`) exposing Proton Mail via Proton Bridge.
- **venv** = Python virtual environment at `<repo>/venv/`, git-ignored.
- **stdio / streamable-http** = the two MCP transports the server supports; transport choice does not affect the build, only runtime (see `proton-mcp-run-and-operate`).

Repo root (canonical source): `/Users/prestonbernstein/dev/proton-email-mcp`
Remote: `git@github.com:preston-bernstein/proton-email-mcp.git`

## When NOT to use this skill

| You actually want to... | Use instead |
|---|---|
| Run the server (either transport), configure env vars, redeploy to the desktop | `proton-mcp-run-and-operate` |
| Diagnose a live failure (hangs, auth errors, tool errors) | `proton-mcp-debugging-playbook` |
| Understand design decisions / invariants / known-weak points | `proton-mcp-architecture-contract` |
| Change requirements.txt, the Dockerfile, or any code | `proton-mcp-change-control` (gates all behavior-affecting changes) |
| Add or modify tests, evidence standards | `proton-mcp-validation-and-qa` |
| Add or change MCP tools | `proton-mcp-extending-tools` |
| Run connectivity / smoke / drift-check scripts | `proton-mcp-diagnostics-and-tooling` |
| Fix source drift between Mac and desktop copies, harden transports | `proton-mcp-drift-and-hardening-campaign` |
| Learn Proton Bridge / IMAP / SMTP / MIME concepts | `proton-bridge-email-reference` |

## Python version: what is actually supported

| Environment | Python | Status |
|---|---|---|
| Dev venv on the Mac | 3.14.6 | Verified: full test suite passes (2026-07-02) |
| Prod Docker image | `python:3.12-slim` | Verified: image built and running since 2026-06-20 |
| Any other version | — | UNVERIFIED. Probably fine (stdlib `imaplib`/`smtplib` + mcp), but nothing has tested it. |

Takeaway: dev on 3.14, ship on 3.12. The code runs on both. Do not "upgrade" the
Dockerfile base or pin a different dev Python without routing through
`proton-mcp-change-control`.

## Dev environment from zero (exact commands)

Prerequisite: `python3` on PATH (the existing venv was created with Python 3.14.6; see version table above).

```bash
# 1. Clone (skip if the repo already exists at the canonical path)
git clone git@github.com:preston-bernstein/proton-email-mcp.git /Users/prestonbernstein/dev/proton-email-mcp

# 2. Create the venv (inside the repo; venv/ is git-ignored)
cd /Users/prestonbernstein/dev/proton-email-mcp
python3 -m venv venv

# 3. Install runtime deps
./venv/bin/pip install -r requirements.txt

# 4. REQUIRED EXTRA STEP — test deps are NOT in requirements.txt (see trap below)
./venv/bin/pip install pytest pytest-asyncio
```

### Verify the install

```bash
# Import check — the server's only third-party import
/Users/prestonbernstein/dev/proton-email-mcp/venv/bin/python -c "from mcp.server.fastmcp import FastMCP"

# Test suite — expect: 12 passed (as of 2026-07-02, runs in ~0.2s, no live Bridge needed)
cd /Users/prestonbernstein/dev/proton-email-mcp && ./venv/bin/python -m pytest tests/ -q
```

If both succeed, the environment is done. Runtime configuration (credentials, Bridge
host/ports) is a separate concern — see `proton-mcp-run-and-operate`.

### Key installed versions (as of 2026-07-02, verified via `pip list`)

| Package | Version | Role |
|---|---|---|
| mcp | 1.28.0 | The real dependency — FastMCP server framework |
| pytest | 9.1.1 | Test runner (NOT in requirements.txt) |
| pytest-asyncio | 1.4.0 | Async test support (NOT in requirements.txt) |
| pydantic | 2.13.4 | Transitive via mcp |
| uvicorn | 0.49.0 | Only used for streamable-http transport |
| starlette | 1.3.1 | Transitive via mcp/uvicorn |

## Known traps

### Trap 1: test deps missing from requirements.txt (verified)

`pytest` and `pytest-asyncio` are installed in the existing venv but ABSENT from
`requirements.txt`. A fresh `pip install -r requirements.txt` produces an environment
that **cannot run the test suite** (`No module named pytest`). The fix is step 4 above:

```bash
./venv/bin/pip install pytest pytest-asyncio
```

Adding a `requirements-dev.txt` would fix this permanently. That is an **open candidate
change** — route it through `proton-mcp-change-control`; do not just add the file.

### Trap 2: requirements.txt anatomy — most of it is vestigial (verified imports)

Contents of `/Users/prestonbernstein/dev/proton-email-mcp/requirements.txt`:

| Line | Verdict |
|---|---|
| `mcp[cli]>=1.2.0` | The real dependency. Everything else in the server is stdlib. |
| `uvicorn` | Needed only for `MCP_TRANSPORT=streamable-http` (the Docker/prod path). Imported lazily at startup. |
| `httpx` | VESTIGIAL — not imported by `proton_email_server.py` |
| `python-dotenv` | VESTIGIAL — not imported; the server never reads `.env` (see Trap 5) |
| `secure-smtplib` | VESTIGIAL — the code uses stdlib `smtplib`. Also NOT installed in the current venv (verified 2026-07-02, `pip show` finds nothing), yet everything works — further proof it is unused. Whether a fresh `pip install -r requirements.txt` on Python 3.14 installs it cleanly is UNVERIFIED. |

Do NOT remove the vestigial lines on your own: requirements.txt is copied into the
Docker image, so editing it is a behavior-affecting change. Route removal through
`proton-mcp-change-control`.

### Trap 3: no pytest config file — asyncio strict mode

There is no `pytest.ini`, `pyproject.toml`, or `setup.cfg` in the repo (verified).
pytest-asyncio therefore runs in its default **strict mode**: every async test function
MUST carry `@pytest.mark.asyncio` or it will be skipped/errored, not run. All 12 existing
tests in `/Users/prestonbernstein/dev/proton-email-mcp/tests/test_proton_email_server.py`
follow this pattern — copy it when adding tests (and see `proton-mcp-validation-and-qa`
for the import-order-sensitive env-var setup those tests rely on).

### Trap 4: .gitignore — never force-add ignored paths

`.gitignore` covers exactly: `venv/`, `__pycache__/`, `*.pyc`, `.env`, `*.log`.
Never `git add -f` any of these. `.env` in particular would commit credentials.

### Trap 5: the server NEVER reads .env

`python-dotenv` is listed in requirements.txt but never imported; `load_dotenv()` is
never called. The server reads configuration ONLY from the process environment at
import time. Creating a `.env` file does nothing. Environment must be injected by the
launcher:

- Claude Code (stdio): `env` block in `~/.claude.json` under `mcpServers`
- Docker: `-e VAR=value` / container env
- Shell: `export VAR=value` before launching

`.env.example` is a template of variable NAMES only, not a functioning config mechanism
(and its `PROTON_BRIDGE_HOST=127.0.0.1` does not reflect the actual Mac deployment —
see `proton-mcp-run-and-operate` for the real config table). Never write credential
values anywhere in this repo.

## Docker image build

Documented, not run here. Build command:

```bash
docker build -t proton-email-mcp:latest /Users/prestonbernstein/dev/proton-email-mcp
```

What the Dockerfile does (verified from `/Users/prestonbernstein/dev/proton-email-mcp/Dockerfile`):

- Base: `python:3.12-slim`
- Copies ONLY `requirements.txt` and `proton_email_server.py`. **Tests are not in the image** — you cannot run pytest inside the container; validate before building.
- `pip install --no-cache-dir -r requirements.txt` (so the vestigial deps from Trap 2 are baked into the image too)
- Image defaults: `MCP_TRANSPORT=streamable-http`, `MCP_HOST=0.0.0.0`, `MCP_PORT=3004`, `EXPOSE 3004`
- Credentials are NOT in the image; they must be injected at `docker run` time.

Drift-prone fact: the prod image on the desktop (desktop.example.internal) was built **2026-06-20**.
Any source change since then is not in prod until a rebuild + redeploy — see
`proton-mcp-run-and-operate` for the redeploy procedure and
`proton-mcp-drift-and-hardening-campaign` for the source-drift problem.

## Rebuild-from-scratch checklist

- [ ] Repo present at `/Users/prestonbernstein/dev/proton-email-mcp` (clone if not)
- [ ] `python3 -m venv venv` in the repo root
- [ ] `./venv/bin/pip install -r requirements.txt`
- [ ] `./venv/bin/pip install pytest pytest-asyncio` (the trap step)
- [ ] `./venv/bin/python -c "from mcp.server.fastmcp import FastMCP"` succeeds
- [ ] `./venv/bin/python -m pytest tests/ -q` → `12 passed`
- [ ] Did NOT commit `venv/`, `.env`, `__pycache__/`, `*.pyc`, `*.log`
- [ ] Did NOT write credential values into any file

## Provenance and maintenance

Facts verified 2026-07-02 by running read-only commands against the live repo and venv.
Re-verify volatile facts with:

- Python version: `/Users/prestonbernstein/dev/proton-email-mcp/venv/bin/python --version` (expect 3.14.6)
- Key package versions: `/Users/prestonbernstein/dev/proton-email-mcp/venv/bin/pip list | grep -Ei '^(mcp|pytest|pytest-asyncio|pydantic|uvicorn|starlette) '`
- Test count: `cd /Users/prestonbernstein/dev/proton-email-mcp && ./venv/bin/python -m pytest tests/ -q` (expect `12 passed`)
- Image base: `head -1 /Users/prestonbernstein/dev/proton-email-mcp/Dockerfile` (expect `FROM python:3.12-slim`)
