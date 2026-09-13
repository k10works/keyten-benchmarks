# keyten-benchmarks/runner/test_run_scripts.py
import re
from pathlib import Path
from unittest import TestCase, main

RUNNER = Path(__file__).resolve().parent


class WheelInstallContractTests(TestCase):
    def test_pdsh_runner_installs_keyten_wheel_when_requested(self) -> None:
        script = (RUNNER / "run_pdsh.sh").read_text()
        self.assertIn('if [ -n "${KEYTEN_WHEEL:-}" ]; then', script)
        self.assertRegex(
            script,
            re.compile(r'pip" install -q --no-cache-dir --force-reinstall "\$KEYTEN_WHEEL"'),
        )

    def test_pdsh_runner_prints_installed_binary_hash(self) -> None:
        script = (RUNNER / "run_pdsh.sh").read_text()
        self.assertIn("keyten binary sha256=", script)
        self.assertIn("_keyten.abi3.so", script)


if __name__ == "__main__":
    main()
