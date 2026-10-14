"""
Every LLMClient.complete() call site passes only arguments complete() accepts.

Orchestrator tests replace the client with a mock, which accepts any keyword,
so a call such as complete(..., system=PROMPT) passed them while failing on
every real request (it broke the conversation layer and scenario projection).
This test reads the call sites from the source instead.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from augur.llm.client import LLMClient

SRC = Path(__file__).parent.parent / "src" / "augur"


def _complete_calls() -> list[tuple[str, int, set[str]]]:
    calls = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "complete"
            ):
                keywords = {k.arg for k in node.keywords if k.arg is not None}
                calls.append((str(path.relative_to(SRC)), node.lineno, keywords))
    return calls


def test_call_sites_are_found() -> None:
    assert len(_complete_calls()) >= 8


def test_every_call_site_uses_accepted_keywords() -> None:
    accepted = set(inspect.signature(LLMClient.complete).parameters) - {"self"}
    for path, line, keywords in _complete_calls():
        unknown = keywords - accepted
        assert not unknown, f"{path}:{line} passes {sorted(unknown)} to complete()"
