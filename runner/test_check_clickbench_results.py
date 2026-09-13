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
            # The engine under test must disagree with both references.
            self._fixture(root, (9.0, 1.0, 1.0))
            report = compare_results(root, ["keyten", "duckdb", "polars"], 1)
            self.assertEqual(report["status"], "fail")
            self.assertIn("value mismatch", report["queries"][0]["errors"][0])

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
