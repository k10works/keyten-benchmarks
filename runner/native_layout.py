# keyten-benchmarks/runner/native_layout.py
"""Elected encodings per column of a native store, from record headers only."""
import struct
from collections import Counter
from pathlib import Path


def _manifest_entries(manifest: bytes):
    assert manifest[:8] == b"k10manT\0", "unsupported manifest magic"
    version, count, rows = struct.unpack_from("<HIQ", manifest, 8)
    assert version == 1, "unsupported manifest version"
    at = 22

    def string():
        nonlocal at
        size, = struct.unpack_from("<I", manifest, at)
        at += 4
        value = manifest[at:at + size].decode("utf-8")
        at += size
        return value

    for _ in range(count):
        name = string()
        at += 1  # kind byte
        filename = string()
        offset, size = struct.unpack_from("<QQ", manifest, at)
        at += 16
        yield rows, name, filename, offset, size


def _column_layout(source, offset, size, rows):
    source.seek(offset)
    root = source.read(size)
    assert root[:8] == b"k10root\0"
    assert struct.unpack_from("<H", root, 8)[0] == 1
    root_rows, records, record_bytes, required_bytes = struct.unpack_from("<QQQQ", root, 16)
    assert root_rows == rows and record_bytes == records * 24
    encodings = Counter()
    for index in range(records):
        record_at, _, record_rows = struct.unpack_from("<QQQ", root, 64 + index * 24)
        source.seek(record_at)
        header = source.read(64)
        assert struct.unpack_from("<Q", header, 8)[0] == record_rows
        encodings[str(header[1])] += 1
    dictionary_form = None
    if required_bytes:
        required_at = 64 + ((record_bytes + 63) & ~63)
        dictionary_form = struct.unpack_from("<H", root, required_at + 4)[0]
    return {"encodings": dict(encodings), "dictionary_form": dictionary_form}


def store_layout(directory) -> dict:
    directory = Path(directory)
    manifest = (directory / "manifest.k10").read_bytes()
    columns = {}
    rows = 0
    for rows, name, filename, offset, size in _manifest_entries(manifest):
        assert Path(filename).name == filename and ".." not in filename
        with open(directory / filename, "rb") as source:
            columns[name] = _column_layout(source, offset, size, rows)
    return {"store": str(directory), "rows": rows, "columns": columns}
