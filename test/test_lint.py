"""Single test-case, just confirms the code is linted."""

import subprocess

from util.git import get_git_root


def test_lint() -> None:
    """Ensures the project is properly linted."""
    subprocess.run(["uv", "run", "ruff", "check", get_git_root()], check=True)
    subprocess.run(
        ["uv", "run", "ruff", "format", "--diff", get_git_root()], check=True
    )
