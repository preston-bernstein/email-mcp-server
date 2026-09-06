"""
Regression test for MCP tool description quality.

FastMCP passes each tool's docstring straight through as its description,
seen by any client that calls list_tools/tools-list. A one-line docstring
gives an LLM caller no way to know limitations, error behavior, or which
sibling tool to prefer instead. This test enforces a minimum bar (arXiv
2602.14878's Purpose + Usage Guidelines) for every registered tool:

  (a) at least three sentences before any "Args:" section
  (b) the description mentions "instead", "not", or names another tool
  (c) the description is at most 1500 characters

Run: pytest tests/test_tool_descriptions.py
"""
import os
import re
import sys

import pytest

# Inject required env vars before importing the module (same convention as
# tests/test_proton_email_server.py) so import doesn't fail on missing config.
os.environ.setdefault("PROTON_USERNAME", "test@protonmail.com")
os.environ.setdefault("PROTON_PASSWORD", "test-bridge-password")
os.environ.setdefault("PROTON_BRIDGE_HOST", "127.0.0.1")
os.environ.setdefault("PROTON_BRIDGE_IMAP_PORT", "1143")
os.environ.setdefault("PROTON_BRIDGE_SMTP_PORT", "1025")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import proton_email_server as srv


def _pre_args_text(description: str) -> str:
    """Return the portion of a description before a Google-style Args: section."""
    return re.split(r"\n\s*Args:", description)[0]


def _sentence_count(text: str) -> int:
    """Rough sentence count: split on '.', '!' or '?' followed by space/end."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    return len(sentences)


@pytest.mark.asyncio
async def test_every_tool_description_meets_minimum_bar():
    tools = await srv.mcp.list_tools()
    assert tools, "expected at least one registered tool"

    failures = []
    for tool in tools:
        description = tool.description or ""
        pre_args = _pre_args_text(description)

        sentence_count = _sentence_count(pre_args)
        if sentence_count < 3:
            failures.append(
                f"{tool.name}: only {sentence_count} sentence(s) before Args: "
                f"(need >= 3)"
            )

        other_tool_names = [t.name for t in tools if t.name != tool.name]
        mentions_alternative = (
            "instead" in description.lower()
            or re.search(r"\bnot\b", description.lower())
            or any(other in description for other in other_tool_names)
        )
        if not mentions_alternative:
            failures.append(
                f"{tool.name}: description doesn't say 'instead', 'not', or "
                f"name another tool"
            )

        if len(description) > 1500:
            failures.append(
                f"{tool.name}: description is {len(description)} chars (max 1500)"
            )

    assert not failures, "\n".join(failures)
