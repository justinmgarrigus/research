"""Tests artifact properties: validation, file copying, editing."""

import json
import pathlib
from pathlib import Path

import pytest
from conftest import write

from research import Artifact, Experiment


def make(project: Path) -> Experiment:
    """Creates an experiment rooted at the test project."""
    return Experiment("exp", root=str(project))


def test_copy_multiple_files(store: Path, project: Path, media: Path) -> None:
    """Every path anywhere in the properties is copied, keeping its name."""
    exp = make(project)
    paths = [write(media / f"item-{i}.md", f"item {i}") for i in range(3)]
    (media / "sub").mkdir()
    write(media / "sub" / "deep.txt", "deep")
    props = {
        "one": pathlib.Path(paths[0]),
        "many": [pathlib.Path(paths[1]), pathlib.Path(paths[2])],
        "nested": {"dir": pathlib.Path(media / "sub")},
    }
    art = Artifact(exp, "art", props)
    exp.add_artifact(art)
    base = pathlib.Path(art.path)
    assert art.props["one"] == base / "item-0.md"
    assert art.props["many"] == [base / "item-1.md", base / "item-2.md"]
    assert art.props["nested"]["dir"] == base / "sub"
    assert (base / "sub" / "deep.txt").read_text() == "deep"

    loaded = exp.get("art")
    assert loaded.props == art.props
    for idx in range(3):
        assert (base / f"item-{idx}.md").read_text() == f"item {idx}"


def test_same_name_clash(store: Path, project: Path, media: Path) -> None:
    """Two files with the same base name cannot be stored together."""
    exp = make(project)
    a = write(media / "a" / "log.txt", "a")
    b = write(media / "b" / "log.txt", "b")
    art = Artifact(exp, "art", {"a": pathlib.Path(a), "b": pathlib.Path(b)})
    with pytest.raises(FileExistsError):
        exp.add_artifact(art)
    assert exp.artifacts == []  # Nothing half-written is left behind.
    assert [
        n for n in (store / "exp").iterdir() if n.name != "experiment.json"
    ] == []


def test_missing_file(store: Path, project: Path) -> None:
    """A path that does not exist is an error, and nothing is stored."""
    exp = make(project)
    with pytest.raises(FileNotFoundError):
        exp.add_artifact(Artifact(exp, "art", {"f": pathlib.Path("nope.txt")}))
    assert exp.artifacts == []


def test_path_string_ambiguous(
    store: Path, project: Path, media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A string naming an existing path is rejected; a plain word is fine."""
    exp = make(project)
    path = write(media / "foo.txt", "abc")
    with pytest.raises(ValueError, match=r"pathlib\.Path"):
        Artifact(exp, "art", {"path": str(path)})
    with pytest.raises(ValueError, match=r"pathlib\.Path"):
        Artifact(exp, "art", {"nested": [{"path": str(path)}]})

    # A value that merely coincides with a directory name in the working
    # directory (e.g. "backend": "vllm" next to a "vllm/" checkout) is fine.
    (media / "vllm").mkdir()
    monkeypatch.chdir(media)
    Artifact(exp, "art", {"backend": "vllm"})


def test_unstorable(store: Path, project: Path) -> None:
    """Only JSON-like values and paths can be stored."""
    exp = make(project)

    class Thing:
        pass

    for props in (
        {"x": Thing()},
        {"x": {1: 2}},
        {"$path": "x"},
        {"x": {"$secret": 1}},
        {"x": {1, 2}},
        {"x": (1, 2)},  # Would come back from JSON as a list.
    ):
        with pytest.raises(ValueError):
            Artifact(exp, "art", props)
    with pytest.raises(ValueError):
        Artifact(exp, "art", [1, 2])


def test_scalar_round_trip(store: Path, project: Path) -> None:
    """Scalars keep their exact types through JSON."""
    exp = make(project)
    props = {"b": True, "i": 3, "f": 1.5, "s": "text", "n": None, "l": [1, "a"]}
    exp.add_artifact(Artifact(exp, "art", props))
    assert exp.get("art").props == props
    assert exp.get("art").props["b"] is True


def test_save_edits(store: Path, project: Path, media: Path) -> None:
    """Editing properties and saving updates "artifact.json" in place."""
    exp = make(project)
    art = Artifact(exp, "art", {"score": None, "log": None})
    exp.add_artifact(art)
    art.props["score"] = 1.0
    art.props["log"] = pathlib.Path(write(media / "run.log", "ok"))
    art.save()

    loaded = exp.get("art")
    assert loaded.props["score"] == 1.0
    assert loaded.props["log"] == pathlib.Path(art.path) / "run.log"
    assert loaded.props["log"].read_text() == "ok"
    assert loaded.timestamp == art.timestamp
    assert loaded.code == art.code


def test_edit_json_by_hand(store: Path, project: Path) -> None:
    """The JSON file can be edited directly in an editor."""
    exp = make(project)
    exp.add_artifact(Artifact(exp, "art", {"score": 0.0, "note": ""}))
    file = store / "exp" / "art" / "artifact.json"
    data = json.loads(file.read_text())
    data["props"]["note"] = "re-scored by hand"
    file.write_text(json.dumps(data, indent=2))
    assert exp.get("art").props["note"] == "re-scored by hand"


def test_save_requires_stored(store: Path, project: Path) -> None:
    """An artifact that was never added cannot be saved."""
    art = Artifact(make(project), "art", {"x": 1})
    with pytest.raises(RuntimeError):
        art.save()
