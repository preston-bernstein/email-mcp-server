#!/usr/bin/env python3
"""Audit Dockerfiles and Compose files for the mistakes agents make most. Pack: docker.

    docker-check.py [ROOT]

Dockerfiles are read as text (standard library only). Compose files are normalized by the real
`docker compose config --format json --no-interpolate --no-env-resolution`, which needs the docker CLI but no daemon.
Exit 0 when clean, 1 with one line per problem, 127 when compose files exist and docker is missing.

Dockerfile checks: base image pinned by digest (not latest, not untagged); the final stage sets a non-root USER;
COPY not ADD; apt-get update and install in one RUN with --no-install-recommends; apk add --no-cache; no download
piped into a shell; no secret-looking ARG or ENV; a .dockerignore next to the Dockerfile.
Compose checks: image pinned by digest; a restart policy; a healthcheck; a memory limit; not privileged; no host
network or docker.sock mount; no literal secret in environment.

A finding is waived with a comment in a Dockerfile (`# docker-allow: unpinned <reason>` on the line before the FROM,
or `# docker-allow: root <reason>` anywhere), or with `x-docker-allow: [no-restart, no-healthcheck, no-limits,
unpinned, privileged, host-network, docker-sock]` on a Compose service.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {"node_modules", "vendor", "__pycache__", "dist", "build", "venv", "fixtures", "testdata"}
SECRET_NAME = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|CREDENTIAL)", re.I)
DOCKERFILE_NAME = re.compile(r"^(Dockerfile(\..+)?|.+\.[Dd]ockerfile)$")
COMPOSE_NAME = re.compile(r"^(docker-compose|compose)([.-].+)?\.ya?ml$")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def walk(root: Path):
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).parts
        if any(part.startswith(".") or part in SKIP_DIRS for part in rel[:-1]) or not p.is_file():
            continue
        yield p


def image_problem(image: str, allowed: bool) -> str | None:
    if allowed:
        return None
    name = image.rsplit("/", 1)[-1]
    has_digest = bool(DIGEST.search(image))
    bare = DIGEST.sub("", image)
    tag = bare.rsplit(":", 1)[1] if ":" in bare.rsplit("/", 1)[-1] else None
    if tag == "latest":
        return f"image '{image}' uses the latest tag; pin a version and a digest"
    if tag is None and not has_digest:
        return f"image '{image}' is untagged; pin a version and a digest"
    if not has_digest:
        return f"image '{image}' is not pinned by digest (add @sha256:...; Dependabot keeps it current)"
    return None


def instructions(text: str):
    """Yield (line_number, keyword, rest, allow_set) with continuation lines joined."""
    raw = text.splitlines()
    allow: set[str] = set()
    i = 0
    while i < len(raw):
        line = raw[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("#"):
            m = re.search(r"docker-allow:\s*([\w-]+)", stripped)
            if m:
                allow.add(m.group(1))
            i += 1
            continue
        start = i
        joined = stripped
        while joined.endswith("\\") and i + 1 < len(raw):
            i += 1
            nxt = raw[i].strip()
            if nxt.startswith("#"):
                continue
            joined = joined[:-1].rstrip() + " " + nxt
        keyword, _, rest = joined.partition(" ")
        yield start + 1, keyword.upper(), rest.strip(), allow
        allow = set()
        i += 1


def check_dockerfile(path: Path, root: Path) -> list[str]:
    rel = path.relative_to(root)
    text = path.read_text()
    out: list[str] = []
    file_allow = set(re.findall(r"docker-allow:\s*([\w-]+)", text))
    stages: set[str] = set()
    user: str | None = None
    saw_from = False

    def add(n: int, msg: str) -> None:
        out.append(f"{rel}:{n}: {msg}")

    for n, kw, rest, allow in instructions(text):
        if kw == "FROM":
            saw_from = True
            toks = [t for t in rest.split() if not t.startswith("--")]
            image = toks[0] if toks else ""
            if len(toks) >= 3 and toks[1].upper() == "AS":
                stages.add(toks[2].lower())
            user = None
            if image.lower() == "scratch" or image.lower() in stages - {toks[2].lower() if len(toks) >= 3 else ""} or "$" in image:
                continue
            problem = image_problem(image, "unpinned" in allow)
            if problem:
                add(n, problem)
        elif kw == "USER":
            user = rest.split()[0].split(":")[0] if rest.split() else None
        elif kw == "ADD":
            src = rest.split()[0] if rest.split() else ""
            if not src.startswith(("http://", "https://")):
                add(n, "ADD of a local path; use COPY (ADD also unpacks archives and fetches URLs)")
        elif kw in {"ENV", "ARG"}:
            name = re.split(r"[=\s]", rest, maxsplit=1)[0]
            if SECRET_NAME.search(name):
                add(n, f"{kw} {name} looks like a secret and persists in the image history; use RUN --mount=type=secret")
        elif kw == "RUN":
            if re.search(r"apt-get\s+update", rest) and not re.search(r"apt-get\s+(-\S+\s+)*install", rest):
                add(n, "apt-get update in its own RUN caches a stale index; run it in the same RUN as apt-get install")
            if re.search(r"apt-get\s+(-\S+\s+)*install", rest) and "--no-install-recommends" not in rest:
                add(n, "apt-get install without --no-install-recommends pulls in extra packages")
            if re.search(r"\bapk\s+add\b", rest) and "--no-cache" not in rest:
                add(n, "apk add without --no-cache keeps the package index in the layer")
            if re.search(r"(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z)?sh\b", rest):
                add(n, "RUN pipes a download into a shell; download, verify a checksum, then run")
    if saw_from and "root" not in file_allow and (user is None or user.lower() in {"root", "0"}):
        out.append(f"{rel}: the final stage runs as root; add USER <non-root uid> (or `# docker-allow: root <reason>`)")
    if not (path.parent / ".dockerignore").is_file() and not (path.parent / f"{path.name}.dockerignore").is_file():
        out.append(f"{rel}: no .dockerignore next to the Dockerfile (the build context sends .git, secrets and caches)")
    return out


def check_compose(path: Path, root: Path) -> list[str]:
    rel = path.relative_to(root)
    res = subprocess.run(["docker", "compose", "-f", str(path), "config", "--format", "json",
                          "--no-interpolate", "--no-env-resolution"], capture_output=True, text=True)
    if res.returncode != 0 and "too many colons" in res.stderr:
        # `${VAR:-/some/path}:/mount` breaks --no-interpolate. Check a copy with the defaults filled in; the copy keeps every
        # other key (including x-docker-allow), which an interpolated `compose config` would drop.
        text = re.sub(r"\$\{[A-Za-z_]\w*:?-([^}$]*)\}", r"\1", path.read_text())
        tmp = path.with_name(".docker-check-" + path.name)
        try:
            tmp.write_text(text)
            res = subprocess.run(["docker", "compose", "-f", str(tmp), "config", "--format", "json",
                                  "--no-interpolate", "--no-env-resolution"], capture_output=True, text=True)
        finally:
            tmp.unlink(missing_ok=True)
    if res.returncode != 0:
        return [f"{rel}: not a valid Compose file: {res.stderr.strip().splitlines()[-1] if res.stderr.strip() else 'error'}"]
    services = json.loads(res.stdout).get("services") or {}
    out: list[str] = []
    for name, svc in sorted(services.items()):
        allow = set(svc.get("x-docker-allow") or [])

        def add(msg: str) -> None:
            out.append(f"{rel}:{name}: {msg}")

        if svc.get("image"):
            problem = image_problem(svc["image"], "unpinned" in allow)
            if problem:
                add(problem)
        if svc.get("restart") in (None, "no", "") and "no-restart" not in allow:
            add("no restart policy (set restart: unless-stopped, or x-docker-allow: [no-restart] for a one-shot job)")
        hc = svc.get("healthcheck")
        if (not hc or hc.get("disable")) and "no-healthcheck" not in allow:
            add("no healthcheck (add one, or x-docker-allow: [no-healthcheck] with a reason)")
        limits = ((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}
        if not (svc.get("mem_limit") or limits.get("memory")) and "no-limits" not in allow:
            add("no memory limit (set mem_limit, or x-docker-allow: [no-limits])")
        if svc.get("privileged") in (True, "true") and "privileged" not in allow:
            add("privileged: true gives the container the host's devices and capabilities")
        if svc.get("network_mode") == "host" and "host-network" not in allow:
            add("network_mode: host removes network isolation")
        if "docker.sock" in json.dumps(svc.get("volumes") or []) and "docker-sock" not in allow:
            add("mounts docker.sock, which is root on the host")
        env = svc.get("environment") or {}
        if isinstance(env, list):
            env = dict(e.split("=", 1) if "=" in e else (e, None) for e in env)
        for key, value in env.items():
            if SECRET_NAME.search(key) and not re.search(r"(_|^)FILE$|SECRETFILE$", key) and value not in (None, "") and not str(value).startswith("${"):
                add(f"literal secret in environment {key}; reference ${{VAR}} or use Compose secrets")
    return out


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    dockerfiles = [p for p in walk(root) if DOCKERFILE_NAME.match(p.name)]
    composes = [p for p in walk(root) if COMPOSE_NAME.match(p.name)]
    if not dockerfiles and not composes:
        print("docker-check: no Dockerfiles or Compose files, nothing to check")
        return 0
    if composes and shutil.which("docker") is None:
        print("docker-check: missing tool: docker (needed to normalize Compose files)", file=sys.stderr)
        return 127
    problems: list[str] = []
    for f in dockerfiles:
        problems += check_dockerfile(f, root)
    for f in composes:
        problems += check_compose(f, root)
    for p in problems:
        print(p)
    if not problems:
        print("docker-check: clean")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
