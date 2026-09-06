"""
Regression guard: no RFC1918 (private LAN) IP literal, and no
operator-specific forbidden literal, may reappear anywhere in this
repository's tracked files.

Why this exists: this repo previously had a real home-lab LAN address and
SSH alias committed into `.claude/skills/**` documentation and diagnostic
scripts. Those scripts must resolve their target host from environment
variables (PROTON_MCP_HOST / PROTON_MCP_SSH_HOST, sourced from an
uncommitted repo-root .env — see .env.example) instead of a hardcoded
address.

This test deliberately does NOT hardcode the sensitive strings it guards
against — doing so would just republish them here. Instead it:

  1. Bans RFC1918 addresses generically, by pattern (10.x, 172.16-31.x,
     192.168.x) — this half always runs.
  2. Optionally checks any exact strings supplied via the
     OPSEC_FORBIDDEN_LITERALS env var (comma-separated), for
     operator-specific secrets like a real SSH config alias that isn't
     itself an IP address. Left unset — the case for every fork and every
     contributor who isn't Preston — this half is skipped, so the suite
     stays green without ever needing his private values checked in.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
THIS_FILE = Path(__file__).resolve()

RFC1918_RE = re.compile(
    r"\b(?:"
    r"10(?:\.\d{1,3}){3}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r"|192\.168(?:\.\d{1,3}){2}"
    r")\b"
)

# Directories never worth (or safe) to scan as text.
_SKIP_DIR_PARTS = {".git", "venv", ".venv", "__pycache__", ".pytest_cache", "node_modules"}


def _tracked_files() -> list[Path]:
    """Prefer git's own view of what's committed; fall back to a filesystem
    walk (e.g. if this ever runs outside a git checkout)."""
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        paths = [REPO_ROOT / p for p in out.splitlines() if p.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        paths = [
            p
            for p in REPO_ROOT.rglob("*")
            if p.is_file() and not _SKIP_DIR_PARTS.intersection(p.parts)
        ]
    # Never scan this guard file itself — it documents the pattern shape,
    # not an offending literal, but keeping it out of scope avoids any
    # future edit here tripping its own assertion.
    return [p for p in paths if p.is_file() and p.resolve() != THIS_FILE]


def _iter_text_lines(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError):
        return
    yield from enumerate(text.splitlines(), start=1)


def test_no_rfc1918_literal_in_tracked_files():
    """No literal 10.x / 172.16-31.x / 192.168.x address may be committed.

    Docs and scripts must reference PROTON_MCP_HOST / PROTON_MCP_SSH_HOST
    (sourced from an uncommitted .env — see .env.example) instead.
    """
    offenders = []
    for path in _tracked_files():
        for lineno, line in _iter_text_lines(path):
            if RFC1918_RE.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert not offenders, (
        "Found RFC1918 literal(s) in tracked files — replace with "
        "PROTON_MCP_HOST / PROTON_MCP_SSH_HOST env-var references: "
        + ", ".join(offenders)
    )


def test_no_operator_forbidden_literals_in_tracked_files():
    """Optional extra guard for operator-specific secrets that aren't IPs
    (e.g. a real SSH config alias).

    Set OPSEC_FORBIDDEN_LITERALS to a comma-separated list of exact strings
    that must never appear committed in this repo. Never set in CI for
    forks/PRs from outside contributors, and never hardcoded here — when
    unset, this check is skipped so the suite stays green everywhere else.
    """
    raw = os.environ.get("OPSEC_FORBIDDEN_LITERALS", "")
    literals = [s.strip() for s in raw.split(",") if s.strip()]
    if not literals:
        pytest.skip("OPSEC_FORBIDDEN_LITERALS not set — skipping operator-specific check")

    offenders = []
    for path in _tracked_files():
        for lineno, line in _iter_text_lines(path):
            for literal in literals:
                if literal in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert not offenders, "Found forbidden literal(s) in tracked files: " + ", ".join(offenders)
