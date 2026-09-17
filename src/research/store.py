"""Locates the research store and validates identifiers."""

import os
import re

"""
Identifiers name directories, so they are restricted to characters that are
safe in every file explorer and shell, and may not be hidden (start with ".").
"""
IDENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._=+@-]*")

"""
Longest identifier allowed, comfortably under the 255-byte file name limit.
"""
MAX_IDENT_LENGTH = 200


def get_basedir() -> str:
    """Returns the absolute path of the research store, creating it if needed.

    The store is the "RESEARCH_PATH" environment variable. It is read on
    every call, so it may change within a session.
    """
    basedir = os.environ.get("RESEARCH_PATH")
    if not basedir:
        raise ValueError(
            "The RESEARCH_PATH environment variable must point to the "
            "research store"
        )

    basedir = os.path.abspath(basedir)
    os.makedirs(basedir, exist_ok=True)
    return basedir


def validate_ident(ident: str, kind: str = "identifier") -> None:
    """Raises ValueError unless "ident" is a legal directory name."""
    if not isinstance(ident, str) or not IDENT_PATTERN.fullmatch(ident):
        raise ValueError(
            f"The {kind} {ident!r} is illegal: it must be non-empty, contain "
            "only letters, digits, and any of '._=+@-', and not start with "
            "'.'"
        )
    if len(ident) > MAX_IDENT_LENGTH:
        raise ValueError(
            f"The {kind} {ident!r} is longer than {MAX_IDENT_LENGTH} characters"
        )
