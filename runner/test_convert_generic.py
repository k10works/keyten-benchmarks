import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

from convert_generic import clickbench, pdsh, summarize_samples


class SummaryTests(TestCase):
    def test_reports_median_dispersion_and_raw_samples(self) -> None:
        summary = summarize_samples([1.0, 2.0, 100.0, 3.0])
        self.assertEqual(summary["median_ms"], 2.5)
        self.assertEqual(summary["mad_ms"], 1.0)
        self.assertEqual(summary["p25_ms"], 1.75)
        self.assertEqual(summary["p75_ms"], 27.25)
        self.assertEqual(summary["min_ms"], 1.0)
        self.assertEqual(summary["samples_ms"], [1.0, 2.0, 100.0, 3.0])


class ClickBenchConversionTests(TestCase):
    def _convert(self, lines, samples=4):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "samples.txt"
            source.write_text(lines)
            out = root / "result.json"
            metadata = {"methodology": {"timed_samples_per_query": samples,
                        "warmups_per_timed_sample": 2, "expected_query_count": 1}}
            clickbench(source, "keyten", "test", out, {"cores": 8}, None, metadata)
            return json.loads(out.read_text())

    def test_clickbench_median_dispersion_and_raw_rounds(self):
        lines = "".join(f"q00 {value}ms run_id={run} order_position={run % 3 + 1} warmup_iterations=2\n"
                        for run, value in enumerate([1, 2, 100, 3]))
        result = self._convert(lines)
        query = result["queries"][0]
        self.assertEqual(query["ms"], 2.5)
        self.assertEqual(query["stats"]["median_ms"], 2.5)
        self.assertEqual(query["stats"]["mad_ms"], 1)
        self.assertEqual(query["stats"]["p25_ms"], 1.75)
        self.assertEqual(query["stats"]["p75_ms"], 27.25)
        self.assertEqual(query["stats"]["samples_ms"], [1, 2, 100, 3])
        self.assertEqual([sample["run_id"] for sample in query["stats"]["samples"]], ["0", "1", "2", "3"])

    def test_clickbench_rejects_incomplete_or_invalid_protocol_samples(self):
        valid = [f"q00 {i + 1}ms run_id={i} order_position={i + 1} warmup_iterations=2\n" for i in range(3)]
        cases = [valid[:-1], [valid[0], valid[0], valid[2]],
                 [line.replace("warmup_iterations=2", "warmup_iterations=0") for line in valid],
                 [line.replace(f"order_position={i + 1}", "order_position=1") for i, line in enumerate(valid)],
                 ["q00 ERROR\n"], ["q00 nanms\n"]]
        for lines in cases:
            with self.subTest(lines=lines), self.assertRaises(SystemExit):
                self._convert("".join(lines), samples=3)


class PdshConversionTests(TestCase):
    def _write_timings(self, path: Path, rows: list[tuple[int, float]]) -> None:
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "solution",
                    "version",
                    "query_number",
                    "duration[s]",
                    "io_type",
                    "scale_factor",
                    "benchmark_run_id",
                    "order_position",
                    "execution_mode",
                    "warmup_iterations",
                ],
            )
            writer.writeheader()
            for run_id, (query, seconds) in enumerate(rows):
                writer.writerow({
                    "solution": "keyten",
                    "version": "0.1.53",
                    "query_number": query,
                    "duration[s]": seconds,
                    "io_type": "skip",
                    "scale_factor": "10.0",
                    "benchmark_run_id": run_id,
                    "order_position": run_id % 3 + 1,
                    "execution_mode": "resident-native",
                    "warmup_iterations": 2,
                })

    def test_uses_median_and_is_deterministic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            timings = root / "timings.csv"
            first = root / "first.json"
            second = root / "second.json"
            self._write_timings(timings, [(1, 0.1), (1, 0.5), (1, 0.2)])
            metadata = {
                "methodology": {
                    "timed_samples_per_query": 3,
                    "warmups_per_timed_sample": 2,
                    "expected_query_count": 1,
                },
                "engine_modes": {"keyten": "resident-native"},
            }

            pdsh(timings, "keyten", first, {"cores": 4}, "0.1.53", metadata)
            pdsh(timings, "keyten", second, {"cores": 4}, "0.1.53", metadata)

            document = json.loads(first.read_text())
            self.assertEqual(document["queries"][0]["ms"], 200.0)
            self.assertEqual(document["queries"][0]["stats"]["min_ms"], 100.0)
            self.assertEqual(
                document["queries"][0]["stats"]["samples"][1]["order_position"], 2
            )
            self.assertEqual(document["execution_mode"], "resident-native")
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_rejects_missing_samples(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            timings = root / "timings.csv"
            self._write_timings(timings, [(1, 0.1), (1, 0.2)])
            metadata = {
                "methodology": {
                    "timed_samples_per_query": 3,
                    "warmups_per_timed_sample": 2,
                    "expected_query_count": 1,
                },
                "engine_modes": {"keyten": "resident-native"},
            }
            with self.assertRaisesRegex(SystemExit, "expected 3 samples"):
                pdsh(timings, "keyten", root / "out.json", {"cores": 4}, "0.1.53", metadata)


if __name__ == "__main__":
    main()
