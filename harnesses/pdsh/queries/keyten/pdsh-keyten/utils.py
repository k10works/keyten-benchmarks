from datetime import date as _pydate
from typing import Any, Callable

import keyten as kt
import polars as pl

from queries.common_utils import get_table_path, run_query_generic
from settings import Settings

settings = Settings()

_EPOCH = _pydate(1970, 1, 1)


def date(y: int, m: int, d: int) -> kt.Expr:
    """A Date literal (typed, comparable with date columns)."""
    return kt.lit((_pydate(y, m, d) - _EPOCH).days).cast("date")


def _scan(table_name: str) -> kt.LazyFrame:
    path = get_table_path(table_name)
    if settings.run.io_type == "skip":
        # The in-memory variant: load lands in the engine's own resident
        # representation (the blocked native store -- dictionaries, zone
        # maps, sketches), exactly as the other engines pre-load into
        # theirs. Conversion is cached beside the tables and untimed.
        import pathlib

        native = pathlib.Path(str(path)).with_suffix(".k10dir")
        if not native.exists():
            kt.scan_parquet(str(path)).collect().write_native(str(native))
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


def run_query(query_number: int, query: Callable[[], Any]) -> None:
    run_query_generic(
        query,
        query_number,
        "keyten",
        library_version=kt.__version__,
        query_checker=check_result,
    )


def check_result(result: Any, query_number: int) -> None:
    """Tolerant comparison against the stored answers: dates arrive as
    epoch-day ints and unrounded floats stand in for round(2) columns."""
    from queries.common_utils import _get_query_answer_pl

    expected = _get_query_answer_pl(query_number)
    got = pl.DataFrame(result.to_dict())
    assert got.height == expected.height, f"rows {got.height} != {expected.height}"
    assert got.columns == expected.columns, f"cols {got.columns} != {expected.columns}"
    for name in expected.columns:
        e = expected.get_column(name)
        g = got.get_column(name)
        if e.dtype == pl.Date:
            g = g.cast(pl.Date)
        if e.dtype.is_float() or isinstance(e.dtype, pl.Decimal):
            ef = e.cast(pl.Float64)
            gf = g.cast(pl.Float64)
            diff = (ef - gf).abs()
            tol = ef.abs() * 1e-6 + 0.011
            bad = (diff > tol).sum()
            assert bad == 0, f"{name}: {bad} values beyond tolerance"
        else:
            assert g.cast(e.dtype).equals(e), f"{name} differs"
