import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

import pyarrow as pa
import pyarrow.parquet as pq

from check_clickbench_results import compare_results


class ClickBenchResultTests(TestCase):
    def _fixture(self, root: Path, values=(1.0, 1.0, 1.0)) -> None:
        for engine, value in zip(("keyten", "duckdb", "polars"), values, strict=True):
            path = root / engine
            path.mkdir()
            pq.write_table(pa.table({"value": [value]}), path / "q00.parquet")
            (root / f"{engine}-status.json").write_text(
                json.dumps({"queries": {"0": {"status": "success"}}}),
                encoding="utf-8",
            )

    def test_full_result_mismatch_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Corrupted results must fail value validation.
            self._fixture(root, (9.0, 1.0, 1.0))
            report = compare_results(root, ["keyten", "duckdb", "polars"], 1)
            self.assertEqual(report["status"], "fail")
            self.assertIn("value mismatch", report["queries"][0]["errors"][0])

    def test_matching_only_one_reference_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root, (1.0, 1.0, 9.0))
            report = compare_results(root, ["keyten", "duckdb", "polars"], 1)
            self.assertEqual(report["status"], "fail")

    def test_tied_query_without_oracle_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root)
            for engine in ("keyten", "duckdb", "polars"):
                (root / engine / "q00.parquet").rename(root / engine / "q17.parquet")
                (root / f"{engine}-status.json").write_text(
                    json.dumps({"queries": {"17": {"status": "success"}}}))
            report = compare_results(root, ["keyten", "duckdb", "polars"], 18)
            self.assertEqual(report["queries"][17]["status"], "fail")
            self.assertIn("full-data SQL oracle required", str(report["queries"][17]["errors"]))

    def test_missing_output_and_execution_error_fail(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._fixture(root)
            (root / "duckdb" / "q00.parquet").unlink()
            (root / "polars-status.json").write_text(
                json.dumps({"queries": {"0": {"status": "error", "error": "boom"}}}),
                encoding="utf-8",
            )
            report = compare_results(root, ["keyten", "duckdb", "polars"], 1)
            self.assertEqual(report["status"], "fail")
            errors = " ".join(report["queries"][0]["errors"])
            self.assertIn("output is missing", errors)
            self.assertIn("execution status is error: boom", errors)


if __name__ == "__main__":
    main()
