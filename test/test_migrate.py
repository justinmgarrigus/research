"""Tests converting a store from the original layout."""

import json
import os
import pathlib
from pathlib import Path

import pytest
from conftest import write

from research import Experiment
from research.migrate import migrate

A_HASH = "a" * 40
B_HASH = "b" * 40


def build_old_store(old: Path) -> None:
    """Builds a store in the original layout, including its quirks.

    - "gen" was collected at two commits; "art1" appears in both (the "b"
      bucket is newer), "art2" only in the newer one.
    - "other" exists on disk but was never indexed (as happens when the
      index and the disk drift apart).
    - The index also references a bucket that is missing from disk.
    - File properties are absolute paths from another machine.
    """
    index = [
        {
            "name": "Generate",
            "ident": "gen",
            "description": "Generates things.",
            "created_timestamp": "09/10/2026 @ 17:00:34",
            "modified_timestamp": "09/10/2026 @ 17:00:34",
            "project_name": "llm-serving",
            "commit_hash": A_HASH,
            "branch": "main",
            "commit_message": "first",
        },
        {
            "name": "Generate",
            "ident": "gen",
            "description": "Generates things.",
            "created_timestamp": "09/12/2026 @ 09:00:00",
            "modified_timestamp": "09/12/2026 @ 10:00:00",
            "project_name": "llm-serving",
            "commit_hash": B_HASH,
            "branch": "feature",
            "commit_message": "second\n\nwith body",
        },
        {
            "name": "Generate",
            "ident": "gen",
            "description": "Generates things.",
            "created_timestamp": "09/13/2026 @ 09:00:00",
            "modified_timestamp": "09/13/2026 @ 10:00:00",
            "project_name": "llm-serving",
            "commit_hash": "c" * 40,
            "branch": "main",
            "commit_message": "missing on disk",
        },
    ]
    write(old / "index.json", json.dumps(index))
    write(old / ".lock", "")

    def bucket(name: str, arts: list[dict]) -> None:
        write(
            old / name / "index.json",
            json.dumps(
                {"experiment-ident": name.split("-")[1], "artifacts": arts}
            ),
        )

    old_root = "/home/elsewhere/research"
    bucket(
        "exp-gen-aaaaaaaa",
        [
            {
                "artifact-ident": "art1",
                "timestamp": "09/10/2026 @ 17:00:34",
                "properties": {
                    "x": 1,
                    "log": f"{old_root}/exp-gen-aaaaaaaa/art1/server.log",
                    "tpot": [f"{old_root}/exp-gen-aaaaaaaa/art1/tpot-0.log"],
                },
            }
        ],
    )
    write(old / "exp-gen-aaaaaaaa" / "art1" / "server.log", "old log")
    write(old / "exp-gen-aaaaaaaa" / "art1" / "tpot-0.log", "1,2,3")
    bucket(
        "exp-gen-bbbbbbbb",
        [
            {
                "artifact-ident": "art1",
                "timestamp": "09/12/2026 @ 09:30:00",
                "properties": {
                    "x": 2,
                    "log": f"{old_root}/exp-gen-bbbbbbbb/art1/server.log",
                    "tpot": [f"{old_root}/exp-gen-bbbbbbbb/art1/tpot-0.log"],
                },
            },
            {
                "artifact-ident": "art2",
                "timestamp": "09/12/2026 @ 10:00:00",
                "properties": {
                    "x": 3,
                    "log": f"{old_root}/exp-gen-bbbbbbbb/art2/server.log",
                    "tpot": [f"{old_root}/exp-gen-bbbbbbbb/art2/gone.log"],
                },
            },
        ],
    )
    write(old / "exp-gen-bbbbbbbb" / "art1" / "server.log", "new log")
    write(old / "exp-gen-bbbbbbbb" / "art1" / "tpot-0.log", "4,5,6")
    write(old / "exp-gen-bbbbbbbb" / "art2" / "server.log", "log 2")
    bucket(
        "exp-other-dddddddd",
        [
            {
                "artifact-ident": "solo",
                "timestamp": "09/11/2026 @ 12:00:00",
                "properties": {"y": "text"},
            }
        ],
    )


def test_migrate(store: Path, tmp_path: Path) -> None:
    """The converted store has the new layout and loads normally."""
    old = tmp_path / "old"
    build_old_store(old)
    lines: list[str] = []
    migrate(str(old), str(store), log=lines.append)

    assert sorted(os.listdir(store)) == ["gen", "other"]
    assert sorted(os.listdir(store / "gen")) == [
        "art1",
        "art2",
        "experiment.json",
        "superseded",
    ]
    assert os.listdir(store / "gen" / "superseded") == ["art1@aaaaaaaa"]
    assert sorted(os.listdir(store / "gen" / "art1")) == [
        "artifact.json",
        "server.log",
        "tpot-0.log",
    ]

    exp_data = json.loads((store / "gen" / "experiment.json").read_text())
    assert exp_data == {
        "name": "Generate",
        "description": "Generates things.",
        "sources": None,
        "created": "2026-09-10T17:00:34",
    }

    art1 = json.loads((store / "gen" / "art1" / "artifact.json").read_text())
    assert art1["timestamp"] == "2026-09-12T09:30:00"
    assert art1["code"] == {
        "project": "llm-serving",
        "commit": B_HASH,
        "branch": "feature",
        "message": "second\n\nwith body",
        "dirty": None,
        "sources": None,
        "fingerprint": None,
    }
    assert art1["accepted"] is None
    assert art1["props"] == {
        "x": 2,
        "log": {"$path": "server.log"},
        "tpot": [{"$path": "tpot-0.log"}],
    }
    old_art1 = json.loads(
        (
            store / "gen" / "superseded" / "art1@aaaaaaaa" / "artifact.json"
        ).read_text()
    )
    assert old_art1["props"]["x"] == 1
    assert old_art1["code"]["commit"] == A_HASH

    # A file that was missing stays a string and is reported.
    art2 = json.loads((store / "gen" / "art2" / "artifact.json").read_text())
    assert art2["props"]["tpot"] == [
        "/home/elsewhere/research/exp-gen-bbbbbbbb/art2/gone.log"
    ]
    assert any("gone.log" in line and "warning" in line for line in lines)

    # The unindexed bucket only knows its short hash.
    solo = json.loads((store / "other" / "solo" / "artifact.json").read_text())
    assert solo["code"]["commit"] == "dddddddd"
    assert solo["code"]["branch"] == ""
    assert (
        json.loads((store / "other" / "experiment.json").read_text())["name"]
        == ""
    )

    # Everything loads through the normal API.
    exps = Experiment.list(root=str(tmp_path))
    assert [e.ident for e in exps] == ["gen", "other"]
    gen = exps[0]
    assert [a.ident for a in gen.artifacts] == ["art1", "art2"]
    art = gen.get("art1")
    assert art.props["log"] == pathlib.Path(
        store / "gen" / "art1" / "server.log"
    )
    assert art.props["log"].read_text() == "new log"
    assert art.props["tpot"][0].read_text() == "4,5,6"
    assert art.is_stale is None
    assert art.code.short_commit == "bbbbbbbb"

    # The old store is untouched, and re-running skips what exists.
    assert sorted(os.listdir(old)) == [
        ".lock",
        "exp-gen-aaaaaaaa",
        "exp-gen-bbbbbbbb",
        "exp-other-dddddddd",
        "index.json",
    ]
    lines.clear()
    migrate(str(old), str(store), log=lines.append)
    assert sum("skip" in line for line in lines) == 4


def test_migrate_same_directory(tmp_path: Path) -> None:
    """In-place conversion is refused."""
    with pytest.raises(ValueError):
        migrate(str(tmp_path), str(tmp_path))
    with pytest.raises(FileNotFoundError):
        migrate(str(tmp_path / "missing"), str(tmp_path / "new"))


def test_migrate_mixed_schemas(store: Path, tmp_path: Path) -> None:
    """Experiments whose property keys changed over time are reported."""
    old = tmp_path / "old"
    build_old_store(old)
    art2 = old / "exp-gen-bbbbbbbb" / "index.json"
    data = json.loads(art2.read_text())
    data["artifacts"][1]["properties"]["backend"] = "vllm"
    art2.write_text(json.dumps(data))
    lines: list[str] = []
    migrate(str(old), str(store), log=lines.append)
    warning = next(line for line in lines if "mixes 2 property schemas" in line)
    assert "'gen'" in warning
    assert any("['backend', 'log', 'tpot', 'x']" in line for line in lines)
