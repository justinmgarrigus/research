"""Tests capturing the state of a project's code."""

import subprocess
from pathlib import Path

import pytest
from conftest import commit, git, write

from research.code import Code, find_root, fingerprint, list_files


def test_find_root(project: Path) -> None:
    """Any path inside the repository resolves to its root."""
    assert find_root(str(project)) == str(project)
    assert find_root(str(project / "lib")) == str(project)
    assert find_root(str(project / "lib" / "mod.py")) == str(project)


def test_find_root_missing(tmp_path: Path) -> None:
    """A path outside any repository raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        find_root(str(tmp_path))


def test_list_files(project: Path) -> None:
    """Sources select tracked and untracked files but never ignored ones."""
    write(project / "lib" / "new.py", "Y = 2\n")  # Untracked.
    write(project / "lib" / "out.log", "...")  # Ignored.
    assert list_files(str(project), ["lib"]) == ["lib/mod.py", "lib/new.py"]
    assert list_files(str(project), ["main.py", "lib/mod.py"]) == [
        "lib/mod.py",
        "main.py",
    ]
    assert "README.md" in list_files(str(project), ["."])


def test_list_files_missing_source(project: Path) -> None:
    """A source that matches nothing is a configuration error."""
    with pytest.raises(FileNotFoundError, match=r"nothing\.py"):
        list_files(str(project), ["nothing.py"])


def test_capture(project: Path) -> None:
    """Every provenance field is recorded."""
    head = git(project, "rev-parse", "HEAD").strip()
    code = Code.capture(str(project), ["main.py"])
    assert code.project == "project"
    assert code.commit == head
    assert code.short_commit == head[:8]
    assert code.branch == "main"
    assert code.message == "initial"
    assert code.dirty is False
    assert code.sources == ["main.py"]
    assert code.fingerprint == fingerprint(str(project), ["main.py"])
    assert Code.from_json(code.to_json()) == code


def test_capture_detached(project: Path) -> None:
    """A detached HEAD is recorded as such."""
    git(project, "checkout", "-q", "--detach")
    assert Code.capture(str(project)).branch == "detached"


def test_capture_no_commits(tmp_path: Path) -> None:
    """A repository without commits still captures (with an empty commit)."""
    path = tmp_path / "fresh"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    write(path / "a.py", "pass\n")
    code = Code.capture(str(path))
    assert code.commit == ""
    assert code.branch == "main"
    assert code.dirty is True
    assert code.fingerprint is not None


def test_commit_does_not_change_fingerprint(project: Path) -> None:
    """Committing (with no content change) leaves the fingerprint alone.

    This is the whole point: artifacts are keyed by what the code *is*, not
    by which commit it was recorded under.
    """
    before = fingerprint(str(project), ["main.py", "lib"])
    git(project, "commit", "-q", "--allow-empty", "-m", "empty")
    assert fingerprint(str(project), ["main.py", "lib"]) == before

    # Changing something outside the sources does not matter either.
    write(project / "README.md", "# Changed\n")
    commit(project, "docs")
    assert fingerprint(str(project), ["main.py", "lib"]) == before


def test_edit_changes_fingerprint(project: Path) -> None:
    """Editing a source changes the fingerprint, committed or not."""
    before = fingerprint(str(project), ["lib"])
    write(project / "lib" / "mod.py", "X = 2\n")
    edited = fingerprint(str(project), ["lib"])
    assert edited != before
    commit(project, "edit")
    assert fingerprint(str(project), ["lib"]) == edited

    # Reverting the content restores the original fingerprint.
    write(project / "lib" / "mod.py", "X = 1\n")
    assert fingerprint(str(project), ["lib"]) == before


def test_untracked_counts_ignored_does_not(project: Path) -> None:
    """A new (untracked) source file counts; an ignored file does not."""
    before = fingerprint(str(project), ["lib"])
    write(project / "lib" / "out.log", "noise")
    assert fingerprint(str(project), ["lib"]) == before
    write(project / "lib" / "new.py", "Y = 2\n")
    assert fingerprint(str(project), ["lib"]) != before


def test_dirty(project: Path) -> None:
    """The "dirty" flag reflects only the declared sources."""
    write(project / "README.md", "# Changed\n")
    assert Code.capture(str(project), ["lib"]).dirty is False
    assert Code.capture(str(project), ["."]).dirty is True
    write(project / "lib" / "new.py", "Y = 2\n")
    assert Code.capture(str(project), ["lib"]).dirty is True


def test_not_a_repository(tmp_path: Path) -> None:
    """Capturing outside a repository fails loudly."""
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse"],
            check=True,
            capture_output=True,
        )
    with pytest.raises(Exception, match=r"repository|git"):
        Code.capture(str(tmp_path))
