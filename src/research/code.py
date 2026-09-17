"""Records the state of the code that produced an artifact."""

import hashlib
import os
from dataclasses import asdict, dataclass
from typing import Any

from git import InvalidGitRepositoryError, NoSuchPathError, Repo

"""
Sources used when an experiment does not declare any: the whole repository.
"""
DEFAULT_SOURCES = ["."]


def find_root(start: str) -> str:
    """Returns the root of the git repository containing "start"."""
    try:
        repo = Repo(start, search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError) as e:
        raise FileNotFoundError(
            f"'{start}' is not inside a git repository"
        ) from e
    return repo.working_tree_dir


def list_files(root: str, sources: list[str]) -> list[str]:
    """Returns every file under "sources" that git does not ignore.

    Paths are relative to "root" and sorted. Untracked files are included, so
    a new module counts before it is "git add"ed, while ignored files (logs,
    virtual environments) never count. Raises ValueError if a source matches
    nothing, since that is a mistake in the declared sources rather than
    something that can be answered.
    """
    repo = Repo(root)
    files: set[str] = set()
    for source in sources:
        out = repo.git.ls_files(
            "--cached", "--others", "--exclude-standard", "-z", "--", source
        )
        matched = [f for f in out.split("\0") if f]
        if len(matched) == 0:
            raise ValueError(f"Source '{source}' matches no files in '{root}'")
        files.update(matched)
    return sorted(files)


def fingerprint(root: str, sources: list[str]) -> str:
    """Returns a hash of the contents of every file under "sources".

    Two working trees whose sources have identical contents produce the same
    fingerprint, whatever their commit history, so committing never changes
    it while editing a source always does.
    """
    digest = hashlib.sha256()
    for rel in list_files(root, sources):
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue  # Tracked but deleted, or a symbolic link to a directory.
        digest.update(rel.encode() + b"\0")
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class Code:
    """The state of the code an artifact was produced by.

    - project (str): name of the repository's root directory.
    - commit (str): full hash of HEAD, or "" in a repository with no commits.
    - branch (str): name of the checked-out branch, or "detached".
    - message (str): message of the HEAD commit.
    - dirty (bool | None): whether any file under "sources" differs from the
      commit (so the fingerprint is not reproducible from the commit alone).
      None if unknown, e.g. for migrated artifacts.
    - sources (list[str] | None): paths, relative to the project root, whose
      contents the artifact depends on. None if unknown.
    - fingerprint (str | None): hash of the contents of "sources" (see
      "fingerprint()"). None if unknown.
    """

    project: str
    commit: str
    branch: str
    message: str
    dirty: bool | None
    sources: list[str] | None
    fingerprint: str | None

    @staticmethod
    def capture(root: str, sources: list[str] | None = None) -> "Code":
        """Records the current state of the repository at "root"."""
        if sources is None:
            sources = DEFAULT_SOURCES
        repo = Repo(root)
        try:
            commit = repo.head.commit.hexsha
            message = repo.head.commit.message.strip()
        except ValueError:
            commit, message = "", ""  # No commits yet.
        branch = (
            "detached" if repo.head.is_detached else repo.active_branch.name
        )
        status = repo.git.status(
            "--porcelain", "--untracked-files=all", "--", *sources
        )
        return Code(
            project=os.path.basename(repo.working_tree_dir),
            commit=commit,
            branch=branch,
            message=message,
            dirty=len(status.strip()) > 0,
            sources=list(sources),
            fingerprint=fingerprint(root, sources),
        )

    def to_json(self: "Code") -> dict[str, Any]:
        """Returns a JSON-serializable representation."""
        return asdict(self)

    @staticmethod
    def from_json(obj: dict[str, Any]) -> "Code":
        """Restores a Code from "to_json()" output."""
        return Code(**obj)

    @property
    def short_commit(self: "Code") -> str:
        """Returns the first eight characters of the commit hash."""
        return self.commit[:8]
