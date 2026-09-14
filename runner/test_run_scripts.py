# keyten-benchmarks/runner/test_run_scripts.py
import re
import os
import subprocess
import tempfile
import hashlib
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

    def test_both_runners_bracket_timing_with_identity_checks(self) -> None:
        for name, first, last in (
            ("run_pdsh.sh", "for ((round = 0;", "# Capture the stores"),
            ("run_clickbench.sh", "for ((round = 0;", 'MACHINE="$WORK/machine.json"'),
        ):
            with self.subTest(runner=name):
                script = (RUNNER / name).read_text()
                self.assertIn('source runner/lib/identity.sh', script)
                self.assertIn('KEYTEN_PYTHON=', script)
                before = script.index('print_keyten_identity before')
                after = script.index('print_keyten_identity after')
                self.assertLess(before, script.index(first))
                self.assertGreater(after, script.index(first))
                self.assertLess(after, script.index(last))
                subprocess.run(["bash", "-n", str(RUNNER / name)], check=True)
        helper = (RUNNER / "lib/identity.sh").read_text()
        self.assertIn('keyten binary identity mismatch', helper)
        self.assertIn('exit 1', helper)
        subprocess.run(["bash", "-n", str(RUNNER / "lib/identity.sh")], check=True)

    def test_tickops_and_taq_bracket_all_timed_engines_after_correctness(self):
        for suite, first, last in (
            ("tickops", '"$VENV/python" "$HARNESS/harness.py" time --engine keyten', '--engine polars --data-dir "$DATA" --out-dir "$OUT" --threads "$THREADS"'),
            ("taq", 'FLUSH=./flush/noflush.sh KEYTEN_WORKERS=', '-result ../polars.psv'),
        ):
            with self.subTest(suite=suite):
                script = (RUNNER / f"run_{suite}.sh").read_text()
                self.assertIn('source runner/lib/identity.sh', script)
                # Absolute interpreter survives TAQ's cd into the copied harness.
                self.assertIn('KEYTEN_PYTHON="$PWD/$VENV/python"', script)
                before = script.index('print_keyten_identity before')
                after = script.index('print_keyten_identity after')
                self.assertEqual(script.count('print_keyten_identity before'), 1)
                self.assertEqual(script.count('print_keyten_identity after'), 1)
                self.assertLess(script.index('exit 0', script.index('BENCH_CORRECTNESS_ONLY')), before)
                self.assertLess(before, script.index(first))
                self.assertLess(script.rindex(last), after)
                self.assertLess(after, script.index('runner/benchmark_metadata.py'))
                self.assertLess(after, script.index(f'runner/convert_{suite}.py'))
                subprocess.run(["bash", "-n", str(RUNNER / f"run_{suite}.sh")], check=True)

    def test_clickbench_rotates_fresh_rounds_with_samples_and_warmups(self):
        script = (RUNNER / "run_clickbench.sh").read_text()
        self.assertIn('SAMPLES="${BENCH_SAMPLES:-12}"', script)
        self.assertIn('WARMUPS="${BENCH_WARMUPS:-2}"', script)
        self.assertIn('for ((round = 0; round < SAMPLES; round++))', script)
        for order in ('keyten polars duckdb', 'polars duckdb keyten', 'duckdb keyten polars'):
            self.assertIn(f'order=({order})', script)
        for field, value in (("RUN_WARMUP_ITERATIONS", "WARMUPS"), ("RUN_BENCHMARK_RUN_ID", "round"), ("RUN_ORDER_POSITION", "position")):
            self.assertIn(f'export {field}="${value}"', script)
        self.assertIn('--samples "$SAMPLES" --warmups "$WARMUPS"', script)
        self.assertIn('run_board*.py >> "../../$out"', script)
        self.assertIn('>> "$WORK/cb_duckdb.txt"', script)
        self.assertIn('wait "$(cat "$WORK/srv.pid")"', script)
        subprocess.run(["bash", "-n", str(RUNNER / "run_clickbench.sh")], check=True)

    def _identity_run(self, change=False, wheel=None):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "keyten binary.so"
            binary.write_bytes(b"before")
            python = Path(tmp) / "fake-python"
            python.write_text('#!/bin/bash\nprintf "%s\\n" "$TEST_BINARY"\n')
            python.chmod(0o755)
            env = dict(os.environ, KEYTEN_PYTHON=str(python), TEST_BINARY=str(binary))
            env.pop("KEYTEN_WHEEL", None)
            if wheel is not None:
                env["KEYTEN_WHEEL"] = wheel
            command = 'set -euo pipefail; source "$1"; print_keyten_identity before; '
            if change:
                command += 'printf changed > "$TEST_BINARY"; '
            command += 'print_keyten_identity after; echo completed'
            return subprocess.run(
                ["bash", "-c", command, "identity-test", str(RUNNER / "lib/identity.sh")],
                env=env, text=True, capture_output=True,
            )

    def test_identity_prints_matching_before_and_after(self):
        digest = hashlib.sha256(b"before").hexdigest()
        for wheel in (None, "/tmp/custom wheel.whl"):
            with self.subTest(wheel=wheel):
                result = self._identity_run(wheel=wheel)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [
                    f"keyten binary sha256={digest} wheel={wheel or 'pypi'} phase=before",
                    f"keyten binary sha256={digest} wheel={wheel or 'pypi'} phase=after",
                    "completed",
                ])

    def test_identity_exits_nonzero_when_binary_changes(self):
        result = self._identity_run(change=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("keyten binary identity mismatch", result.stderr)
        self.assertIn("phase=before", result.stdout)
        self.assertIn("phase=after", result.stdout)
        self.assertNotIn("completed", result.stdout)


if __name__ == "__main__":
    main()
