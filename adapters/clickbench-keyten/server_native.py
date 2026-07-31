#!/usr/bin/env python3
"""FastAPI wrapper around keyten so it conforms to the ClickBench
install/start/check/stop/load/query interface. Mirrors polars/server.py.

/load builds a LazyFrame over hits.parquet (scan only, no collect). Unlike
the polars server, EventTime stays raw epoch seconds and EventDate stays raw
days-since-epoch: the queries file uses arithmetic helpers (minute(),
date()) over those integers, which yields identical values and orderings
without any datetime casts at load.

/query eval()s a Python expression against `hits`, `kt`, and the helpers.
The workload lives in queries.sql, one expression per line (the filename
matches the cross-system convention; the contents are not SQL).
"""

import os
import timeit
from datetime import date as _pydate

import keyten as kt
import uvicorn
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
hits = None
parquet_path = "hits.parquet"
import os as _os
native_path = _os.environ.get("KEYTEN_NATIVE", "/skull/bench/hits10m_native.k10dir")

EPOCH = _pydate(1970, 1, 1)


def date(y: int, m: int, d: int):
    """Date literal as days-since-epoch (EventDate's raw domain)."""
    return kt.lit((_pydate(y, m, d) - EPOCH).days)


def minute(col_name: str):
    """Minute-of-hour of a raw epoch-seconds column, as an integer."""
    t = kt.col(col_name)
    return ((t % kt.lit(3600) - t % kt.lit(60)) / kt.lit(60)).cast("int")


def minute_trunc(col_name: str):
    """Epoch seconds truncated to the minute."""
    t = kt.col(col_name)
    return t - t % kt.lit(60)


def unsupported(what: str):
    raise NotImplementedError(what)


EVAL_CTX = {
    "kt": kt,
    "date": date,
    "minute": minute,
    "minute_trunc": minute_trunc,
    "unsupported": unsupported,
}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/load")
def load():
    global hits
    start = timeit.default_timer()
    hits = kt.scan_native(native_path)
    elapsed = round(timeit.default_timer() - start, 3)
    return {"elapsed": elapsed}


@app.post("/query")
async def query(request: Request):
    if hits is None:
        raise HTTPException(status_code=409, detail="DataFrame not loaded; POST /load first")
    code = (await request.body()).decode("utf-8").strip()
    if not code:
        raise HTTPException(status_code=400, detail="empty query")
    ctx = dict(EVAL_CTX)
    ctx["hits"] = hits
    try:
        start = timeit.default_timer()
        result = eval(code, ctx)
        elapsed = round(timeit.default_timer() - start, 6)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"unsupported: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return {"elapsed": elapsed, "result": str(result)}


@app.get("/data-size")
def data_size():
    return {"bytes": os.path.getsize(parquet_path)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
