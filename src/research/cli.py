"""Command-line front-end: "research ls|rm|accept|migrate|path"."""

import argparse
import os
import sys
from collections.abc import Sequence

from research.artifact import Artifact
from research.experiment import Experiment
from research.migrate import migrate
from research.store import get_basedir


def get_parser() -> argparse.ArgumentParser:
    """Builds the argument parser."""
    parser = argparse.ArgumentParser(
        prog="research",
        description=(
            "Manages the research store at RESEARCH_PATH. Targets are "
            "'<experiment>' or '<experiment>/<artifact>'. Staleness is judged "
            "against the git repository containing the working directory."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ls = sub.add_parser("ls", help="List experiments, or one's artifacts")
    ls.add_argument("target", nargs="?", default=None)

    rm = sub.add_parser("rm", help="Delete an experiment or artifact")
    rm.add_argument("target")
    rm.add_argument(
        "-f", "--force", action="store_true", help="Do not ask to confirm"
    )

    accept = sub.add_parser(
        "accept",
        help="Mark an experiment's (or one artifact's) results as valid for "
        "the current code, so they are no longer reported stale",
    )
    accept.add_argument("target")

    mig = sub.add_parser(
        "migrate", help="Copy a store in the original layout into a new one"
    )
    mig.add_argument("old", help="Store in the original (util.research) layout")
    mig.add_argument("new", help="Directory to write the converted store to")

    sub.add_parser("path", help="Print the location of the store")
    return parser


def split_target(target: str) -> tuple[str, str | None]:
    """Splits "<experiment>[/<artifact>]" into its parts."""
    experiment, _, artifact = target.partition("/")
    return experiment, (artifact or None)


def state(art: Artifact) -> str:
    """Describes an artifact's staleness in one word."""
    stale = art.is_stale
    if stale is None:
        return "unknown"
    return "stale" if stale else "current"


def table(rows: list[list[str]]) -> str:
    """Formats rows as aligned columns."""
    if len(rows) == 0:
        return ""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in rows
    )


def cmd_ls(target: str | None, root: str) -> str:
    """Lists experiments, or the artifacts of one experiment."""
    if target is None:
        rows = [["EXPERIMENT", "ARTIFACTS", "STALE", "NAME"]]
        for exp in Experiment.list(root=root):
            arts = exp.artifacts
            stale = sum(1 for a in arts if a.is_stale is not False)
            rows.append([exp.ident, str(len(arts)), str(stale), exp.name])
        return table(rows)

    ident, art_ident = split_target(target)
    exp = Experiment.load(ident, root=root)
    arts = exp.artifacts
    if art_ident is not None:
        arts = [a for a in arts if a.ident == art_ident]
        if len(arts) == 0:
            raise FileNotFoundError(f"No artifact '{target}'")
    rows = [["ARTIFACT", "TIMESTAMP", "COMMIT", "STATE"]]
    for art in arts:
        commit = "" if art.code is None else art.code.short_commit
        if art.code is not None and art.code.dirty:
            commit += "*"
        rows.append([art.ident, art.timestamp, commit, state(art)])
    return table(rows)


def cmd_rm(target: str, root: str, force: bool) -> str:
    """Deletes an experiment or a single artifact."""
    ident, art_ident = split_target(target)
    exp = Experiment.load(ident, root=root)
    if art_ident is None:
        what = f"experiment '{ident}' ({len(exp.artifacts)} artifacts)"
        victim = exp
    else:
        victim = exp.get(art_ident)
        if victim is None:
            raise FileNotFoundError(f"No artifact '{target}'")
        what = f"artifact '{target}'"
    if not force:
        answer = input(f"Delete {what}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            return "Aborted"
    victim.delete()
    return f"Deleted {what}"


def cmd_accept(target: str, root: str) -> str:
    """Marks artifacts as valid for the current code."""
    ident, art_ident = split_target(target)
    exp = Experiment.load(ident, root=root)
    if art_ident is None:
        arts = exp.artifacts
    else:
        art = exp.get(art_ident)
        if art is None:
            raise FileNotFoundError(f"No artifact '{target}'")
        arts = [art]
    for art in arts:
        art.accept()
    return f"Accepted {len(arts)} artifact(s) at {exp.code.short_commit}"


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the command line and returns the exit status."""
    args = get_parser().parse_args(argv)
    root = os.getcwd()
    try:
        if args.command == "ls":
            print(cmd_ls(args.target, root))
        elif args.command == "rm":
            print(cmd_rm(args.target, root, args.force))
        elif args.command == "accept":
            print(cmd_accept(args.target, root))
        elif args.command == "migrate":
            migrate(args.old, args.new, log=print)
        elif args.command == "path":
            print(get_basedir())
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
