"""Tests the "research" command line."""

import os
from pathlib import Path

import pytest
from conftest import write
from test_migrate import build_old_store

from research import Artifact, Experiment
from research.cli import main


def fill(project: Path) -> Experiment:
    """Stores an experiment with two artifacts."""
    exp = Experiment("gen", name="Gen", sources=["main.py"], root=str(project))
    exp.add_artifact(Artifact(exp, "a", {"x": 1}))
    exp.add_artifact(Artifact(exp, "b", {"x": 2}))
    Experiment("empty", name="Empty", root=str(project)).save()
    return exp


def run(capsys: pytest.CaptureFixture, *argv: str) -> str:
    """Runs the CLI and returns its standard output."""
    assert main(list(argv)) == 0
    return capsys.readouterr().out


def test_ls(
    store: Path,
    project: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lists experiments, then the artifacts of one, with staleness."""
    monkeypatch.chdir(project)
    exp = fill(project)
    out = run(capsys, "ls")
    lines = out.splitlines()
    assert lines[0].split() == ["EXPERIMENT", "ARTIFACTS", "STALE", "NAME"]
    assert lines[1].split() == ["empty", "0", "0", "Empty"]
    assert lines[2].split() == ["gen", "2", "0", "Gen"]

    write(project / "main.py", "print('changed')\n")
    write(project / "lib" / "mod.py", "X = 2\n")  # Not a source: irrelevant.
    lines = run(capsys, "ls", "gen").splitlines()
    assert lines[0].split() == ["ARTIFACT", "TIMESTAMP", "COMMIT", "STATE"]
    commit = exp.code.short_commit + "*"  # Dirty when collected? No: clean.
    assert lines[1].split() == [
        "a",
        exp.get("a").timestamp,
        commit[:-1],
        "stale",
    ]
    assert lines[2].split()[0] == "b"
    assert run(capsys, "ls").splitlines()[2].split() == ["gen", "2", "2", "Gen"]
    assert run(capsys, "ls", "gen/a").splitlines()[1].split()[0] == "a"

    # Outside a repository, staleness is unknown rather than an error.
    monkeypatch.chdir(store)
    assert run(capsys, "ls", "gen").splitlines()[1].split()[-1] == "unknown"


def test_ls_dirty(
    store: Path,
    project: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An artifact collected from an uncommitted source is flagged with "*"."""
    monkeypatch.chdir(project)
    write(project / "main.py", "print('uncommitted')\n")
    exp = Experiment("gen", sources=["main.py"], root=str(project))
    exp.add_artifact(Artifact(exp, "a", {"x": 1}))
    line = run(capsys, "ls", "gen").splitlines()[1].split()
    assert line == [
        "a",
        exp.get("a").timestamp,
        exp.code.short_commit + "*",
        "current",
    ]


def test_accept(
    store: Path,
    project: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepting clears staleness for one artifact or a whole experiment."""
    monkeypatch.chdir(project)
    fill(project)
    write(project / "main.py", "print('changed')\n")
    assert run(capsys, "ls").splitlines()[2].split()[2] == "2"
    assert run(capsys, "accept", "gen/a").startswith("Accepted 1 artifact")
    assert run(capsys, "ls").splitlines()[2].split()[2] == "1"
    assert run(capsys, "accept", "gen").startswith("Accepted 2 artifact")
    assert run(capsys, "ls").splitlines()[2].split()[2] == "0"


def test_rm(
    store: Path,
    project: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removal asks for confirmation unless forced."""
    monkeypatch.chdir(project)
    fill(project)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert run(capsys, "rm", "gen/a") == "Aborted\n"
    assert os.path.exists(store / "gen" / "a")
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert run(capsys, "rm", "gen/a") == "Deleted artifact 'gen/a'\n"
    assert not os.path.exists(store / "gen" / "a")
    assert run(capsys, "rm", "-f", "gen").startswith("Deleted experiment 'gen'")
    assert not os.path.exists(store / "gen")


def test_errors(
    store: Path,
    project: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing targets are reported on stderr with a non-zero status."""
    monkeypatch.chdir(project)
    fill(project)
    for argv in (
        ["ls", "nope"],
        ["ls", "gen/nope"],
        ["rm", "-f", "gen/nope"],
        ["accept", "gen/nope"],
        ["ls", "bad ident"],
    ):
        assert main(argv) == 1
        assert capsys.readouterr().err.startswith("error:")


def test_path_and_migrate(
    store: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The "path" command prints the store; "migrate" converts into it."""
    assert run(capsys, "path") == f"{store}\n"
    old = tmp_path / "old"
    build_old_store(old)
    out = run(capsys, "migrate", str(old), str(store))
    assert "gen: 2 bucket(s)" in out
    assert sorted(os.listdir(store)) == ["gen", "other"]
