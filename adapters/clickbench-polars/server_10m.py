#!/usr/bin/env python3
"""FastAPI wrapper around polars so it conforms to the ClickBench
install/start/check/stop/load/query interface.

Routes:
    GET  /health     -> 200 OK once the server is up
    POST /load       -> builds a LazyFrame over hits.parquet (no
                        collect()) and returns {"elapsed": <seconds>}
    POST /query      -> body: a Python expression. eval()s it against the
                        loaded LazyFrame (`hits`, `pl`, and `date` in scope)
                        and returns {"elapsed": <seconds>}.
    GET  /data-size  -> on-disk parquet size (the LazyFrame is not
                        materialized so estimated_size doesn't apply)

The /query endpoint takes a Python expression directly rather than an
SQL string mapped to a hardcoded lambda. The workload lives in
queries.sql, one Python expression per line (the filename matches the
cross-system convention; the contents are not SQL).
"""

import os
import timeit
from datetime import date
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import uvicorn
from fastapi import FastAPI, HTTPException, Request

# Streaming engine will be the default soon.
pl.Config.set_engine_affinity("streaming")

app = FastAPI()
hits: pl.LazyFrame | None = None
import os as _os
parquet_path: str = _os.environ.get("POLARS_PARQUET","hits.parquet")
capture_dir: str | None = _os.environ.get("CLICKBENCH_CAPTURE_DIR")


def _as_arrow(value) -> pa.Table:
    if isinstance(value, pl.LazyFrame):
        value = value.collect()
    if isinstance(value, pl.DataFrame):
        return value.to_arrow()
    if isinstance(value, pl.Series):
        return value.to_frame().to_arrow()
    if isinstance(value, tuple):
        return pa.table({f"c_{i}": [item] for i, item in enumerate(value)})
    return pa.table({"c_0": [value]})


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/load")
def load():
    global hits
    start = timeit.default_timer()
    # Lazy: just builds the plan. Data is read on each query collect().
    hits = pl.scan_parquet(parquet_path).with_columns(
        (pl.col("EventTime") * int(1e6)).cast(pl.Datetime(time_unit="us")),
        pl.col("EventDate").cast(pl.Date),
    )
    elapsed = round(timeit.default_timer() - start, 3)
    return {"elapsed": elapsed}


@app.post("/query")
async def query(request: Request):
    if hits is None:
        raise HTTPException(status_code=409, detail="DataFrame not loaded; POST /load first")
    code = (await request.body()).decode("utf-8").strip()
    if not code:
        raise HTTPException(status_code=400, detail="empty query")
    try:
        compiled = compile(code, "<query>", "eval")
    except SyntaxError as e:
        raise HTTPException(status_code=400, detail=f"syntax error: {e}")
    start = timeit.default_timer()
    value = eval(compiled, {"hits": hits, "pl": pl, "date": date})
    elapsed = round(timeit.default_timer() - start, 6)
    # Render the eval result so the playground UI shows something
    # instead of just a timing line. polars DataFrames / Series /
    # LazyFrames have a useful __str__; everything else (scalar,
    # tuple, dict, ...) falls through repr.
    if isinstance(value, (pl.DataFrame, pl.Series, pl.LazyFrame)):
        result = str(value)
    else:
        result = repr(value)
    return {"elapsed": elapsed, "result": result}


@app.post("/capture/{idx}")
async def capture(idx: int, request: Request):
    """Execute once and persist the full result, including scalar results."""
    if hits is None:
        raise HTTPException(status_code=409, detail="DataFrame not loaded; POST /load first")
    if capture_dir is None:
        raise HTTPException(status_code=409, detail="CLICKBENCH_CAPTURE_DIR is not configured")
    code = (await request.body()).decode("utf-8").strip()
    try:
        value = eval(compile(code, "<query>", "eval"), {"hits": hits, "pl": pl, "date": date})
        table = _as_arrow(value)
        output = Path(capture_dir) / f"q{idx:02d}.parquet"
        output.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, output)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"{type(error).__name__}: {error}")
    return {"rows": table.num_rows, "columns": table.num_columns}


@app.get("/data-size")
def data_size():
    # LazyFrame doesn't materialize; report on-disk parquet size.
    try:
        return {"bytes": os.path.getsize(parquet_path)}
    except OSError:
        return {"bytes": 0}


if __name__ == "__main__":
    port = int(os.environ.get("BENCH_POLARS_PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
