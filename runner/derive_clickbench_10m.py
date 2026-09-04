#!/usr/bin/env python3
"""Derive a reproducible first-N ClickBench Parquet subset with provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


UPSTREAM_URL = "https://datasets.clickhouse.com/hits_compatible/hits.parquet"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def derive(source: Path, output: Path, rows: int, manifest: Path, source_url: str) -> dict:
    if rows <= 0:
        raise ValueError("rows must be positive")
    parquet = pq.ParquetFile(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    written = 0
    writer = pq.ParquetWriter(temporary, parquet.schema_arrow)
    try:
        for batch in parquet.iter_batches(batch_size=min(rows - written, 131_072)):
            remaining = rows - written
            if remaining <= 0:
                break
            if batch.num_rows > remaining:
                batch = batch.slice(0, remaining)
            writer.write_batch(batch)
            written += batch.num_rows
            if written == rows:
                break
    finally:
        writer.close()
    if written != rows:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"source has only {written} rows; requested {rows}")
    temporary.replace(output)
    record = {
        "source": str(source.resolve()),
        "source_url": source_url,
        "source_sha256": sha256(source),
        "selection": "first rows in Parquet physical order",
        "requested_rows": rows,
        "written_rows": written,
        "columns": parquet.schema_arrow.names,
        "schema": str(parquet.schema_arrow),
        "output": str(output.resolve()),
        "output_sha256": sha256(output),
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="public ClickBench hits.parquet")
    parser.add_argument("output", type=Path, help="derived hits10m.parquet")
    parser.add_argument("--rows", type=int, default=10_000_000)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--source-url", default=UPSTREAM_URL)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifest = args.manifest or args.output.with_suffix(".manifest.json")
    existing = [path for path in (args.output, manifest) if path.exists()]
    if existing and not args.force:
        parser.error(f"refusing to overwrite existing files: {existing}; pass --force")
    record = derive(args.source, args.output, args.rows, manifest, args.source_url)
    print(
        f"wrote {record['written_rows']} rows to {args.output}; "
        f"sha256={record['output_sha256']}"
    )


if __name__ == "__main__":
    main()
