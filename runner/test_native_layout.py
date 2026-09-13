# keyten-benchmarks/runner/test_native_layout.py
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main, skipUnless

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import keyten as kt
except ImportError:  # the harness venv always has keyten; developer shells may not
    kt = None

from native_layout import store_layout


@skipUnless(kt is not None, "keyten wheel required")
class StoreLayoutTests(TestCase):
    def test_reports_every_column_with_its_record_encodings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "t.k10dir"
            df = kt.DataFrame([
                kt.Series.int("k", list(range(5000))),
                kt.Series.string("s", ["x"] * 5000),
            ])
            df.write_native(str(store))
            layout = store_layout(store)
        self.assertEqual(layout["rows"], 5000)
        self.assertEqual(set(layout["columns"]), {"k", "s"})
        # the constant text column is one record family; the sequence column another
        self.assertTrue(all(sum(c["encodings"].values()) >= 1 for c in layout["columns"].values()))
        json.dumps(layout)  # must be JSON serializable as-is


if __name__ == "__main__":
    main()
