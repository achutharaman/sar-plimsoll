"""Locate repo-level data files (rubric/, config/) in both editable installs and the container."""

import os
from pathlib import Path


def project_root() -> Path:
    override = os.environ.get("PLIMSOLL_ROOT")
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        if (parent / "rubric").is_dir() and (parent / "config").is_dir():
            return parent
    return Path.cwd()
