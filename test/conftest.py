"""Fixtures shared by every test: an isolated store and a tiny git project."""

import subprocess
from pathlib import Path

import pytest


def git(project: Path, *args: str) -> str:
    """Runs a git command inside "project" and returns its output."""
    return subprocess.run(
        ["git", *args],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def write(path: Path, text: str) -> Path:
    """Writes "text" to "path", creating parent directories, and returns it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def commit(project: Path, message: str = "commit") -> str:
    """Stages and commits everything in "project", returning the hash."""
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", message)
    return git(project, "rev-parse", "HEAD").strip()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty store, pointed to by RESEARCH_PATH."""
    path = tmp_path / "store"
    monkeypatch.setenv("RESEARCH_PATH", str(path))
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git repository with one commit: "main.py", "lib/mod.py", ".gitignore".

    "*.log" files are ignored.
    """
    path = tmp_path / "project"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    write(path / "main.py", "print('main')\n")
    write(path / "lib" / "mod.py", "X = 1\n")
    write(path / "README.md", "# Project\n")
    write(path / ".gitignore", "*.log\n")
    commit(path, "initial")
    return path


@pytest.fixture
def media(tmp_path: Path) -> Path:
    """A directory of files to copy into artifacts."""
    path = tmp_path / "media"
    path.mkdir()
    return path
