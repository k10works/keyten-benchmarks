#!/usr/bin/env python3
"""Create and verify a fresh benchmark harness snapshot.

Generated data and virtual environments live outside the snapshot.  Rebuilding
the code directory on every sitting prevents a stale .work copy from executing.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
from pathlib import Path


def _ignored(path: Path) -> bool:
    return any(part in {"__pycache__", ".venv"} for part in path.parts) or path.suffix == ".pyc"


def tree_digest(root: Path) -> str:
    """Hash relative names and contents, excluding generated Python state."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not _ignored(p.relative_to(root))):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def materialize(source: Path, target: Path, allowed_parent: Path) -> str:
    """Replace target with an exact source copy and return its verified digest."""
    source = source.resolve()
    target = target.resolve()
    allowed_parent = allowed_parent.resolve()
    if target.parent != allowed_parent:
        raise ValueError(f"refusing to replace {target}: parent is not {allowed_parent}")
    if source == target or source in target.parents:
        raise ValueError("source and target must be separate trees")

    expected = tree_digest(source)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv"),
    )
    actual = tree_digest(target)
    if actual != expected:
        raise RuntimeError(f"snapshot digest mismatch: source={expected} target={actual}")
    return actual


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--allowed-parent", required=True, type=Path)
    args = parser.parse_args()
    print(materialize(args.source, args.target, args.allowed_parent))


if __name__ == "__main__":
    main()
