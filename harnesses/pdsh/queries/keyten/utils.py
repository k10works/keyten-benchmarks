import hashlib
import pathlib
from functools import lru_cache

from datetime import date as _pydate
from typing import Any, Callable

import keyten as kt
import polars as pl

from queries.common_utils import get_table_path, run_query_generic
from settings import Settings

settings = Settings()
if settings.run.workers:
    kt.set_workers(settings.run.workers)

_EPOCH = _pydate(1970, 1, 1)


def date(y: int, m: int, d: int) -> kt.Expr:
    """A Date literal (typed, comparable with date columns)."""
    return kt.lit((_pydate(y, m, d) - _EPOCH).days).cast("date")


@lru_cache(maxsize=1)
def engine_stamp() -> str:
    """sha256 of the keyten binary answering this process: the only identity that survives version-string collisions."""
    so = pathlib.Path(kt.__file__).parent / "_keyten.abi3.so"
    return hashlib.sha256(so.read_bytes()).hexdigest()


def write_stamp(native: pathlib.Path) -> None:
    (native / ".engine").write_text(engine_stamp())


def store_is_current(native: pathlib.Path) -> bool:
    stamp = native / ".engine"
    return native.exists() and stamp.exists() and stamp.read_text().strip() == engine_stamp()


def _scan(table_name: str) -> kt.LazyFrame:
    path = get_table_path(table_name)
    if settings.run.io_type == "skip":
        native = pathlib.Path(str(path)).with_suffix(".k10dir")
        if not store_is_current(native):
            import shutil
            shutil.rmtree(native, ignore_errors=True)
            kt.scan_parquet(str(path)).collect().write_native(str(native))
            write_stamp(native)
        return kt.scan_native(str(native))
    return kt.scan_parquet(str(path))


def get_line_item_ds() -> kt.LazyFrame:
    return _scan("lineitem")


def get_orders_ds() -> kt.LazyFrame:
    return _scan("orders")


def get_customer_ds() -> kt.LazyFrame:
    return _scan("customer")


def get_region_ds() -> kt.LazyFrame:
    return _scan("region")


def get_nation_ds() -> kt.LazyFrame:
    return _scan("nation")


def get_supplier_ds() -> kt.LazyFrame:
    return _scan("supplier")


def get_part_ds() -> kt.LazyFrame:
    return _scan("part")


def get_part_supp_ds() -> kt.LazyFrame:
    return _scan("partsupp")


def starts_with(expr: kt.Expr, prefix: str) -> kt.Expr:
    """LIKE 'prefix%' via anchored extract (no dedicated kernel yet)."""
    import re

    return expr.str_extract(f"^{re.escape(prefix)}").fill_null("") == kt.lit(prefix)


def matches(expr: kt.Expr, pattern: str) -> kt.Expr:
    """Regex containment as a boolean (str_contains is substring-only)."""
    return expr.str_extract(pattern).fill_null("\x00") != kt.lit("\x00")


def semi_join(left: kt.LazyFrame, right: kt.LazyFrame, on: list[tuple[str, str]]) -> kt.LazyFrame:
    """SQL EXISTS: the engine's native semi join."""
    return left.semi_join(right, on)


def scalar(lf: kt.LazyFrame, column: str) -> float:
    return lf.collect().column(column).to_list()[0]


def round2(expr: kt.Expr) -> kt.Expr:
    """Match SQL ROUND(value, 2) using Keyten's integer-place round."""
    return (expr * kt.lit(100.0)).round() / kt.lit(100.0)


def run_query(query_number: int, query: Callable[[], Any]) -> None:
    # Hash the process-constant engine once, outside all query timers.
    # The outer runner also verifies the installed binary before/after the suite.
    engine_stamp()
    run_query_generic(
        query,
        query_number,
        "keyten",
        library_version=kt.__version__,
        query_checker=check_result,
    )


def check_result(result: Any, query_number: int) -> None:
    """Compare against stored answers, normalizing Keyten epoch-day dates."""
    from queries.common_utils import check_query_result_pl

    got = pl.DataFrame(result.to_dict())
    check_query_result_pl(got, query_number)
