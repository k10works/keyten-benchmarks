from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

from verify_adapter import mismatches


class AdapterVerificationTests(TestCase):
    def test_detects_stale_and_missing_harness_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = root / "adapter"
            harness = root / "harness"
            adapter.mkdir()
            harness.mkdir()
            (adapter / "same.py").write_text("same\n")
            (harness / "same.py").write_text("same\n")
            (adapter / "stale.py").write_text("new\n")
            (harness / "stale.py").write_text("old\n")
            (adapter / "missing.py").write_text("missing\n")

            self.assertEqual(
                mismatches(adapter, harness),
                ["missing harness copy: missing.py", "content differs: stale.py"],
            )

    def test_accepts_matching_adapter(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = root / "adapter"
            harness = root / "harness"
            adapter.mkdir()
            harness.mkdir()
            (adapter / "q1.py").write_text("same\n")
            (harness / "q1.py").write_text("same\n")
            self.assertEqual(mismatches(adapter, harness), [])


if __name__ == "__main__":
    main()
