import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import keyten as kt

logger = logging.getLogger(__name__)

# The exchange-code legend the suite's queries expand codes through
# (mirrors the schema module's EXNAMES; exnames.parquet carries the same
# pairs for the parquet engines).
EXNAMES = {
    "A": "NYSE American", "B": "NASDAQ OMX BX", "C": "NYSE National",
    "D": "FINRA Alternative Display Facility", "I": "International Securities Exchange",
    "J": "Cboe EDGA Exchange", "K": "Cboe EDGX Exchange", "L": "Long-Term Stock Exchange",
    "M": "Chicago Stock Exchange", "N": "New York Stock Exchange", "P": "NYSE Arca",
    "S": "Consolidated Tape System", "T": "NASDAQ Stock Market", "Q": "NASDAQ Stock Exchange",
    "V": "The Investors' Exchange", "W": "Chicago Broad Options Exchange",
    "X": "NASDAQ OMX PSX", "Y": "Cboe BYX Exchange", "Z": "Cboe BZX Exchange",
}

MIN_NS = 60_000_000_000


def _td_ns(td: timedelta) -> int:
    return int(td.total_seconds() * 1_000_000_000)


class QueryExecutorKeytenInMemory:
    """
    Keyten in-memory executor: native tables mmap-loaded, queries are
    keyten lazy expressions evaluated from the query file, exactly the
    protocol the other engines use.
    """

    def __init__(self, param: dict[str, Any], sort_cols: str | list[str], datadate: date) -> None:
        self.params: dict[str, Any] = param
        self.sort_cols = sort_cols
        buckets = list(self.params["timeBuckets"].items())
        bucket_bounds = [_td_ns(b) for _, b in buckets]
        bucket_labels = [name for name, _ in buckets]

        def time_ns(h: int, m: int = 0, s: int = 0, ns: int = 0) -> Any:
            return kt.time_lit(((h * 60 + m) * 60 + s) * 1_000_000_000 + ns)

        def bucket(every_ns: int) -> Any:
            t = kt.col("time").cast("int")
            return (t - t % kt.lit(every_ns)).cast("time")

        def bucket_index() -> Any:
            acc = None
            for b in bucket_bounds:
                term = (kt.col("time") >= kt.time_lit(b)).cast("int")
                acc = term if acc is None else acc + term
            return acc - kt.lit(1)

        def bucket_label(idx_col: str) -> Any:
            pairs = [(i, name) for i, name in enumerate(bucket_labels)]
            return kt.col(idx_col).recode(pairs, default="")

        def liq_w_mid() -> Any:
            return (
                (kt.col("bsize") * kt.col("bid") + kt.col("asize") * kt.col("ask"))
                / (kt.col("asize") + kt.col("bsize"))
            ).nan_to_null()

        def ex_named() -> Any:
            return kt.col("ex").recode(list(EXNAMES.items()), default="")

        self.eval_context: dict[str, Any] = {
            "kt": kt,
            "datadate": datadate,
            "time_ns": time_ns,
            "bucket": bucket,
            "bucket_index": bucket_index,
            "bucket_label": bucket_label,
            "liq_w_mid": liq_w_mid,
            "ex_named": ex_named,
            "exnames": EXNAMES,
            "MIN_NS": MIN_NS,
            **self.params,
        }

    def load_resources(self, db_path: Path, datadate: date, writer, row_start, ios) -> None:
        # Same parquet files the other in-memory engines load.
        logger.info("loading keyten tables from parquet at %s", db_path)
        io_load_start = ios.get_io_stat()
        t_load_start = time.perf_counter_ns()
        trade = kt.DataFrame.read_parquet(str(db_path / "trade"))
        quote = kt.DataFrame.read_parquet(str(db_path / "quote"))
        t_load_elapsed = time.perf_counter_ns() - t_load_start
        io_load_end = ios.get_io_stat()
        size_kb = self._parquet_size_kb(db_path)
        writer.writerow(row_start + [0, "load", "load a partition into memory", "success",
                                     t_load_elapsed, None, None, None,
                                     io_load_end - io_load_start, None, None, size_kb])

        t_tr_start = time.perf_counter_ns()
        trade = trade.lazy().with_columns([kt.col("time").cast("time")]).collect()
        quote = quote.lazy().with_columns([kt.col("time").cast("time")]).collect()
        t_tr_elapsed = time.perf_counter_ns() - t_tr_start
        writer.writerow(row_start + [-1, "load", "transform", "success", t_tr_elapsed,
                                     None, None, None, 0, None, None, size_kb])

        t_sort_start = time.perf_counter_ns()
        if self.sort_cols:
            cols = self.sort_cols if isinstance(self.sort_cols, list) else [self.sort_cols]
            trade = trade.lazy().sort(cols).collect()
            quote = quote.lazy().sort(cols).collect()
        t_sort_elapsed = time.perf_counter_ns() - t_sort_start
        writer.writerow(row_start + [-2, "load", "sort", "success", t_sort_elapsed,
                                     None, None, None, 0, None, None, size_kb])

        logger.info("Shape of trade: %s x %s", trade.shape[0], trade.shape[1])
        logger.info("Shape of quote: %s x %s", quote.shape[0], quote.shape[1])
        self.eval_context["trade"] = trade
        self.eval_context["quote"] = quote

    @staticmethod
    def _parquet_size_kb(db_path: Path) -> int:
        total = 0
        for sub in ("trade", "quote"):
            d = db_path / sub
            if d.is_dir():
                total += sum(f.stat().st_size for f in d.rglob("*.parquet"))
        return total // 1024

    @staticmethod
    def get_table_size(df) -> int:
        # Result sizing: decoded logical width is what the other engines
        # report; approximate from shape with 8 bytes per lane.
        rows, cols = df.shape
        return rows * cols * 8 // 1024

    def get_table_stats(self) -> dict[str, Any]:
        stats = {}
        for name in ("trade", "quote"):
            df = self.eval_context[name]
            stats[name] = {
                "name": name,
                "rowCount": df.shape[0],
                "columnCount": df.shape[1],
                "columns": [{"name": c} for c in df.columns],
            }
        return stats

    def prepare_run(self) -> None:
        pass

    def get_parameters(self, parameter: str) -> str:
        return parameter

    def execute_query(self, idx: int, tags: set, query_str: str, parameter: str, runidx: int):
        return eval(query_str, self.eval_context)

    def write_csv(self, res, out_file: Path) -> None:
        res.write_csv(str(out_file))
