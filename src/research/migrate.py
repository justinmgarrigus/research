"""Converts a store from the original "util.research" layout to this one.

The original layout kept a global "index.json", one bucket directory
"exp-<ident>-<commit8>/" per (experiment, commit) pair with its own
"index.json", and absolute file paths inside properties. This layout has one
directory per experiment and per artifact, no indexes, and relative paths.
"""

import json
import os
import re
import shutil
from collections.abc import Callable
from datetime import datetime
from typing import Any

from research.artifact import FILENAME as ARTIFACT_FILENAME
from research.artifact import PATH_KEY
from research.code import Code
from research.experiment import FILENAME as EXPERIMENT_FILENAME

"""
Timestamp format of the original layout.
"""
OLD_TIMESTAMP_FORMAT = "%m/%d/%Y @ %H:%M:%S"

"""
Matches an original bucket directory name.
"""
BUCKET_PATTERN = re.compile(r"exp-(?P<ident>.+)-(?P<commit>[0-9a-fA-F]{8})")

"""
Where an older duplicate of an artifact (same identifier, produced at an
older commit) is placed inside the experiment directory. It is not a direct
sub-directory of the experiment, so it is kept but not loaded.
"""
SUPERSEDED_DIR = "superseded"


def convert_timestamp(value: str) -> str:
    """Converts an original timestamp to ISO 8601, if it parses."""
    try:
        return datetime.strptime(value, OLD_TIMESTAMP_FORMAT).isoformat()
    except (TypeError, ValueError):
        return value


def convert_props(
    props: Any,  # noqa: ANN401
    pattern: re.Pattern,
    target: str,
    log: Callable[[str], None],
) -> Any:  # noqa: ANN401
    """Rewrites absolute file paths in "props" as relative "$path" entries.

    "pattern" matches the original absolute path of a file inside this
    artifact and captures the path relative to the artifact. Strings that
    match but whose file is not in "target" are left alone and reported.
    """
    if isinstance(props, dict):
        return {
            k: convert_props(v, pattern, target, log) for k, v in props.items()
        }
    if isinstance(props, list):
        return [convert_props(v, pattern, target, log) for v in props]
    if isinstance(props, str):
        match = pattern.search(props)
        if match is not None:
            rel = match.group("rel")
            if os.path.exists(os.path.join(target, rel)):
                return {PATH_KEY: rel}
            log(f"  warning: '{props}' names a file that was not found")
    return props


def _bucket_time(artifacts: list[dict[str, Any]], path: str) -> str:
    """Returns when a bucket was last written, for ordering buckets."""
    times = [convert_timestamp(a.get("timestamp", "")) for a in artifacts]
    if len(times) > 0:
        return max(times)
    return datetime.fromtimestamp(os.path.getmtime(path)).isoformat()


def migrate(old: str, new: str, log: Callable[[str], None] = print) -> None:
    """Copies every experiment and artifact in "old" into "new".

    "old" is not modified. Within an experiment, the newest bucket wins when
    several contain the same artifact identifier; older copies are kept
    under "superseded/<ident>@<commit8>/". Artifacts already present in
    "new" are skipped. Migrated artifacts have no source fingerprint (their
    staleness is unknown until "accept"ed); their commit, branch, and
    message are preserved.
    """
    old = os.path.abspath(old)
    new = os.path.abspath(new)
    if old == new:
        raise ValueError("The old and new stores must be different directories")
    if not os.path.isdir(old):
        raise FileNotFoundError(f"'{old}' is not a directory")

    # Experiment metadata and full commit details live only in the index.
    index_path = os.path.join(old, "index.json")
    index: list[dict[str, Any]] = []
    if os.path.exists(index_path):
        with open(index_path) as f:
            index = json.load(f)
    meta: dict[str, dict[str, Any]] = {}
    commits: dict[str, dict[str, str]] = {}
    for entry in index:
        ident = entry["ident"]
        created = convert_timestamp(entry.get("created_timestamp", ""))
        current = meta.setdefault(ident, {"created": created})
        current["created"] = min(current["created"], created)
        current["name"] = entry.get("name", "")
        current["description"] = entry.get("description", "")
        current["project"] = entry.get("project_name", "")
        commits[entry["commit_hash"][:8]] = {
            "commit": entry["commit_hash"],
            "branch": entry.get("branch", ""),
            "message": entry.get("commit_message", ""),
        }

    # Buckets on disk are authoritative, whether or not they were indexed.
    buckets: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(os.listdir(old)):
        match = BUCKET_PATTERN.fullmatch(name)
        path = os.path.join(old, name)
        if match is None or not os.path.isdir(path):
            continue
        artifacts: list[dict[str, Any]] = []
        bucket_index = os.path.join(path, "index.json")
        if os.path.exists(bucket_index):
            with open(bucket_index) as f:
                artifacts = json.load(f)["artifacts"]
        buckets.setdefault(match.group("ident"), []).append(
            {
                "commit8": match.group("commit").lower(),
                "path": path,
                "artifacts": artifacts,
                "time": _bucket_time(artifacts, path),
            }
        )

    for ident, group in buckets.items():
        info = meta.get(ident, {})
        exp_dir = os.path.join(new, ident)
        os.makedirs(exp_dir, exist_ok=True)
        exp_file = os.path.join(exp_dir, EXPERIMENT_FILENAME)
        if not os.path.exists(exp_file):
            with open(exp_file, "w") as f:
                json.dump(
                    {
                        "name": info.get("name", ""),
                        "description": info.get("description", ""),
                        "sources": None,
                        "created": info.get("created")
                        or min(b["time"] for b in group),
                    },
                    f,
                    indent=2,
                )
                f.write("\n")
        log(f"{ident}: {len(group)} bucket(s)")

        seen: set[str] = set()
        for bucket in sorted(group, key=lambda b: b["time"], reverse=True):
            commit8 = bucket["commit8"]
            commit = commits.get(
                commit8, {"commit": commit8, "branch": "", "message": ""}
            )
            log(
                f"  {os.path.basename(bucket['path'])}: "
                f"{len(bucket['artifacts'])} artifact(s)"
            )
            for entry in bucket["artifacts"]:
                art_ident = entry["artifact-ident"]
                if art_ident in seen:
                    target = os.path.join(
                        exp_dir, SUPERSEDED_DIR, f"{art_ident}@{commit8}"
                    )
                else:
                    target = os.path.join(exp_dir, art_ident)
                seen.add(art_ident)
                if os.path.exists(target):
                    log(f"    skip {os.path.relpath(target, new)} (exists)")
                    continue

                source = os.path.join(bucket["path"], art_ident)
                if os.path.isdir(source):
                    shutil.copytree(source, target)
                else:
                    os.makedirs(target)
                pattern = re.compile(
                    rf"/{re.escape(os.path.basename(bucket['path']))}"
                    rf"/{re.escape(art_ident)}/(?P<rel>.+)$"
                )
                code = Code(
                    project=info.get("project", ""),
                    commit=commit["commit"],
                    branch=commit["branch"],
                    message=commit["message"],
                    dirty=None,
                    sources=None,
                    fingerprint=None,
                )
                data = {
                    "timestamp": convert_timestamp(entry.get("timestamp", "")),
                    "code": code.to_json(),
                    "accepted": None,
                    "props": convert_props(
                        entry.get("properties", {}), pattern, target, log
                    ),
                }
                with open(os.path.join(target, ARTIFACT_FILENAME), "w") as f:
                    json.dump(data, f, indent=2)
                    f.write("\n")
