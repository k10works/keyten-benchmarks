# keyten-benchmarks/runner/test_store_stamp.py
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main, skipUnless

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harnesses" / "pdsh"))
sys.path.insert(0, str(ROOT / "adapters" / "pdsh-keyten"))
try:
    import keyten as kt
except ImportError:
    kt = None


@skipUnless(kt is not None, "keyten wheel required")
class StoreStampTests(TestCase):
    def test_stamp_is_the_binary_hash_and_stale_stores_are_detected(self) -> None:
        from utils import engine_stamp, store_is_current, write_stamp
        stamp = engine_stamp()
        self.assertEqual(len(stamp), 64)
        with tempfile.TemporaryDirectory() as tmp:
            native = Path(tmp) / "t.k10dir"
            native.mkdir()
            self.assertFalse(store_is_current(native))
            write_stamp(native)
            self.assertTrue(store_is_current(native))
            (native / ".engine").write_text("0" * 64)
            self.assertFalse(store_is_current(native))


if __name__ == "__main__":
    main()
