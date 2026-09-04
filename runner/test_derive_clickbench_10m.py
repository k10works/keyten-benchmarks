from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

import pyarrow as pa
import pyarrow.parquet as pq

from derive_clickbench_10m import derive, sha256


class ClickBenchDerivationTests(TestCase):
    def test_derives_exact_prefix_and_records_hashes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "hits.parquet"
            output = root / "hits10m.parquet"
            manifest = root / "hits10m.manifest.json"
            pq.write_table(pa.table({"row": list(range(20)), "text": [str(i) for i in range(20)]}), source)

            record = derive(source, output, 7, manifest, "https://example.invalid/hits.parquet")

            self.assertEqual(pq.read_table(output).column("row").to_pylist(), list(range(7)))
            self.assertEqual(record["written_rows"], 7)
            self.assertEqual(record["source_sha256"], sha256(source))
            self.assertEqual(record["output_sha256"], sha256(output))
            self.assertTrue(manifest.is_file())


if __name__ == "__main__":
    main()
