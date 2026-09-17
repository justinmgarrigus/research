"""Tests locating the store and validating identifiers."""

from pathlib import Path

import pytest

from research.store import get_basedir, validate_ident


def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RESEARCH_PATH in the environment locates (and creates) the store."""
    path = tmp_path / "store"
    monkeypatch.setenv("RESEARCH_PATH", str(path))
    assert not path.exists()
    assert get_basedir() == str(path)
    assert path.is_dir()


def test_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without RESEARCH_PATH (or with it empty), a clear error is raised."""
    monkeypatch.delenv("RESEARCH_PATH", raising=False)
    with pytest.raises(ValueError, match="RESEARCH_PATH"):
        get_basedir()
    monkeypatch.setenv("RESEARCH_PATH", "")
    with pytest.raises(ValueError, match="RESEARCH_PATH"):
        get_basedir()


def test_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing RESEARCH_PATH within a session takes effect immediately."""
    monkeypatch.setenv("RESEARCH_PATH", str(tmp_path / "a"))
    assert get_basedir() == str(tmp_path / "a")
    monkeypatch.setenv("RESEARCH_PATH", str(tmp_path / "b"))
    assert get_basedir() == str(tmp_path / "b")


@pytest.mark.parametrize(
    "ident",
    [
        "gen-seq",
        "model=qwen2.5-7B_ntokens=2048_mem=None",
        "v1.2.3",
        "a",
        "A@b+c",
    ],
)
def test_valid_ident(ident: str) -> None:
    """Identifiers used in practice are accepted."""
    validate_ident(ident)


@pytest.mark.parametrize(
    "ident",
    [
        "",
        ".hidden",
        "..",
        "has space",
        "a/b",
        "a\\b",
        "hello!",
        "a,b",
        "x" * 201,
    ],
)
def test_invalid_ident(ident: str) -> None:
    """Identifiers that are unsafe as directory names are rejected."""
    with pytest.raises(ValueError):
        validate_ident(ident)
