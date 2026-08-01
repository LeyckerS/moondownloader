"""Verify moon_cli.py invocations documented in Markdown use flags the parser accepts.

lint.yml only triggers on .py changes, so a documentation-only PR was never checked
against the real CLI parser (see #44, where README.md documented an invocation the
parser never accepted, and #56, which added this check). The real flag set is read
from `moon_cli.py --help` at test time rather than hardcoded, so adding a new flag to
moon_cli.py never requires touching this file to stay in sync.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

FENCE_RE = re.compile(r"```(?:bash|sh|text)?\n(.*?)```", re.DOTALL)
FLAG_RE = re.compile(r"(?<!-)--[a-zA-Z][a-zA-Z-]*|(?<!-)-[a-zA-Z](?![a-zA-Z-])")


def _real_flags() -> set[str]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "moon_cli.py"), "--help"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return set(FLAG_RE.findall(result.stdout))


def _tracked_md_files() -> list[pathlib.Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line.strip()]


def _logical_lines(block: str):
    """Yield (line_offset, joined_text) for each line in a fenced code block, merging
    backslash-continued lines into a single logical command."""
    physical = block.split("\n")
    i = 0
    while i < len(physical):
        start = i
        parts = [physical[i].rstrip()]
        while parts[-1].endswith("\\") and i + 1 < len(physical):
            parts[-1] = parts[-1][:-1]
            i += 1
            parts.append(physical[i].rstrip())
        yield start, " ".join(p.strip() for p in parts)
        i += 1


def _bad_flags_in_file(md_path: pathlib.Path, real_flags: set[str]) -> list[str]:
    text = md_path.read_text(encoding="utf-8")
    errors: list[str] = []
    for fence in FENCE_RE.finditer(text):
        block = fence.group(1)
        block_start_line = text[: fence.start()].count("\n") + 1
        for offset, line in _logical_lines(block):
            if "moon_cli.py" not in line or "python" not in line:
                continue
            used_flags = set(FLAG_RE.findall(line))
            for flag in sorted(used_flags - real_flags):
                line_no = block_start_line + offset + 1
                rel = md_path.relative_to(ROOT).as_posix()
                errors.append(f"{rel}:{line_no}: {flag!r} is not a real moon_cli.py flag")
    return errors


def test_documented_cli_flags_exist():
    real_flags = _real_flags()
    assert real_flags, "could not read any flags from `python moon_cli.py --help`"

    all_errors: list[str] = []
    for md_file in _tracked_md_files():
        all_errors.extend(_bad_flags_in_file(md_file, real_flags))

    assert not all_errors, "\n" + "\n".join(all_errors)
