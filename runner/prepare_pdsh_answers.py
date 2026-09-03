#!/usr/bin/env python3
"""Convert the vendored PDS-H SF1 answer files into harness Parquet files."""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any


_INT = re.compile(r"[+-]?\d+")
_FLOAT = re.compile(
    r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?"
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _convert_column(values: list[str]) -> list[Any]:
    stripped = [value.strip() for value in values]
    if stripped and all(_INT.fullmatch(value) for value in stripped):
        return [int(value) for value in stripped]
    if stripped and all(_FLOAT.fullmatch(value) for value in stripped):
        return [float(value) for value in stripped]
    if stripped and all(_DATE.fullmatch(value) for value in stripped):
        return [date.fromisoformat(value) for value in stripped]
    # The reference renderer left-aligns text and pads it on the right.
    # Leading whitespace can be real TPC-H data (for example Q2 addresses),
    # so only remove the renderer's trailing field padding.
    return [value.rstrip() for value in values]


def _display_tolerance(values: list[str]) -> float | None:
    stripped = [value.strip() for value in values]
    if not stripped or all(_INT.fullmatch(value) for value in stripped):
        return None
    if not all(_FLOAT.fullmatch(value) for value in stripped):
        return None
    # The answer text has already rounded each value for display. Half of the
    # coarsest displayed unit is the strongest valid absolute comparison.
    return max(
        float(Decimal("0.5").scaleb(Decimal(value).as_tuple().exponent))
        for value in stripped
    )


def parse_answer(path: Path) -> dict[str, list[Any]]:
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.reader(source, delimiter="|"))
    if not rows:
        raise ValueError(f"empty PDS-H answer file: {path}")

    names = [name.strip() for name in rows[0]]
    if not names or any(not name for name in names) or len(set(names)) != len(names):
        raise ValueError(f"invalid PDS-H answer header: {path}")

    body = rows[1:]
    for line, row in enumerate(body, start=2):
        if len(row) != len(names):
            raise ValueError(
                f"{path}:{line}: expected {len(names)} columns, got {len(row)}"
            )
    columns = zip(*body, strict=True) if body else [[] for _ in names]
    return {
        name: _convert_column(list(values))
        for name, values in zip(names, columns, strict=True)
    }


def answer_tolerances(path: Path) -> dict[str, float]:
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.reader(source, delimiter="|"))
    if not rows:
        raise ValueError(f"empty PDS-H answer file: {path}")
    names = [name.strip() for name in rows[0]]
    body = rows[1:]
    columns = zip(*body, strict=True) if body else [[] for _ in names]
    tolerances = {
        name: tolerance
        for name, values in zip(names, columns, strict=True)
        if (tolerance := _display_tolerance(list(values))) is not None
    }
    return tolerances


def prepare_answers(source_dir: Path, output_dir: Path) -> None:
    import polars as pl

    expected = {f"q{query}.out" for query in range(1, 23)}
    present = {path.name for path in source_dir.glob("q*.out")}
    if present != expected:
        missing = sorted(expected - present)
        extra = sorted(present - expected)
        raise ValueError(f"invalid answer set: missing={missing}, extra={extra}")

    output_dir.mkdir(parents=True, exist_ok=True)
    tolerances: dict[str, dict[str, float]] = {}
    for query in range(1, 23):
        source = source_dir / f"q{query}.out"
        frame = pl.DataFrame(parse_answer(source))
        frame.write_parquet(output_dir / f"q{query}.parquet")
        tolerances[f"q{query}"] = answer_tolerances(source)
    (output_dir / "tolerances.json").write_text(
        json.dumps(tolerances, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    prepare_answers(args.source_dir, args.output_dir)


if __name__ == "__main__":
    main()
