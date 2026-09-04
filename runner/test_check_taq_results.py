import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

from check_taq_results import compare_results


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


if __name__ == "__main__":
    main()
