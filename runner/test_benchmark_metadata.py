from unittest import TestCase, main

from benchmark_metadata import machine_facts


class MachineFactsTests(TestCase):
    def test_machine_probe_has_reproducibility_fields(self) -> None:
        facts = machine_facts()
        for name in ("cpu", "cores", "affinity_cpus", "ram_bytes", "os", "architecture", "kernel"):
            self.assertIn(name, facts)
        self.assertGreaterEqual(facts["cores"], 1)
        self.assertTrue(facts["affinity_cpus"])
        self.assertGreater(facts["ram_bytes"], 0)


if __name__ == "__main__":
    main()
