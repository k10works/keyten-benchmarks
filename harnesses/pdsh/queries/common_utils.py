from __future__ import annotations

import json
import os
import re
import statistics
import sys
from importlib.metadata import version
from pathlib import Path
from subprocess import run
from typing import TYPE_CHECKING, Any

from linetimer import CodeTimer

from settings import Settings

if TYPE_CHECKING:
    from collections.abc import Callable

    import pandas as pd
    import polars as pl

settings = Settings()

# The official SF1 answers use decimal arithmetic, while this public harness
# loads price/discount columns as binary floats. Long aggregations can differ
# by about 1e-8 relative (Q17 is ~8.6e-8); keep this reference-only tolerance
# separate from the tighter cross-engine comparison of identical input types.
REFERENCE_REL_TOL = 1e-7


def get_table_path(table_name: str) -> Path | str:
    """Return the path to the given table."""
    if settings.run.io_type == "network":
        return "/".join(
            [
                settings.paths.network_base_url,
                f"scale-factor-{settings.scale_factor}",
                str(settings.num_batches),
                table_name,
                "*.parquet",
            ]
        )
    ext = settings.run.io_type if settings.run.include_io else "parquet"
    if settings.num_batches is None:
        return settings.dataset_base_dir / f"{table_name}.{ext}"
    return (
        settings.dataset_base_dir
        / str(settings.num_batches)
        / table_name
        / "*"
        / f"part.{ext}"
    )


def log_query_timing(
    solution: str, version: str, query_number: int, time: float
) -> None:
    settings.paths.timings.mkdir(parents=True, exist_ok=True)

    with (settings.paths.timings / settings.paths.timings_filename).open("a") as f:
        if f.tell() == 0:
            f.write(
                "solution,version,query_number,duration[s],io_type,scale_factor,"
                "benchmark_run_id,order_position,execution_mode,warmup_iterations\n"
            )

        line = (
            ",".join(
                [
                    solution,
                    version,
                    str(query_number),
                    str(time),
                    settings.run.io_type,
                    str(settings.scale_factor),
                    settings.run.benchmark_run_id,
                    str(settings.run.order_position),
                    settings.run.execution_mode,
                    str(settings.run.warmup_iterations),
                ]
            )
            + "\n"
        )
        f.write(line)


def capture_query_result(result: Any, solution: str, query_number: int) -> None:
    """Persist an untimed canonical interchange result for cross-engine checks."""
    import polars as pl

    if result is None:
        raise ValueError(f"{solution} query {query_number} returned no result to capture")
    if isinstance(result, pl.DataFrame):
        frame = result
    elif hasattr(result, "to_dict"):
        frame = pl.DataFrame(result.to_dict())
    else:
        raise TypeError(
            f"cannot capture {solution} query {query_number} result of type {type(result).__name__}"
        )

    output = settings.run.result_dir / solution
    output.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(output / f"q{query_number}.parquet")


def on_second_call(func: Any) -> Any:
    def helper(*args: Any, **kwargs: Any) -> Any:
        helper.calls += 1  # type: ignore[attr-defined]

        # first call is outside the function
        # this call must set the result
        if helper.calls == 1:  # type: ignore[attr-defined]
            # include IO will compute the result on the 2nd call
            if not settings.run.include_io:
                helper.result = func(*args, **kwargs)  # type: ignore[attr-defined]
            return helper.result  # type: ignore[attr-defined]

        # second call is in the query, now we set the result
        if settings.run.include_io and helper.calls == 2:  # type: ignore[attr-defined]
            helper.result = func(*args, **kwargs)  # type: ignore[attr-defined]

        return helper.result  # type: ignore[attr-defined]

    helper.calls = 0  # type: ignore[attr-defined]
    helper.result = None  # type: ignore[attr-defined]

    return helper


def execute_all(library_name: str) -> None:
    print(settings.model_dump_json())

    query_numbers = _get_query_numbers(library_name)

    total_query_s = 0.0

    with CodeTimer(
        name=f"Overall execution benchmark harness and {library_name} queries", unit="s"
    ):
        for i in query_numbers:
            out = run(
                [sys.executable, "-m", f"queries.{library_name}.q{i}"],
                check=True,
                capture_output=True,
                env=os.environ,
            )
            stdout_str = out.stdout.decode("utf8")
            sys.stdout.write(stdout_str)
            times = [
                float(x.rpartition("took: ")[2])
                for x in stdout_str.split(" s\n")
                if x != ""
            ]
            total_query_s += statistics.median(times)

        print(f"\nAll queries combined took: {total_query_s:.4} s (median)\n")


def _get_query_numbers(library_name: str) -> list[int]:
    """Get the query numbers that are implemented for the given library."""
    query_numbers = []

    path = Path(__file__).parent / library_name
    expr = re.compile(r"q(\d+).py$")

    for file in path.iterdir():
        match = expr.search(str(file))
        if match is not None:
            query_numbers.append(int(match.group(1)))

    return sorted(query_numbers)


def run_query_generic(
    query: Callable[..., Any],
    query_number: int,
    library_name: str,
    library_version: str | None = None,
    query_checker: Callable[..., None] | None = None,
) -> None:
    """Execute a query."""
    # Execute explicit untimed warmups before recording raw samples.
    if settings.run.pre_run:
        for _ in range(settings.run.warmup_iterations):
            query()
    for _ in range(settings.run.iterations):
        with CodeTimer(
            name=f"Run {library_name} query {query_number}", unit="s"
        ) as timer:
            result = query()

        if settings.run.capture_results:
            capture_query_result(result, library_name, query_number)

        if settings.run.log_timings:
            log_query_timing(
                solution=library_name,
                version=library_version or version(library_name),
                query_number=query_number,
                time=timer.took,
            )

        if settings.run.check_results:
            if query_checker is None:
                msg = "cannot check results if no query checking function is provided"
                raise ValueError(msg)
            if settings.scale_factor != 1:
                msg = f"cannot check results when scale factor is not 1, got {settings.scale_factor}"
                raise RuntimeError(msg)
            query_checker(result, query_number)

        if settings.run.show_results:
            print(result)


def check_query_result_pl(result: pl.DataFrame, query_number: int) -> None:
    """Compare a result with the fixed-width vendored SF1 answer set."""
    import polars as pl

    expected = _get_query_answer_pl(query_number)
    tolerance_path = settings.paths.answers / "tolerances.json"
    tolerances = {}
    if tolerance_path.exists():
        tolerances = json.loads(tolerance_path.read_text(encoding="utf-8")).get(
            f"q{query_number}", {}
        )
    assert result.height == expected.height, (
        f"rows {result.height} != {expected.height}"
    )
    assert result.columns == expected.columns, (
        f"cols {result.columns} != {expected.columns}"
    )
    for name in expected.columns:
        want = expected.get_column(name)
        got = result.get_column(name)
        assert got.is_null().equals(want.is_null()), f"{name}: null positions differ"
        if want.dtype == pl.String:
            # dbgen's answer files are fixed-width. Preserve meaningful
            # leading spaces, but ignore indistinguishable right padding.
            assert got.cast(pl.String).str.strip_chars_end().equals(
                want.str.strip_chars_end()
            ), f"{name} differs"
        elif want.dtype.is_float() or want.dtype.is_decimal():
            want_float = want.cast(pl.Float64)
            got_float = got.cast(pl.Float64)
            diff = (want_float - got_float).abs()
            tolerance = want_float.abs() * REFERENCE_REL_TOL + float(
                tolerances.get(name, 1e-8)
            )
            bad = ((diff > tolerance) & want_float.is_not_null()).sum()
            assert bad == 0, f"{name}: {bad} values beyond tolerance"
        else:
            assert got.cast(want.dtype).equals(want), f"{name} differs"


def check_query_result_pd(result: pd.DataFrame, query_number: int) -> None:
    """Assert that the pandas result of the query is correct."""
    from pandas.testing import assert_frame_equal

    expected = _get_query_answer_pd(query_number)
    assert_frame_equal(result.reset_index(drop=True), expected, check_dtype=False)


def _get_query_answer_pl(query: int) -> pl.DataFrame:
    """Read the true answer to the query from disk as a Polars DataFrame."""
    from polars import read_parquet

    path = settings.paths.answers / f"q{query}.parquet"
    return read_parquet(path)


def _get_query_answer_pd(query: int) -> pd.DataFrame:
    """Read the true answer to the query from disk as a pandas DataFrame."""
    from pandas import read_parquet

    path = settings.paths.answers / f"q{query}.parquet"
    return read_parquet(path, dtype_backend="pyarrow")
