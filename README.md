# Research

Stores, versions, and queries research artifacts on the filesystem. An
*experiment* is a named group of *artifacts* (one per trial) that are queried
together; an artifact may carry files (logs, traces) that are copied next to
its properties.

The store is a plain directory tree, and the tree is the only source of truth.
There are no indexes to keep in sync, so viewing, editing, renaming, and
deleting experiments or artifacts in a file explorer (or with `rm -r`) is fully
supported.

```
$RESEARCH_PATH/
  gen-seq/                                   # one directory per experiment
    experiment.json                          # name, description, sources
    model=qwen3-8B-Q4_ntokens=2048/          # one directory per artifact
      artifact.json                          # properties + provenance
      server.log                             # files referenced by properties
      tpot-0.log
  swebench-solve/
    experiment.json
    model=qwen3-8B-Q4_task=0/
      artifact.json
      ...
```

`artifact.json` is meant to be read and edited by hand:

```json
{
  "timestamp": "2026-09-17T12:46:58",
  "code": {
    "project": "llm-serving",
    "commit": "b031cf19dc0d33149aa73d7579632705f123db83",
    "branch": "vllm-backend",
    "message": "Improved LLM size calculation (#4)",
    "dirty": false,
    "sources": ["experiments/gen/main.py", "src/llm_serving/frontends"],
    "fingerprint": "5f0c…"
  },
  "accepted": null,
  "props": {
    "model": "qwen3-8B-Q4",
    "ntokens": 2048,
    "log": {"$path": "server.log"}
  }
}
```

File paths are stored relative to the artifact (`{"$path": ...}`), so
artifacts and even the whole store can be moved between directories or
machines. A directory only counts as an experiment if it directly contains
`experiment.json`, and as an artifact if it directly contains `artifact.json`;
anything else (notes, figures, an artifact moved into a sub-folder to set it
aside) is ignored.

## Versioning by code, not by commit

Artifacts used to be bucketed by the commit hash they were produced at, on the
theory that any commit might change results. In practice most commits do not
(plotting, docs, unrelated modules), so every commit forced results to be
re-collected, which discouraged committing at all.

Instead, each experiment declares the **sources** its results depend on, and
every artifact records a **fingerprint** of the *contents* of those sources
when it was produced (plus the commit, branch, and whether the sources had
uncommitted changes, for the record). Consequences:

* Committing never changes the fingerprint. Neither does editing a plot
  script, a README, or any file outside the sources.
* Editing a source makes existing artifacts **stale**, immediately, whether or
  not the edit is committed. Staleness is only ever *reported*, never acted on
  automatically: `exists(ident)` is still true for a stale artifact, so a sweep
  keeps skipping it until you decide otherwise.
* When a source edit does not affect results (a comment, a refactor), run
  `research accept <experiment>` and the artifacts are marked valid for the
  code as it is now. Their original provenance is kept untouched.
* When it does affect results, either delete the artifacts (file explorer,
  `research rm`) or run the sweep with `exists(ident, allow_stale=False)` and
  `add_artifact(art, replace=True)` to re-collect only what is stale.

Untracked files under the sources count towards the fingerprint (a new module
matters before it is `git add`ed); ignored files (logs, virtual environments)
never do.

## Usage

```python
import pathlib
from research import Artifact, Experiment

EXPERIMENT = Experiment(
    "gen-seq",
    name="Generate long sequences of tokens",
    description="Measures the time per output token as sequences grow.",
    sources=["experiments/gen/main.py", "src/llm_serving/frontends"],
)

ident = f"model={model}_ntokens={n_tokens}"
if not EXPERIMENT.exists(ident):
    ...  # Run the trial.
    EXPERIMENT.add_artifact(
        Artifact(
            experiment=EXPERIMENT,
            ident=ident,
            props={
                "model": model,
                "ntokens": n_tokens,
                "log": pathlib.Path("server.log"),  # Copied into the artifact.
            },
        )
    )

for art in EXPERIMENT.artifacts:
    print(art.ident, art.props["ntokens"], art.props["log"].read_text())
    print(art.code.short_commit, art.is_stale)
```

Notes:

* Sources are relative to the root of the git repository containing the module
  that creates the `Experiment` (override with `root=`). Undeclared sources
  mean the whole repository.
* A `pathlib.Path` property is copied into the artifact and afterwards points
  at the copy. A `str` that names an existing path is rejected as ambiguous.
* Artifacts in an experiment may have different property keys, so recording a
  new setting (or dropping an old one) needs no migration; readers should use
  `props.get(...)` for keys older artifacts lack. A property both have must
  keep its value type (`None` matches anything), checked against the most
  recently written artifact, which catches a key reused for something else.
* Adding an artifact is atomic: it is written to a hidden temporary directory
  and renamed into place, so concurrent workers never see a partial artifact,
  and two workers storing the same identifier cannot both succeed.
* The code state is captured once per `Experiment` object, on first use, so a
  long-running sweep records the code it started with.
* `Experiment.list()`, `Experiment.load(ident)`, `exp.get(ident)`,
  `exp.stale`, `exp.accept()`, `exp.delete()`, `art.save()`, `art.accept()`,
  `art.delete()` do what their names say.

## Command line

```
research ls                     # experiments, with artifact and stale counts
research ls gen-seq             # artifacts: timestamp, commit (* = dirty), state
research ls gen-seq/model=...   # one artifact
research rm gen-seq/model=...   # delete an artifact (or an experiment)
research accept gen-seq         # mark results valid for the current code
research migrate OLD NEW        # convert a store from the original layout
research path                   # print RESEARCH_PATH
```

Staleness is judged against the git repository containing the current
directory, so run these from inside the project.

## Migrating an existing store

`research migrate OLD NEW` copies a store in the original `util.research`
layout (`index.json` plus `exp-<ident>-<commit>/` buckets) into `NEW`. Each
experiment becomes one directory; when several buckets hold the same artifact
identifier the newest wins and older copies are kept under
`<experiment>/superseded/<ident>@<commit>/`, which the library ignores.
Migrated artifacts keep their commit, branch, and message but have no
fingerprint, so they are listed as `unknown`; `research accept` them once the
experiments declare their sources. `OLD` is never modified.

## Install

Requires `uv`. Install with `uv sync`, then run the tests with `uv run pytest`.
To add this to a project:

```bash
uv add "git+https://github.com/justinmgarrigus/research.git@main"
```

Source `activate.sh` from your `.bashrc` to get the `research` command.

The store location is the `RESEARCH_PATH` environment variable, which must
be set.

## Contributing

Lint and test before committing:

```bash
uv run ruff format
uv run ruff check --fix
uv run pytest
```
