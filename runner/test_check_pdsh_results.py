from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

import polars as pl

from check_pdsh_results import compare_results


class ResultComparisonTests(TestCase):
    def _write(self, root: Path, engine: str, frame: pl.DataFrame) -> None:
        path = root / engine
        path.mkdir()
        frame.write_parquet(path / "q1.parquet")

    def test_normalizes_dates_order_and_small_float_noise(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "duckdb",
                pl.DataFrame({"day": [1, 0], "value": [2.0, 1.0]}).with_columns(
                    pl.col("day").cast(pl.Date)
                ),
            )
            self._write(root, "polars", pl.DataFrame({"day": [0, 1], "value": [1.0, 2.0]}))
            self._write(
                root,
                "keyten",
                pl.DataFrame({"day": [1, 0], "value": [2.0000001, 1.0000001]}),
            )

            report = compare_results(root, ["duckdb", "polars", "keyten"], 1)

            self.assertEqual(report["status"], "pass")
            self.assertEqual(len(report["queries"][0]["sha256"]), 3)

    def test_rejects_semantic_rounding_difference(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "duckdb", pl.DataFrame({"key": [1], "value": [1.23]}))
            self._write(root, "polars", pl.DataFrame({"key": [1], "value": [1.23]}))
            self._write(root, "keyten", pl.DataFrame({"key": [1], "value": [1.234]}))

            report = compare_results(root, ["duckdb", "polars", "keyten"], 1)

            self.assertEqual(report["status"], "fail")
            self.assertIn("float mismatch", report["errors"][0]["error"])


if __name__ == "__main__":
    main()
