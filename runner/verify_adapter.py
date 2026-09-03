#!/usr/bin/env python3
"""Verify that a vendored adapter exactly matches its runnable harness copy."""

from __future__ import annotations

import argparse
from pathlib import Path


def mismatches(adapter: Path, harness_package: Path) -> list[str]:
    problems = []
    for source in sorted(path for path in adapter.rglob("*") if path.is_file()):
        relative = source.relative_to(adapter)
        if "__pycache__" in relative.parts or source.suffix == ".pyc":
            continue
        target = harness_package / relative
        if not target.is_file():
            problems.append(f"missing harness copy: {relative}")
        elif source.read_bytes() != target.read_bytes():
            problems.append(f"content differs: {relative}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("adapter", type=Path)
    parser.add_argument("harness_package", type=Path)
    args = parser.parse_args()
    problems = mismatches(args.adapter, args.harness_package)
    if problems:
        for problem in problems:
            print(problem)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
