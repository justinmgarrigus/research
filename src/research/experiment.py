"""A group of artifacts sharing one schema, stored as one directory."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from typing import Any

from util.atomic import AtomicWriteFile

from research import artifact as artifact_module
from research.artifact import Artifact, timestamp
from research.code import DEFAULT_SOURCES, Code, find_root
from research.store import get_basedir, validate_ident

"""
Name of the file inside an experiment directory holding its metadata. A
directory is an experiment if and only if it contains this file.
"""
FILENAME = "experiment.json"


def _caller_dir() -> str:
    """Returns the directory of the module that called our caller.

    Falls back to the working directory when there is no file (e.g. a REPL).
    """
    file = sys._getframe(2).f_globals.get("__file__")
    if file is None:
        return os.getcwd()
    return os.path.dirname(os.path.abspath(file))


def _kind(value: Any) -> type:  # noqa: ANN401
    """Returns the type "value" is compared by: int and float are one kind."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float
    return type(value)


def _check_schema(new: Any, old: Any, where: str = "props") -> None:  # noqa: ANN401
    """Raises ValueError unless "new" has the same keys and types as "old".

    Dicts are compared recursively. A None on either side matches anything,
    since a property may legitimately be missing for some trials, and an int
    matches a float (a whole-number timeout is still a timeout).
    """
    if new is None or old is None:
        return
    if _kind(new) is not _kind(old):
        raise ValueError(
            f"{where}: expected a {type(old).__name__} like the existing "
            f"artifacts, got a {type(new).__name__}"
        )
    if isinstance(new, dict):
        if set(new.keys()) != set(old.keys()):
            missing = sorted(set(old.keys()) - set(new.keys()))
            extra = sorted(set(new.keys()) - set(old.keys()))
            raise ValueError(
                f"{where}: keys differ from the existing artifacts "
                f"(missing {missing}, unexpected {extra})"
            )
        for key in new:
            _check_schema(new[key], old[key], f"{where}.{key}")


def _has_none(obj: Any) -> bool:  # noqa: ANN401
    """Returns True if "obj" is None or contains a None anywhere."""
    if obj is None:
        return True
    if isinstance(obj, dict):
        return any(_has_none(v) for v in obj.values())
    return False


def _fill(reference: Any, other: Any) -> Any:  # noqa: ANN401
    """Returns "reference" with None values replaced from "other".

    Only dicts are descended; keys missing from "other" stay as they are, so
    "reference" keeps defining the key set.
    """
    if reference is None:
        return other
    if isinstance(reference, dict) and isinstance(other, dict):
        return {k: _fill(v, other.get(k)) for k, v in reference.items()}
    return reference


class Experiment:
    """A named group of artifacts that share the same queryable structure.

    Stored as the directory "RESEARCH_PATH/<ident>/", which holds
    "experiment.json" and one sub-directory per artifact. The filesystem is
    the only source of truth: deleting, renaming, or moving those directories
    in a file explorer is fully supported.

    - ident (str): unique name; names the directory.
    - name (str): human-readable title.
    - description (str): what the experiment measures and why.
    - sources (list[str] | None): paths, relative to the project's git root,
      whose contents artifacts depend on (e.g. the experiment's "main.py" and
      the library it uses). None means the whole repository. Editing a file
      under "sources" makes existing artifacts stale; committing, or editing
      anything else (plots, docs), never does.
    - created (str | None): when the experiment was first saved.
    """

    def __init__(
        self: Experiment,
        ident: str,
        name: str = "",
        description: str = "",
        sources: list[str] | None = None,
        root: str | None = None,
        created: str | None = None,
    ) -> None:
        """Describes an experiment; nothing is written until it is saved.

        "root" is any path inside the project's git repository, used to
        record which code produced each artifact. It defaults to the
        location of the module creating the experiment.
        """
        validate_ident(ident, kind="experiment identifier")
        if sources is not None and len(sources) == 0:
            raise ValueError("sources must be None or a non-empty list")
        self.ident = ident
        self.name = name
        self.description = description
        self.sources = None if sources is None else list(sources)
        self.created = created
        self._start = root if root is not None else _caller_dir()
        self._code: Code | None = None

    @classmethod
    def list(cls, root: str | None = None) -> list[Experiment]:
        """Returns every experiment in the store, sorted by identifier."""
        if root is None:
            root = _caller_dir()
        basedir = get_basedir()
        return [
            cls._load(os.path.join(basedir, name), root)
            for name in sorted(os.listdir(basedir))
            if not name.startswith(".")
            and os.path.isfile(os.path.join(basedir, name, FILENAME))
        ]

    @classmethod
    def load(cls, ident: str, root: str | None = None) -> Experiment:
        """Returns the stored experiment "ident" (FileNotFoundError if none)."""
        if root is None:
            root = _caller_dir()
        validate_ident(ident, kind="experiment identifier")
        path = os.path.join(get_basedir(), ident)
        if not os.path.isfile(os.path.join(path, FILENAME)):
            raise FileNotFoundError(f"No experiment '{ident}' in '{path}'")
        return cls._load(path, root)

    @classmethod
    def _load(cls, path: str, root: str) -> Experiment:
        """Reads the experiment stored in directory "path"."""
        with open(os.path.join(path, FILENAME)) as f:
            data = json.load(f)
        return cls(
            ident=os.path.basename(path),
            name=data.get("name", ""),
            description=data.get("description", ""),
            sources=data.get("sources"),
            root=root,
            created=data.get("created"),
        )

    @property
    def path(self: Experiment) -> str:
        """Returns the directory the experiment is (or would be) stored in."""
        return os.path.join(get_basedir(), self.ident)

    @property
    def root(self: Experiment) -> str:
        """Returns the root of the project's git repository."""
        return find_root(self._start)

    @property
    def code(self: Experiment) -> Code:
        """Returns the state of the project's code, captured once per object.

        The capture happens on first use (typically the first "exists()"
        call, at the start of a run), so later edits to the working tree do
        not change what a long-running experiment records.
        """
        if self._code is None:
            sources = self.sources or DEFAULT_SOURCES
            self._code = Code.capture(self.root, sources)
        return self._code

    def save(self: Experiment) -> None:
        """Creates the experiment directory and writes "experiment.json".

        An empty name/description, or undeclared sources, never overwrite
        values already on disk, so a bare "Experiment(ident)" can be used to
        read or extend an experiment without erasing its documentation.
        """
        os.makedirs(self.path, exist_ok=True)
        file = os.path.join(self.path, FILENAME)
        old: dict[str, Any] = {}
        if os.path.exists(file):
            with open(file) as f:
                old = json.load(f)

        self.name = self.name or old.get("name", "")
        self.description = self.description or old.get("description", "")
        if self.sources is None:
            self.sources = old.get("sources")
        self.created = old.get("created") or self.created or timestamp()
        data = {
            "name": self.name,
            "description": self.description,
            "sources": self.sources,
            "created": self.created,
        }
        with AtomicWriteFile(file, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

    @property
    def artifacts(self: Experiment) -> list[Artifact]:
        """Returns the stored artifacts, sorted by identifier.

        Only direct sub-directories containing "artifact.json" count, so
        anything else placed in the experiment directory (notes, figures, an
        artifact moved into a sub-folder to set it aside) is ignored.
        """
        if not os.path.isdir(self.path):
            return []
        return [
            Artifact.load(self, os.path.join(self.path, name))
            for name in sorted(os.listdir(self.path))
            if not name.startswith(".")
            and os.path.isfile(
                os.path.join(self.path, name, artifact_module.FILENAME)
            )
        ]

    def get(self: Experiment, ident: str) -> Artifact | None:
        """Returns the stored artifact "ident", or None if there is none."""
        validate_ident(ident, kind="artifact identifier")
        path = os.path.join(self.path, ident)
        if not os.path.isfile(os.path.join(path, artifact_module.FILENAME)):
            return None
        return Artifact.load(self, path)

    def _reference_props(self: Experiment, ident: str) -> dict[str, Any] | None:
        """Returns the properties new artifacts are checked against.

        The most recently added artifact (by its own timestamp; directory
        times change whenever an artifact is saved or accepted) defines the
        keys, so a schema that evolved over time (e.g. after migrating an old
        store) is compared against its latest form. Its None values say
        nothing about a type, so they are filled in from the next newest
        artifacts. Returns None if there is no other artifact.
        """
        arts = [art for art in self.artifacts if art.ident != ident]
        arts.sort(key=lambda art: art.timestamp, reverse=True)
        reference = None
        for art in arts:
            props = art.props
            reference = props if reference is None else _fill(reference, props)
            if not _has_none(reference):
                break
        return reference

    def exists(self: Experiment, ident: str, allow_stale: bool = True) -> bool:
        """Returns True if artifact "ident" is stored, so it can be skipped.

        With "allow_stale=False", an artifact whose code has changed since it
        was produced (or whose staleness is unknown) does not count, so the
        caller re-collects it; pair this with "add_artifact(replace=True)".
        """
        art = self.get(ident)
        if art is None:
            return False
        return allow_stale or art.is_stale is False

    @property
    def stale(self: Experiment) -> list[Artifact]:
        """Returns the artifacts that are stale or of unknown staleness."""
        return [art for art in self.artifacts if art.is_stale is not False]

    def add_artifact(
        self: Experiment, artifact: Artifact, replace: bool = False
    ) -> None:
        """Stores an artifact, copying its files into a new directory.

        The artifact is written to a hidden temporary directory and renamed
        into place, so concurrent processes never observe a half-written
        artifact and never store the same identifier twice (the loser gets
        FileExistsError). With "replace=True", an existing artifact with the
        same identifier is deleted first. Raises ValueError if the artifact's
        properties do not have the keys and value types of the most recently
        added artifact.
        """
        if artifact.experiment is None:
            artifact.experiment = self
        elif artifact.experiment.ident != self.ident:
            raise ValueError(
                f"Artifact '{artifact.ident}' belongs to experiment "
                f"'{artifact.experiment.ident}', not '{self.ident}'"
            )
        artifact.code = self.code  # Raises early if there is no repository.
        self.save()

        existing = self.get(artifact.ident)
        if existing is not None and not replace:
            raise FileExistsError(
                f"Artifact '{artifact.ident}' already exists in experiment "
                f"'{self.ident}' (pass replace=True to overwrite it)"
            )
        reference = self._reference_props(artifact.ident)
        if reference is not None:
            _check_schema(artifact.props, reference)

        final = os.path.join(self.path, artifact.ident)
        tmp = tempfile.mkdtemp(prefix=f".{artifact.ident}.", dir=self.path)
        # Writing repoints the properties' paths into "tmp"; a failed add
        # gives the caller back the originals, so it can retry.
        props = artifact.props
        try:
            artifact._write(tmp)
            if existing is not None:
                existing.delete()
            try:
                os.rename(tmp, final)
            except OSError as e:
                raise FileExistsError(
                    f"Artifact '{artifact.ident}' was added to experiment "
                    f"'{self.ident}' by another process"
                ) from e
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            artifact.props = props
            raise

        # The copied files now live in "final", not "tmp".
        artifact.path = final
        artifact.props = artifact_module.deserialize(
            artifact_module.serialize(artifact.props, tmp), final
        )

    def accept(self: Experiment) -> None:
        """Declares every artifact valid for the code as it is right now."""
        for art in self.artifacts:
            art.accept()

    def delete(self: Experiment) -> None:
        """Removes the experiment and all of its artifacts from the store."""
        if os.path.exists(self.path):
            shutil.rmtree(self.path)

    def __eq__(self: Experiment, other: object) -> bool:
        """Two experiments are equal if they have the same identifier."""
        return isinstance(other, Experiment) and self.ident == other.ident

    def __hash__(self: Experiment) -> int:
        """Hashes consistently with "__eq__"."""
        return hash(self.ident)

    def __repr__(self: Experiment) -> str:
        """Returns a short description naming the experiment."""
        return f"Experiment(ident={self.ident!r}, path={self.path!r})"
