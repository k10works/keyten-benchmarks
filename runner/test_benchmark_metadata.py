import sys
import json
import hashlib
import struct
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from unittest import TestCase, main
from unittest.mock import patch

import benchmark_metadata
from benchmark_metadata import _engine_artifact, machine_facts
from convert_generic import clickbench


class MachineFactsTests(TestCase):
    def test_machine_probe_has_reproducibility_fields(self) -> None:
        facts = machine_facts()
        for name in ("cpu", "cores", "affinity_cpus", "ram_bytes", "os", "architecture", "kernel"):
            self.assertIn(name, facts)
        self.assertGreaterEqual(facts["cores"], 1)
        self.assertTrue(facts["affinity_cpus"])
        self.assertGreater(facts["ram_bytes"], 0)


class EngineBuildTests(TestCase):
    def test_engine_artifact_carries_build_identity_fields(self) -> None:
        artifact = _engine_artifact("json")  # any importable module works for the shape
        self.assertIn("build", artifact)
        self.assertEqual(set(artifact["build"]), {"git_sha", "build_state"})


class ClickBenchMetadataTests(TestCase):
    def test_clickbench_results_carry_engine_identity_and_native_encodings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / "hits10m.k10dir"
            store.mkdir()
            # One native record and root, using the format read by store_layout.
            record = bytearray(64)
            record[1] = 7
            struct.pack_into("<Q", record, 8, 10)
            column_root = bytearray(88)
            column_root[:8] = b"k10root\0"
            struct.pack_into("<H", column_root, 8, 1)
            struct.pack_into("<QQQQ", column_root, 16, 10, 1, 24, 0)
            struct.pack_into("<QQQ", column_root, 64, 0, 64, 10)
            (store / "hits.col").write_bytes(record + column_root)
            def string(value):
                data = value.encode()
                return struct.pack("<I", len(data)) + data
            (store / "manifest.k10").write_bytes(
                b"k10manT\0" + struct.pack("<HIQ", 1, 1, 10)
                + string("WatchID") + b"\x01" + string("hits.col")
                + struct.pack("<QQ", 64, 88)
            )
            digest = hashlib.sha256(b"test engine binary").hexdigest()
            def engine(name):
                return {
                    "build": {"git_sha": "abc123", "build_state": "clean"},
                    "distributions": {name: {
                        "native_artifacts": {f"{name}/engine.so": digest},
                    }},
                }
            metadata_out = root / "metadata.json"
            argv = [
                "benchmark_metadata.py", "--suite", "clickbench",
                "--repo", str(root), "--harness", str(root), "--adapter", str(root),
                "--machine-out", str(root / "machine.json"),
                "--metadata-out", str(metadata_out),
                "--samples", "3", "--warmups", "2", "--workers", "8",
                "--benchmark-mode", "resident-native", "--native-store", str(store),
            ]
            with patch.object(sys, "argv", argv), \
                 patch.object(benchmark_metadata, "_git_state", return_value={"sha": "harness"}), \
                 patch.object(benchmark_metadata, "tree_digest", return_value="tree"), \
                 patch.object(benchmark_metadata, "_engine_artifact", side_effect=engine), \
                 patch.object(benchmark_metadata.importlib.metadata, "distributions", return_value=[]):
                benchmark_metadata.main()
            metadata = json.loads(metadata_out.read_text())
            transcript = root / "timings.txt"
            transcript.write_text("".join(
                f"q{q:02d} {1.25 if q == 0 else 2.5 if q == 1 else 0}ms run_id={run} order_position={run + 1} warmup_iterations=2\n"
                for run in range(3) for q in range(43)
            ))
            for name in ("keyten", "polars", "duckdb"):
                with self.subTest(engine=name):
                    out = root / f"{name}.json"
                    clickbench(transcript, name, "test", out, {"cores": 8}, None, metadata)
                    result = json.loads(out.read_text())
                    identity = result["benchmark"]
                    self.assertEqual(identity, metadata)
                    self.assertEqual(identity["engines"][name]["build"]["git_sha"], "abc123")
                    self.assertEqual(
                        identity["engines"][name]["distributions"][name]["native_artifacts"],
                        {f"{name}/engine.so": digest},
                    )
                    self.assertEqual(identity["native_stores"][0]["store"], str(store))
                    self.assertEqual(identity["native_stores"][0]["columns"]["WatchID"]["encodings"], {"7": 1})
                    self.assertEqual(identity["methodology"]["statistic"], "median")
                    self.assertEqual(identity["methodology"]["timed_samples_per_query"], 3)
                    self.assertEqual(identity["methodology"]["expected_query_count"], 43)
                    self.assertTrue(identity["methodology"]["raw_samples_retained"])
                    self.assertEqual(identity["methodology"]["warmups_per_timed_sample"], 2)
                    self.assertEqual(identity["methodology"]["dispersion"], ["mad", "p25", "p75"])
                    self.assertEqual(identity["methodology"]["engine_order"], "cyclic rotations; each engine occupies every position")
                    self.assertIn("fresh engine process per round", identity["methodology"]["sample_process"])
                    self.assertEqual(result["total_ms"], 3.8)

    def test_clickbench_runner_passes_metadata_to_every_result(self):
        script = (Path(__file__).resolve().parent / "run_clickbench.sh").read_text()
        self.assertIn('"$KEYTEN_PYTHON" runner/benchmark_metadata.py', script)
        self.assertIn('--suite clickbench', script)
        self.assertIn('--native-store "$PWD/$WORK/hits10m.k10dir"', script)
        for engine in ("keyten", "polars", "duckdb"):
            self.assertIn(f'results/clickbench-10m/{engine}.json "$METADATA"', script)


class TickOpsTaqMetadataTests(TestCase):
    def _results(self, suite, native=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            digest = hashlib.sha256(b"fixture binary").hexdigest()
            artifact = {
                "build": {"git_sha": "abc123", "build_state": "clean"},
                "distributions": {"fixture": {"native_artifacts": {"engine.so": digest}}},
            }
            layout = {"store": str(root / "fixture.k10dir"), "columns": {"id": {"encodings": {"7": 1}}}}
            metadata_path = root / "metadata.json"
            argv = ["benchmark_metadata.py", "--suite", suite,
                    "--repo", str(root), "--harness", str(root), "--adapter", str(root),
                    "--machine-out", str(root / "machine.json"), "--metadata-out", str(metadata_path),
                    "--samples", "3", "--warmups", "0", "--workers", "6",
                    "--benchmark-mode", "resident-memory"]
            if native:
                argv += ["--native-store", layout["store"]]
            with patch.object(sys, "argv", argv), \
                 patch.object(benchmark_metadata, "_git_state", return_value={"sha": "runner"}), \
                 patch.object(benchmark_metadata, "_engine_artifact", return_value=artifact), \
                 patch.object(benchmark_metadata, "store_layout", return_value=layout), \
                 patch.object(benchmark_metadata.importlib.metadata, "distributions", return_value=[]):
                benchmark_metadata.main()
            metadata = json.loads(metadata_path.read_text())
            source = root / "timings.txt"
            if suite == "tickops":
                source.write_text("idx,name,query,ms\n1,test,select test,1.25\n")
            else:
                source.write_text("idx|status|query|run1timeNS|run2timeNS|run3timeNS\n"
                                  "0|success|load|999999999||\n"
                                  "1|success|select test|3000000|1250000|2000000\n"
                                  "2|error|bad||||\n")
            for engine in ("keyten", "duckdb", "polars"):
                out = root / f"{engine}.json"
                # Exercise each converter's optional CLI metadata argument.
                subprocess.run([sys.executable, str(Path(__file__).resolve().parent / f"convert_{suite}.py"),
                                str(source), engine, "test", str(root / "machine.json"), str(out),
                                str(metadata_path)], check=True, capture_output=True, text=True)
                result = json.loads(out.read_text())
                self.assertEqual(result["benchmark"], metadata)
                self.assertEqual(result["threads"], 6)
                self.assertEqual(result["execution_mode"], "in-memory")
                self.assertEqual(result["queries"][0]["ms"], 1.25)
                self.assertEqual(len(result["queries"]), 1)
                self.assertEqual(metadata["engines"][engine]["build"]["git_sha"], "abc123")
                self.assertEqual(metadata["engines"][engine]["distributions"]["fixture"]["native_artifacts"]["engine.so"], digest)
                self.assertEqual(metadata["native_stores"], [layout] if native else [])
                self.assertEqual(len(metadata["artifacts"]["harness_sha256"]), 64)
            return metadata["methodology"]

    def test_tickops_methodology_and_result_identity(self):
        method = self._results("tickops")
        self.assertEqual(method["statistic"], "min")
        self.assertEqual(method["timed_samples_per_query"], 3)
        self.assertEqual(method["warmups_per_query"], 0)
        self.assertEqual(method["engine_order"], "keyten, duckdb, polars")
        self.assertEqual(method["expected_queries_by_engine"], {"keyten": 8, "polars": 8, "duckdb": 7})
        self.assertEqual(method["known_gaps"], {"duckdb": [5]})
        self.assertFalse(method["raw_samples_retained"])
        self.assertFalse(method["io_included"])
        self.assertFalse(method["load_included"])

    def test_taq_methodology_and_result_identity(self):
        method = self._results("taq")
        self.assertEqual(method["statistic"], "min")
        self.assertEqual(method["timed_samples_per_query"], 3)
        self.assertEqual(method["warmups_per_query"], 0)
        self.assertEqual(method["expected_query_count"], 53)
        self.assertEqual(method["engine_order"], "keyten, duckdb, polars")
        self.assertTrue(method["raw_samples_retained"])
        self.assertFalse(method["raw_samples_in_result_json"])
        self.assertIn("not guaranteed cold", method["cache_flush"])
        for field in ("io_included", "load_included", "prepare_run_included", "gc_included", "load_transform_sort_included"):
            self.assertFalse(method[field])

    def test_tickops_and_taq_metadata_preserve_optional_native_encodings(self):
        # Current adapters have no native stores. The shared optional metadata
        # contract must still preserve layouts if a store is explicitly given.
        for suite in ("tickops", "taq"):
            with self.subTest(suite=suite):
                self._results(suite, native=True)

    def test_tickops_and_taq_runners_pass_metadata_to_every_result(self):
        for suite, results in (("tickops", "tickops"), ("taq", "taq-small")):
            script = (Path(__file__).resolve().parent / f"run_{suite}.sh").read_text()
            self.assertIn('"$KEYTEN_PYTHON" runner/benchmark_metadata.py', script)
            self.assertIn(f'--suite {suite}', script)
            self.assertIn('--samples 3 --warmups 0 --workers "$THREADS"', script)
            self.assertIn('--benchmark-mode resident-memory', script)
            self.assertNotIn('--native-store', script)
            for engine in ("keyten", "duckdb", "polars"):
                self.assertIn(f'results/{results}/{engine}.json "$METADATA"', script)


if __name__ == "__main__":
    main()
