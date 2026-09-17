"""Many processes add artifacts to one experiment at the same time.

Mirrors a real sweep: each worker computes one artifact and stores it on its
own, skipping identifiers that are already stored.
"""

import multiprocessing as mp
import os
import random
import time
from pathlib import Path

from research import Artifact, Experiment

NUM_JOBS = 100
POOL_SIZE = 32


def worker(args: tuple[int, str]) -> str:
    """Produces one artifact; returns "" on success or the error otherwise."""
    job, root = args
    try:
        exp = Experiment(
            "integration-test",
            name="Integration test",
            description="Each worker stores one artifact on its own.",
            root=root,
        )
        ident = f"artifact-{job}"
        if exp.exists(ident):
            return ""
        time.sleep(random.random() * 0.1)
        art = Artifact(exp, ident, {"input": job, "output": job + 1000000})
        exp.add_artifact(art)
        assert art.exists()
        return ""
    except Exception as e:
        return repr(e)


def test_integration(store: Path, project: Path) -> None:
    """A partial sweep, then a full one, yields exactly one artifact per job."""
    skipped = {random.randrange(NUM_JOBS) for _ in range(3)}
    jobs = [j for j in range(NUM_JOBS) if j not in skipped]

    with mp.Pool(processes=POOL_SIZE) as pool:
        results = pool.map(worker, [(j, str(project)) for j in jobs])
    assert all(r == "" for r in results), results

    exp = Experiment.load("integration-test", root=str(project))
    assert exp.name == "Integration test"
    assert len(exp.artifacts) == len(jobs)
    assert [n for n in os.listdir(exp.path) if n.startswith(".")] == []

    # Re-running everything only fills in the gaps.
    with mp.Pool(processes=POOL_SIZE) as pool:
        results = pool.map(worker, [(j, str(project)) for j in range(NUM_JOBS)])
    assert all(r == "" for r in results), results
    arts = Experiment.load("integration-test", root=str(project)).artifacts
    assert len(arts) == NUM_JOBS
    for art in arts:
        assert art.props["output"] == art.props["input"] + 1000000
        assert art.is_stale is False


def test_same_artifact_race(store: Path, project: Path) -> None:
    """Two processes storing the same identifier: exactly one wins."""
    with mp.Pool(processes=8) as pool:
        results = pool.map(racer, [(str(project), i) for i in range(8)])
    assert results.count("") == 1, results
    assert all(r == "" or "FileExistsError" in r for r in results), results
    exp = Experiment.load("race", root=str(project))
    assert len(exp.artifacts) == 1
    assert exp.artifacts[0].props["winner"] in range(8)


def racer(args: tuple[str, int]) -> str:
    """Stores the artifact "same" without checking whether it exists."""
    root, idx = args
    try:
        exp = Experiment("race", root=root)
        exp.add_artifact(Artifact(exp, "same", {"winner": idx}))
        return ""
    except Exception as e:
        return repr(e)
