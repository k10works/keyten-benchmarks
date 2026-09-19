import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

from check_taq_results import compare_results, _compare_files, _compare_tie_rows


class TaqResultComparisonTests(TestCase):
    def _fixture(self, root: Path, value: str = "1.0") -> Path:
        querymeta = root / "querymeta.psv"
        querymeta.write_text("idx|tags\n1|simple\n", encoding="utf-8")
        for engine in ("keyten", "duckdb", "polars"):
            output = root / engine
            output.mkdir()
            (output / "queryoutput_1.csv").write_text(
                f"sym,value\nABC,{value}\n", encoding="utf-8"
            )
            (root / f"{engine}-status.json").write_text(
                json.dumps({"queries": {"1": {"status": "success"}}}),
                encoding="utf-8",
            )
        return querymeta

    def test_relative_float_tolerance_is_part_of_the_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            querymeta = self._fixture(root, "1000000.0")
            (root / "polars" / "queryoutput_1.csv").write_text(
                "sym,value\nABC,1000000.05\n", encoding="utf-8"
            )
            self.assertEqual(
                compare_results(root, querymeta, ["keyten", "duckdb", "polars"])["status"],
                "pass",
            )

    def test_deliberately_wrong_result_fails_before_timing_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            querymeta = self._fixture(root)
            (root / "polars" / "queryoutput_1.csv").write_text(
                "sym,value\nABC,9.0\n", encoding="utf-8"
            )
            report = root / "report.json"
            timing_artifact = root / "timing.psv"
            command = [
                sys.executable,
                str(Path(__file__).with_name("check_taq_results.py")),
                str(root), str(querymeta), "--report", str(report),
            ]
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            if completed.returncode == 0:
                timing_artifact.write_text("timed\n", encoding="utf-8")

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("TAQ Q01 FAIL", completed.stdout)
            self.assertFalse(timing_artifact.exists())

    def test_execution_error_and_missing_output_fail_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            querymeta = self._fixture(root)
            (root / "duckdb-status.json").write_text(
                json.dumps({"queries": {"1": {"status": "error", "error": "boom"}}}),
                encoding="utf-8",
            )
            (root / "polars" / "queryoutput_1.csv").unlink()

            report = compare_results(root, querymeta, ["keyten", "duckdb", "polars"])

            self.assertEqual(report["status"], "fail")
            errors = " ".join(report["queries"][0]["errors"])
            self.assertIn("execution status is error: boom", errors)
            self.assertIn("output is missing", errors)

    def test_q32_output_ties_preserve_full_rows_and_multiplicity(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            querymeta = root / "querymeta.psv"
            querymeta.write_text("idx|tags\n32|window\n")
            header = "sym,time,price,size,movingvwap\n"
            a = "A,08:00,16.26,1,16.17\n"
            b = "A,08:00,16.27,1,16.18\n"
            c = "A,08:01,17,1,16.5\n"
            for engine, rows in [("keyten", a+b+c), ("duckdb", b+a+c)]:
                (root / engine).mkdir()
                (root / engine / "queryoutput_32.csv").write_text(header+rows)
                (root / f"{engine}-status.json").write_text(json.dumps(
                    {"queries": {"32": {"status": "success"}}}))
            self.assertEqual(compare_results(root, querymeta, ["keyten", "duckdb"])["status"], "pass")
            for bad in [
                a+a+c,  # duplicated row cannot stand in for the missing price
                a+c,    # lost row
                a+b+b+c,
                c+a+b,  # only equal output keys can reorder
                b.replace("16.18", "16.17")+a.replace("16.17", "16.18")+c,
                b.replace("16.27", "16.99")+a+c,
            ]:
                with self.subTest(bad=bad):
                    (root / "duckdb/queryoutput_32.csv").write_text(header+bad)
                    self.assertEqual(compare_results(root, querymeta, ["keyten", "duckdb"])["status"], "fail")
            # The ordinary strict comparator still rejects reordered rows.
            (root / "duckdb/queryoutput_32.csv").write_text(header+b+a+c)
            with self.assertRaises(AssertionError):
                _compare_files("keyten", root / "keyten/queryoutput_32.csv",
                               "duckdb", root / "duckdb/queryoutput_32.csv")

    def test_tolerant_tie_matching_finds_a_bijection_not_a_greedy_match(self) -> None:
        _compare_tie_rows([["0"], ["0.00008"]], [["0.00004"], ["0"]])
        with self.assertRaises(AssertionError):
            _compare_tie_rows([["0"], ["0.00008"]], [["0"], ["0"]])

    def test_tie_comparison_rejects_missing_keys_and_malformed_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            left, right = root / "left.csv", root / "right.csv"
            left.write_text("sym,time,value\nA,08:00,1\n")
            for content in ["sym,time,value\nA,08:00\n", "sym,other,value\nA,08:00,1\n"]:
                right.write_text(content)
                with self.assertRaises(AssertionError):
                    _compare_files("left", left, "right", right, ("sym", "time"))


if __name__ == "__main__":
    main()
