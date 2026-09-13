import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from unittest import TestCase, main

from benchmark_metadata import _engine_artifact, machine_facts


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


if __name__ == "__main__":
    main()
