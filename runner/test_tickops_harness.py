import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

import pyarrow as pa
import pyarrow.parquet as pq


HARNESS_PATH = Path(__file__).parents[1] / "harnesses" / "tickops" / "harness.py"
SPEC = importlib.util.spec_from_file_location("tickops_harness", HARNESS_PATH)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class TickOpsComparatorTests(TestCase):
    @staticmethod
    def _summary_for_query_one() -> dict[str, dict]:
        return {
            "1": {"rows": 1, "columns": ["sym", "minute", "value"]},
            **{str(idx): {"gap": "fixture"} for idx in range(2, 9)},
        }

    def test_wrong_result_fails_before_timing_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for engine, value in (("keyten", 1.0), ("duckdb", 1.0), ("polars", 9.0)):
                pq.write_table(
                    pa.table({"sym": ["A"], "minute": [1], "value": [value]}),
                    root / f"{engine}.q1.parquet",
                )
                (root / f"{engine}.checksum.json").write_text(
                    json.dumps(self._summary_for_query_one()),
                    encoding="utf-8",
                )

            timing_artifact = root / "polars.csv"
            if HARNESS.check(str(root), ["keyten", "duckdb", "polars"]):
                timing_artifact.write_text("timed\n", encoding="utf-8")

            self.assertFalse(timing_artifact.exists())
            report = json.loads((root / "correctness_report.json").read_text())
            self.assertEqual(report["1"]["status"], "MISMATCH")
            self.assertEqual(set(report["1"]["sha256"]), {"keyten", "duckdb", "polars"})

    def test_missing_output_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for engine in ("keyten", "duckdb", "polars"):
                summary = self._summary_for_query_one()
                summary["1"]["columns"] = ["sym", "minute"]
                (root / f"{engine}.checksum.json").write_text(
                    json.dumps(summary),
                    encoding="utf-8",
                )
            self.assertFalse(HARNESS.check(str(root), ["keyten", "duckdb", "polars"]))

    def test_query_missing_from_every_adapter_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for engine in ("keyten", "duckdb", "polars"):
                (root / f"{engine}.checksum.json").write_text(
                    json.dumps({}), encoding="utf-8"
                )

            self.assertFalse(HARNESS.check(str(root), ["keyten", "duckdb", "polars"]))


if __name__ == "__main__":
    main()
