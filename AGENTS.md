# Core rules for agents

## Commands

- The core gate: `bash scripts/core-validate.sh` (file size, names, complexity, function length, parameters, nesting, duplication). Run it before you say a task is done.
- The other gates: `bash scripts/gha-validate.sh`, `bash scripts/docker-validate.sh`, `python3 scripts/logging-check.py .` and `bash scripts/writing-validate.sh`. The pre-commit hook and CI run all of them.
- Tests: `pytest` (install `requirements.txt`, `pytest` and `pytest-asyncio` first).
- Enable the hook once per clone: `git config core.hooksPath .githooks`. Never commit with `--no-verify`.

## Rules

- Reuse before you write. Search for an existing function or module first. Extend it instead of adding a near copy.
- Put variants in data and in generic code. Do not copy a function to change one value.
- Extract on the second real occurrence. Guessing on the first is too early. No boolean flag arguments.
- Organize by domain. Do not name things utils, helpers or managers.
- A function does one thing. Use guard clauses. Keep it under 50 lines, under 10 branches, at most 4 parameters.
- Names say what a thing is or does. No `data`, `info`, `tmp`, `handle`, `process` as a whole name.
- Fail loudly. Never swallow an error. Comments say why, not what. Delete dead code.
- A behavior change ships with a test that fails without it.
- Never edit a limit or a config to make a run pass. Fix the code. A waiver goes in `.core-allow` with a reason.
- `.core-baseline.json` lists old over-limit functions. It may shrink. It never grows.

## Traps

- `scripts/` holds the stack pack gate scripts, copied unchanged. Do not edit them. Update them from the packs. `.core-config` and `.writing-allow` leave them out of the checks, with a reason.
- `ci/` pins the gate tools: `requirements.txt` has hashes, `package-lock.json` locks jscpd. The workflow checks the sha256 of the binary tools. Change a version and its checksum together.
- The Dockerfile pins the base image by digest and runs as uid 10001. Dependabot moves the digest.
- Logs go to stderr as plain text through `logging.basicConfig`. The logging pack wants JSON lines on stdout. That change is not made yet, because it changes behavior.

## GitHub Actions

- Pin every `uses:` to a full 40-character SHA with the version in a trailing comment.
- Set `permissions: contents: read` at the top of every workflow. Set `timeout-minutes` on every job. Set `persist-credentials: false` on checkout.
- Never put `${{ github.event... }}` or `${{ inputs.x }}` inside `run:`. Pass it through `env:`.
- No `pull_request_target`. No `secrets: inherit`.

## Docker

- Pin every base image by version and digest. Run the final stage as a numeric non-root `USER`.
- Use `COPY`, not `ADD`. Keep secrets out of `ARG` and `ENV`. Keep `.dockerignore` current.
- A waiver is a comment with a reason, such as `# docker-allow: root <reason>`.

## Writing

- Short sentences, plain words, American spelling. The point goes first.
- A comment says why. It does not say what the next line does.
