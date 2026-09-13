import sys
import json
import hashlib
import struct
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
                "--samples", "3", "--warmups", "0", "--workers", "8",
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
            transcript.write_text("q00 1.25ms\nq01 2.50ms\n")
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
                    self.assertEqual(identity["methodology"]["statistic"], "min")
                    self.assertEqual(identity["methodology"]["timed_samples_per_query"], 3)
                    self.assertEqual(identity["methodology"]["expected_query_count"], 43)
                    self.assertFalse(identity["methodology"]["raw_samples_retained"])
                    self.assertEqual(result["total_ms"], 3.8)

    def test_clickbench_runner_passes_metadata_to_every_result(self):
        script = (Path(__file__).resolve().parent / "run_clickbench.sh").read_text()
        self.assertIn('"$KEYTEN_PYTHON" runner/benchmark_metadata.py', script)
        self.assertIn('--suite clickbench', script)
        self.assertIn('--native-store "$PWD/$WORK/hits10m.k10dir"', script)
        for engine in ("keyten", "polars", "duckdb"):
            self.assertIn(f'results/clickbench-10m/{engine}.json "$METADATA"', script)


if __name__ == "__main__":
    main()
