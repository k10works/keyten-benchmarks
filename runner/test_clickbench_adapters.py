"""Exercise timing clients with fake engines; never start a benchmark server."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class ClickBenchAdapterProtocolTests(TestCase):
    def test_daemon_clients_discard_warmups_and_keep_one_sample(self):
        for relative in ("adapters/clickbench-keyten/run_board_native.py",
                         "adapters/clickbench-polars/run_board.py"):
            with self.subTest(adapter=relative), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "queries.sql").write_text("first\nsecond\n")
                calls = []
                values = iter([900, 800, .00125, 700, 600, .0025])
                def respond(request, **kwargs):
                    calls.append((request.full_url.rsplit("/", 1)[-1], request.data))
                    return io.BytesIO(json.dumps({"elapsed": 0 if calls[-1][0] == "load" else next(values)}).encode())
                previous = Path.cwd()
                output = io.StringIO()
                try:
                    os.chdir(root)
                    with patch.object(sys, "argv", [relative]), \
                         patch.dict(os.environ, RUN_WARMUP_ITERATIONS="2", RUN_BENCHMARK_RUN_ID="7", RUN_ORDER_POSITION="3"), \
                         patch("urllib.request.urlopen", side_effect=respond), \
                         contextlib.redirect_stdout(output):
                        runpy.run_path(str(ROOT / relative), run_name="__main__")
                finally:
                    os.chdir(previous)
                self.assertEqual(calls, [("load", b"")] + [("query", b"first")] * 3 + [("query", b"second")] * 3)
                self.assertEqual(output.getvalue().splitlines()[:2], [
                    "q00 1.250000ms run_id=7 order_position=3 warmup_iterations=2",
                    "q01 2.500000ms run_id=7 order_position=3 warmup_iterations=2",
                ])

    def test_duckdb_discards_warmups_and_materializes_each_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            queries = Path(tmp) / "queries.sql"
            queries.write_text("first\nsecond\n")
            calls = []
            class Connection:
                def execute(self, sql, *args):
                    self.sql = sql
                    return self
                def to_arrow_table(self):
                    calls.append(self.sql)
                    return object()
            duckdb = types.ModuleType("duckdb")
            duckdb.connect = Connection
            arrow = types.ModuleType("pyarrow")
            parquet = types.ModuleType("pyarrow.parquet")
            arrow.parquet = parquet
            output = io.StringIO()
            with patch.dict(sys.modules, {"duckdb": duckdb, "pyarrow": arrow, "pyarrow.parquet": parquet}), \
                 patch.object(sys, "argv", ["cb_duckdb.py", "unused.parquet", str(queries)]), \
                 patch.dict(os.environ, RUN_WARMUP_ITERATIONS="2", RUN_BENCHMARK_RUN_ID="7", RUN_ORDER_POSITION="3"), \
                 patch("time.perf_counter", side_effect=[0, .00125, 1, 1.0025]), \
                 contextlib.redirect_stdout(output):
                runpy.run_path(str(ROOT / "runner/cb_duckdb.py"), run_name="__main__")
            self.assertEqual(calls, ["first"] * 3 + ["second"] * 3)
            self.assertEqual([line for line in output.getvalue().splitlines() if line.startswith("q")], [
                "q00 1.250000ms run_id=7 order_position=3 warmup_iterations=2",
                "q01 2.500000ms run_id=7 order_position=3 warmup_iterations=2",
            ])
