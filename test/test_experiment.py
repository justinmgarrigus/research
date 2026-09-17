"""Tests experiments: layout on disk, adding artifacts, and staleness."""

import json
import os
import pathlib
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import commit, write

from research import Artifact, Experiment


def make(project: Path, ident: str = "exp", **kwargs: object) -> Experiment:
    """Creates an experiment rooted at the test project."""
    return Experiment(ident, root=str(project), **kwargs)


def test_save_creates_layout(store: Path, project: Path) -> None:
    """Saving creates "<store>/<ident>/experiment.json" and nothing else."""
    exp = make(
        project, "gen-seq", name="Gen", description="Desc", sources=["main.py"]
    )
    assert not store.exists()
    exp.save()
    assert exp.path == str(store / "gen-seq")
    assert os.listdir(store) == ["gen-seq"]
    assert os.listdir(store / "gen-seq") == ["experiment.json"]
    with open(store / "gen-seq" / "experiment.json") as f:
        data = json.load(f)
    assert data == {
        "name": "Gen",
        "description": "Desc",
        "sources": ["main.py"],
        "created": exp.created,
    }
    assert data["created"] == exp.created and "T" in exp.created


def test_save_keeps_documentation(store: Path, project: Path) -> None:
    """Saving a bare "Experiment(ident)" never erases what is on disk."""
    make(project, "b", name="Name", description="D", sources=["main.py"]).save()
    created = Experiment.load("b").created

    bare = make(project, "b")
    bare.save()
    assert (bare.name, bare.description, bare.sources) == (
        "Name",
        "D",
        ["main.py"],
    )
    assert bare.created == created

    renamed = make(project, "b", name="New", sources=["lib"])
    renamed.save()
    loaded = Experiment.load("b")
    assert (loaded.name, loaded.description) == ("New", "D")
    assert loaded.sources == ["lib"]
    assert loaded.created == created


def test_list_and_load(store: Path, project: Path) -> None:
    """The store is scanned for experiment directories, ignoring the rest."""
    assert Experiment.list() == []
    for ident in ("c", "a", "b"):
        make(project, ident, name=ident.upper()).save()
    (store / "notes").mkdir()
    write(store / "notes" / "todo.txt", "...")
    (store / ".hidden").mkdir()
    write(store / ".hidden" / "experiment.json", "{}")

    exps = Experiment.list()
    assert [e.ident for e in exps] == ["a", "b", "c"]
    assert [e.name for e in exps] == ["A", "B", "C"]
    assert Experiment.load("b") == exps[1]
    with pytest.raises(FileNotFoundError):
        Experiment.load("notes")


def test_ident_validation(store: Path, project: Path) -> None:
    """Identifiers must be safe directory names."""
    for ident in ("", "hello!", "foo bar", "a/b", ".hidden"):
        with pytest.raises(ValueError):
            make(project, ident)
    with pytest.raises(ValueError):
        make(project, "ok").get("a/b")


def test_add_artifact_layout(store: Path, project: Path, media: Path) -> None:
    """An artifact is one directory: "artifact.json" plus copied files."""
    exp = make(project, "gen", sources=["main.py"])
    src = write(media / "abc.txt", "hello")
    art = Artifact(exp, "art0", {"x": 1, "file": pathlib.Path(src)})
    assert not art.exists()
    exp.add_artifact(art)
    assert art.exists()
    assert art.path == str(store / "gen" / "art0")
    assert sorted(os.listdir(art.path)) == ["abc.txt", "artifact.json"]
    assert art.props["file"] == pathlib.Path(art.path) / "abc.txt"
    assert art.code is not None and art.code.commit == exp.code.commit

    with open(store / "gen" / "art0" / "artifact.json") as f:
        data = json.load(f)
    assert data["props"] == {"x": 1, "file": {"$path": "abc.txt"}}
    assert data["timestamp"] == art.timestamp
    assert data["code"]["sources"] == ["main.py"]
    assert data["code"]["fingerprint"] == exp.code.fingerprint
    assert data["accepted"] is None

    # It is a copy: the original may go.
    shutil.rmtree(media)
    loaded = Experiment.load("gen").artifacts
    assert loaded == [art]
    assert loaded[0].props["file"].read_text() == "hello"
    assert loaded[0].timestamp == art.timestamp


def test_duplicate_and_replace(store: Path, project: Path) -> None:
    """The same identifier cannot be added twice unless replacing."""
    exp = make(project)
    art = Artifact(exp, "art", {"x": 1})
    exp.add_artifact(art)
    with pytest.raises(FileExistsError):
        exp.add_artifact(art)
    with pytest.raises(FileExistsError):
        exp.add_artifact(Artifact(exp, "art", {"x": 2}))
    assert exp.get("art").props == {"x": 1}

    exp.add_artifact(Artifact(exp, "art", {"x": 2}), replace=True)
    assert exp.get("art").props == {"x": 2}
    assert len(exp.artifacts) == 1


def test_schema(store: Path, project: Path) -> None:
    """Every artifact in an experiment has the same keys and value types."""
    exp = make(project)
    exp.add_artifact(Artifact(exp, "a", {"x": 1, "cfg": {"n": 2}, "e": None}))
    good = [
        {"x": 2, "cfg": {"n": 3}, "e": "boom"},
        {"x": None, "cfg": None, "e": None},
    ]
    for idx, props in enumerate(good):
        exp.add_artifact(Artifact(exp, f"good{idx}", props))
    bad = [
        {"x": 1},
        {"x": 1, "cfg": {"n": 2}, "e": None, "extra": 1},
        {"x": "1", "cfg": {"n": 2}, "e": None},
        {"x": 1, "cfg": {"m": 2}, "e": None},
        {"x": 1, "cfg": {"n": "2"}, "e": None},
    ]
    for idx, props in enumerate(bad):
        with pytest.raises(ValueError):
            exp.add_artifact(Artifact(exp, f"bad{idx}", props))
    assert [a.ident for a in exp.artifacts] == ["a", "good0", "good1"]


def test_delete_in_file_explorer(store: Path, project: Path) -> None:
    """Deleting directories by hand is all it takes; there is no index."""
    exp = make(project)
    exp.add_artifact(Artifact(exp, "a", {"x": 1}))
    exp.add_artifact(Artifact(exp, "b", {"x": 2}))

    shutil.rmtree(store / "exp" / "a")
    assert exp.get("a") is None
    assert not exp.exists("a")
    assert [a.ident for a in exp.artifacts] == ["b"]

    shutil.rmtree(store / "exp")
    assert Experiment.list() == []
    assert exp.artifacts == []


def test_rename_and_move_in_file_explorer(
    store: Path, project: Path, media: Path, tmp_path: Path
) -> None:
    """Renaming an artifact, or moving the whole store, keeps files valid.

    Paths inside "artifact.json" are relative to the artifact directory.
    """
    exp = make(project)
    src = write(media / "log.txt", "log")
    exp.add_artifact(Artifact(exp, "a", {"f": pathlib.Path(src)}))
    os.rename(store / "exp" / "a", store / "exp" / "a-old")
    arts = exp.artifacts
    assert [a.ident for a in arts] == ["a-old"]
    assert arts[0].props["f"] == store / "exp" / "a-old" / "log.txt"
    assert arts[0].props["f"].read_text() == "log"

    moved = tmp_path / "elsewhere"
    shutil.move(store, moved)
    os.environ["RESEARCH_PATH"] = str(moved)
    art = Experiment.load("exp").artifacts[0]
    assert art.props["f"] == moved / "exp" / "a-old" / "log.txt"
    assert art.props["f"].read_text() == "log"


def test_other_content_ignored(store: Path, project: Path) -> None:
    """Anything that is not a direct artifact directory is left alone.

    Moving an artifact into a sub-folder is how to set it aside without
    deleting it.
    """
    exp = make(project)
    exp.add_artifact(Artifact(exp, "keep", {"x": 1}))
    exp.add_artifact(Artifact(exp, "aside", {"x": 2}))
    (store / "exp" / "old").mkdir()
    shutil.move(store / "exp" / "aside", store / "exp" / "old" / "aside")
    (store / "exp" / "figures").mkdir()
    write(store / "exp" / "figures" / "fig.txt", "...")
    write(store / "exp" / "notes.md", "...")
    write(store / "exp" / ".tmp" / "artifact.json", "{}")
    assert [a.ident for a in exp.artifacts] == ["keep"]


def test_no_crossover(store: Path, project: Path) -> None:
    """Artifacts of one experiment never show up in another."""
    foo = make(project, "foo")
    bar = make(project, "bar")
    foo.add_artifact(Artifact(foo, "art1", {"x": 1}))
    bar.add_artifact(Artifact(bar, "art2", {"y": 1}))
    assert [a.ident for a in Experiment.load("foo").artifacts] == ["art1"]
    assert [a.ident for a in Experiment.load("bar").artifacts] == ["art2"]
    with pytest.raises(ValueError):
        foo.add_artifact(Artifact(bar, "art3", {"x": 1}))

    art = Artifact(None, "art4", {"x": 2})
    foo.add_artifact(art)
    assert art.experiment is foo


def test_delete(store: Path, project: Path) -> None:
    """Deleting an experiment removes only it."""
    a, b = make(project, "a"), make(project, "b")
    a.add_artifact(Artifact(a, "x", {"v": 1}))
    b.add_artifact(Artifact(b, "x", {"v": 1}))
    a.delete()
    assert not os.path.exists(a.path)
    assert [e.ident for e in Experiment.list()] == ["b"]
    b.get("x").delete()
    assert b.artifacts == []
    assert os.path.exists(b.path)


def test_stale(store: Path, project: Path) -> None:
    """Editing a declared source makes existing artifacts stale."""
    exp = make(project, sources=["main.py", "lib"])
    exp.add_artifact(Artifact(exp, "a", {"x": 1}))
    assert exp.get("a").is_stale is False
    assert exp.exists("a", allow_stale=False)
    assert exp.stale == []

    write(project / "lib" / "mod.py", "X = 2\n")
    fresh = make(project, sources=["main.py", "lib"])
    art = fresh.get("a")
    assert art.is_stale is True
    assert fresh.exists("a")  # Still there, so a plain run skips it.
    assert not fresh.exists("a", allow_stale=False)  # A strict run redoes it.
    assert fresh.stale == [art]

    fresh.add_artifact(Artifact(fresh, "a", {"x": 2}), replace=True)
    assert fresh.get("a").is_stale is False


def test_commits_do_not_make_stale(store: Path, project: Path) -> None:
    """Committing, or editing files outside the sources, changes nothing.

    This is the reason artifacts are no longer bucketed by commit hash.
    """
    exp = make(project, sources=["main.py", "lib"])
    exp.add_artifact(Artifact(exp, "a", {"x": 1}))
    write(project / "README.md", "# Edited\n")
    write(project / "plot.py", "print('plot')\n")
    commit(project, "docs and plotting")
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "empty"],
        cwd=project,
        check=True,
    )
    fresh = make(project, sources=["main.py", "lib"])
    art = fresh.get("a")
    assert art.is_stale is False
    assert art.code.commit != fresh.code.commit  # Provenance is still exact.


def test_accept(store: Path, project: Path) -> None:
    """Accepting marks artifacts valid for the current code."""
    exp = make(project, sources=["main.py"])
    exp.add_artifact(Artifact(exp, "a", {"x": 1}))
    exp.add_artifact(Artifact(exp, "b", {"x": 2}))
    original = exp.code
    write(project / "main.py", "print('main')  # A harmless comment.\n")

    fresh = make(project, sources=["main.py"])
    assert [a.ident for a in fresh.stale] == ["a", "b"]
    fresh.get("a").accept()
    assert [a.ident for a in fresh.stale] == ["b"]
    fresh.accept()
    assert fresh.stale == []

    art = make(project, sources=["main.py"]).get("a")
    assert art.is_stale is False
    assert art.accepted["fingerprint"] == fresh.code.fingerprint
    assert art.code == original  # What actually produced it is untouched.

    write(project / "main.py", "print('changed')\n")
    assert make(project, sources=["main.py"]).get("a").is_stale is True


def test_stale_unknown(store: Path, project: Path, tmp_path: Path) -> None:
    """Without a fingerprint or a repository, staleness is unknown."""
    exp = make(project)
    exp.add_artifact(Artifact(exp, "a", {"x": 1}))
    file = store / "exp" / "a" / "artifact.json"
    data = json.loads(file.read_text())
    data["code"]["fingerprint"] = None
    file.write_text(json.dumps(data))
    art = exp.get("a")
    assert art.is_stale is None
    assert exp.exists("a")
    assert not exp.exists("a", allow_stale=False)
    assert exp.stale == [art]

    outside = Experiment("exp", root=str(tmp_path))
    assert outside.get("a").is_stale is None
    with pytest.raises(FileNotFoundError):
        outside.add_artifact(Artifact(outside, "b", {"x": 1}))


def test_detached_artifact(store: Path, project: Path) -> None:
    """An artifact without an experiment has no staleness."""
    art = Artifact(None, "a", {"x": 1})
    assert not art.exists()
    assert art.is_stale is None
    with pytest.raises(RuntimeError):
        art.accept()


def test_default_root_is_caller(store: Path) -> None:
    """Without "root", the repository containing the calling module is used."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=here,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert Experiment("x").root == root
    Experiment("x").save()
    assert Experiment.load("x").root == root
    assert Experiment.list()[0].root == root


def test_code_captured_once(store: Path, project: Path) -> None:
    """The code state is captured on first use and then reused."""
    exp = make(project, sources=["main.py"])
    code = exp.code
    write(project / "main.py", "print('changed')\n")
    assert exp.code is code
    exp.add_artifact(Artifact(exp, "a", {"x": 1}))
    assert exp.get("a").code == code


def test_eq_and_repr(store: Path, project: Path) -> None:
    """Experiments compare by identifier; artifacts by experiment and ident."""
    a1, a2, b = make(project, "a"), make(project, "a"), make(project, "b")
    assert a1 == a2 and a1 != b and a1 != "a"
    assert len({a1, a2, b}) == 2
    assert Artifact(a1, "x", {}) == Artifact(a2, "x", {})
    assert Artifact(a1, "x", {}) != Artifact(b, "x", {})
    assert Artifact(a1, "x", {}) != Artifact(a1, "y", {})
    assert "a" in repr(a1) and "x" in repr(Artifact(a1, "x", {}))


def test_schema_fills_none_from_older(store: Path, project: Path) -> None:
    """A None in the newest artifact does not disable the type check."""
    exp = make(project)
    exp.add_artifact(Artifact(exp, "a", {"score": 1.0, "error": None}))
    exp.add_artifact(Artifact(exp, "b", {"score": None, "error": "boom"}))
    with pytest.raises(ValueError):
        exp.add_artifact(Artifact(exp, "c", {"score": "high", "error": None}))
    with pytest.raises(ValueError):
        exp.add_artifact(Artifact(exp, "d", {"score": None, "error": 1}))
    exp.add_artifact(Artifact(exp, "e", {"score": 0.0, "error": "x"}))


def test_schema_follows_newest(store: Path, project: Path) -> None:
    """The schema reference is the most recently written artifact.

    After a migration, an experiment may hold artifacts whose properties
    grew over time; new artifacts must match the latest form, not the
    oldest.
    """
    exp = make(project)
    exp.add_artifact(Artifact(exp, "a-old", {"x": 1}))
    write(
        store / "exp" / "z-new" / "artifact.json",
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:00",
                "code": None,
                "accepted": None,
                "props": {"x": 1, "backend": "vllm"},
            },
        ),
    )
    exp.add_artifact(Artifact(exp, "b", {"x": 2, "backend": "llamacpp"}))
    with pytest.raises(ValueError):
        exp.add_artifact(Artifact(exp, "c", {"x": 2}))
