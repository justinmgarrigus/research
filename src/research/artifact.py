"""A single research result, stored as one directory."""

import json
import os
import pathlib
import shutil
from datetime import datetime
from typing import TYPE_CHECKING, Any

from util.atomic import AtomicWriteFile

from research.code import Code
from research.store import validate_ident

if TYPE_CHECKING:
    from research.experiment import Experiment

"""
Name of the file inside an artifact directory holding its metadata and
properties. A directory is an artifact if and only if it contains this file.
"""
FILENAME = "artifact.json"

"""
Key marking a file inside "artifact.json": {"$path": "server.log"} is the
file "server.log" next to the JSON file. Paths are relative, so an artifact
(or the whole store) can be moved or renamed freely.
"""
PATH_KEY = "$path"


def timestamp() -> str:
    """Returns the current local time in sortable ISO 8601 form."""
    return datetime.now().isoformat(timespec="seconds")


def check_props(props: Any, where: str = "props") -> None:  # noqa: ANN401
    """Raises ValueError unless "props" can be stored.

    Storable values are None, bool, int, float, str, pathlib.Path, and lists
    or dicts (with str keys) of storable values. A str that names an existing
    path is rejected as ambiguous: wrap it in pathlib.Path to have the file
    copied into the artifact, or make it not look like a path.
    """
    if isinstance(props, dict):
        for key, value in props.items():
            if not isinstance(key, str):
                raise ValueError(f"{where}: key {key!r} is not a string")
            if key.startswith("$"):
                raise ValueError(
                    f"{where}: key '{key}' may not start with '$' (reserved)"
                )
            check_props(value, f"{where}.{key}")
    elif isinstance(props, list):
        for idx, value in enumerate(props):
            check_props(value, f"{where}[{idx}]")
    elif isinstance(props, str):
        looks_like_path = os.sep in props or os.path.isabs(props)
        if looks_like_path and os.path.exists(props):
            raise ValueError(
                f"{where}: '{props}' is a string that names an existing path, "
                "which is ambiguous. Use a pathlib.Path to copy it into the "
                "artifact, or ensure it does not name a path."
            )
    elif props is not None and not isinstance(
        props, (bool, int, float, pathlib.Path)
    ):
        raise ValueError(
            f"{where}: a {type(props).__name__} cannot be stored in an artifact"
        )


def _is_inside(path: str, directory: str) -> bool:
    """Returns True if "path" is "directory" or lies below it."""
    path = os.path.abspath(path)
    directory = os.path.abspath(directory)
    return path == directory or path.startswith(directory + os.sep)


def copy_files(props: Any, directory: str) -> Any:  # noqa: ANN401
    """Copies every pathlib.Path in "props" into "directory".

    Returns "props" with each path replaced by its copy. Paths already inside
    "directory" are left alone. Each file keeps its base name, so two files
    with the same name cannot both be stored (FileExistsError).
    """
    if isinstance(props, dict):
        return {k: copy_files(v, directory) for k, v in props.items()}
    if isinstance(props, list):
        return [copy_files(v, directory) for v in props]
    if not isinstance(props, pathlib.Path):
        return props
    if _is_inside(props, directory):
        return props

    target = os.path.join(directory, props.name)
    if os.path.exists(target):
        raise FileExistsError(
            f"Cannot copy '{props}': '{target}' already exists (files are "
            "stored by base name, so names must be unique per artifact)"
        )
    if props.is_file():
        shutil.copy(props, target)
    elif props.is_dir():
        shutil.copytree(props, target)
    else:
        raise FileNotFoundError(f"'{props}' does not exist")
    return pathlib.Path(target)


def serialize(props: Any, directory: str) -> Any:  # noqa: ANN401
    """Turns "props" into JSON, writing paths relative to "directory"."""
    if isinstance(props, dict):
        return {k: serialize(v, directory) for k, v in props.items()}
    if isinstance(props, list):
        return [serialize(v, directory) for v in props]
    if isinstance(props, pathlib.Path):
        if not _is_inside(props, directory):
            raise ValueError(
                f"'{props}' lies outside the artifact directory '{directory}'"
            )
        return {PATH_KEY: os.path.relpath(props, directory)}
    return props


def deserialize(obj: Any, directory: str) -> Any:  # noqa: ANN401
    """Inverts "serialize()", resolving paths against "directory"."""
    if isinstance(obj, dict):
        if set(obj.keys()) == {PATH_KEY}:
            return pathlib.Path(os.path.join(directory, obj[PATH_KEY]))
        return {k: deserialize(v, directory) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deserialize(v, directory) for v in obj]
    return obj


class Artifact:
    """A single research item, like the result of one trial of an experiment.

    - experiment (Experiment | None): the experiment this belongs to.
    - ident (str): unique within the experiment; names the directory.
    - props (dict[str, Any]): queryable properties. Every artifact in an
      experiment has the same keys and value types. A pathlib.Path value is
      a file or directory that is copied into the artifact when it is added
      and points at the copy afterwards.
    - timestamp (str): when the artifact was added (ISO 8601).
    - code (Code | None): the state of the code that produced it. None until
      it is added.
    - accepted (dict[str, Any] | None): set by "accept()" when the user
      declares the artifact valid for a later code state; holds that state's
      "fingerprint", "sources", and the "timestamp" of the declaration.
    - path (str | None): the directory it is stored in, or None until added.
    """

    def __init__(
        self: "Artifact",
        experiment: "Experiment | None",
        ident: str,
        props: dict[str, Any],
        timestamp: str | None = None,
        code: Code | None = None,
        accepted: dict[str, Any] | None = None,
        path: str | None = None,
        check: bool = True,
    ) -> None:
        """Creates an artifact in memory; "Experiment.add_artifact" stores it.

        Raises ValueError if "ident" is not a legal directory name or "props"
        contains something that cannot be stored (see "check_props()").
        "check=False" skips the latter, for properties read back from the
        store: they were checked when stored, and a str in them may name a
        path that only came to exist afterwards.
        """
        validate_ident(ident, kind="artifact identifier")
        if not isinstance(props, dict):
            raise ValueError(f"props must be a dict, not {type(props)}")
        if check:
            check_props(props)

        self.experiment = experiment
        self.ident = ident
        self.props = props
        self.timestamp = timestamp if timestamp is not None else _now()
        self.code = code
        self.accepted = accepted
        self.path = path

    @classmethod
    def load(cls, experiment: "Experiment | None", path: str) -> "Artifact":
        """Reads the artifact stored in directory "path"."""
        path = os.path.abspath(path)
        with open(os.path.join(path, FILENAME)) as f:
            data = json.load(f)
        code = data.get("code")
        return cls(
            experiment=experiment,
            ident=os.path.basename(path),
            props=deserialize(data["props"], path),
            timestamp=data["timestamp"],
            code=Code.from_json(code) if code is not None else None,
            accepted=data.get("accepted"),
            path=path,
            check=False,
        )

    def _to_json(self: "Artifact", directory: str) -> dict[str, Any]:
        """Returns the contents of "artifact.json" for directory "directory"."""
        return {
            "timestamp": self.timestamp,
            "code": self.code.to_json() if self.code is not None else None,
            "accepted": self.accepted,
            "props": serialize(self.props, directory),
        }

    def _write(self: "Artifact", directory: str) -> None:
        """Copies our files into "directory" and writes "artifact.json"."""
        self.props = copy_files(self.props, directory)
        with AtomicWriteFile(os.path.join(directory, FILENAME), "w") as f:
            json.dump(self._to_json(directory), f, indent=2)
            f.write("\n")

    def exists(self: "Artifact") -> bool:
        """Returns True if our experiment already stores our identifier."""
        if self.experiment is None:
            return False
        return self.experiment.exists(self.ident)

    def save(self: "Artifact") -> None:
        """Writes changed properties back to disk.

        New pathlib.Path values are copied into the artifact first. The
        artifact must already be stored (see "Experiment.add_artifact").
        """
        if self.path is None:
            raise RuntimeError(
                f"Artifact '{self.ident}' is not stored yet; add it to an "
                "experiment instead"
            )
        check_props(self.props)
        self._write(self.path)

    def delete(self: "Artifact") -> None:
        """Removes the artifact and all of its files from the store."""
        if self.path is not None and os.path.exists(self.path):
            shutil.rmtree(self.path)
        self.path = None

    @property
    def is_stale(self: "Artifact") -> bool | None:
        """Whether the code it depends on has changed since it was produced.

        Compares the fingerprint of the experiment's sources now against the
        fingerprint recorded when the artifact was added (or last accepted).
        Returns None if that cannot be determined: the artifact has no
        fingerprint (it was migrated), it is not attached to an experiment,
        or the current directory is not inside a git repository.
        """
        reference = None
        if self.accepted is not None:
            reference = self.accepted.get("fingerprint")
        elif self.code is not None:
            reference = self.code.fingerprint
        if reference is None or self.experiment is None:
            return None
        try:
            current = self.experiment.code
        except FileNotFoundError:
            return None
        return current.fingerprint != reference

    def accept(self: "Artifact") -> None:
        """Declares the artifact valid for the code as it is right now.

        Use this after changing a source in a way that does not affect
        results (e.g. a comment), so the artifact stops being reported as
        stale without being re-collected.
        """
        if self.experiment is None:
            raise RuntimeError(f"Artifact '{self.ident}' has no experiment")
        code = self.experiment.code
        self.accepted = {
            "fingerprint": code.fingerprint,
            "sources": code.sources,
            "timestamp": _now(),
        }
        self.save()

    def __eq__(self: "Artifact", other: object) -> bool:
        """Equal if the experiment identifiers and identifiers both match."""
        if not isinstance(other, Artifact):
            return False
        mine = None if self.experiment is None else self.experiment.ident
        theirs = None if other.experiment is None else other.experiment.ident
        return mine == theirs and self.ident == other.ident

    def __hash__(self: "Artifact") -> int:
        """Hashes consistently with "__eq__"."""
        exp = None if self.experiment is None else self.experiment.ident
        return hash((exp, self.ident))

    def __repr__(self: "Artifact") -> str:
        """Returns a short description naming the experiment and artifact."""
        exp = None if self.experiment is None else self.experiment.ident
        return f"Artifact(experiment={exp!r}, ident={self.ident!r})"


"""
Alias so "Artifact.__init__" can default its "timestamp" argument without the
parameter shadowing the module-level function.
"""
_now = timestamp
