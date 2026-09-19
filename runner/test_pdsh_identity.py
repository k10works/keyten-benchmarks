"""Exercise the adapter's real identity helpers without loading SF10 data."""
import ast
from functools import lru_cache
import hashlib
import pathlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Callable
import unittest
from unittest.mock import patch


def helpers(root):
    source = Path(__file__).resolve().parents[1] / 'adapters/pdsh-keyten/utils.py'
    tree = ast.parse(source.read_text())
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in {'engine_stamp', 'store_is_current', 'run_query'}]
    scope = dict(hashlib=hashlib, pathlib=pathlib, lru_cache=lru_cache,
                 Any=Any, Callable=Callable, check_result=None,
                 kt=SimpleNamespace(__file__=str(root / '__init__.py'), __version__='test'))
    exec(compile(tree, str(source), 'exec'), scope)
    return scope


class IdentityTests(unittest.TestCase):
    def test_hash_is_paid_once_before_query_timer(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / '_keyten.abi3.so'
            binary.write_bytes(b'engine binary')
            native = root / 'table.k10dir'
            native.mkdir()
            (native / '.engine').write_text(hashlib.sha256(b'engine binary').hexdigest())
            scope = helpers(root)
            reads = []
            read_bytes = Path.read_bytes

            def counted(path):
                reads.append(path)
                return read_bytes(path)

            def timed(query, *args, **kwargs):
                self.assertEqual(reads, [binary], 'identity is ready before timing')
                for _ in range(3):
                    query()
                self.assertEqual(reads, [binary], 'scans must not rehash the binary')

            scope['run_query_generic'] = timed
            with patch.object(Path, 'read_bytes', counted):
                scope['run_query'](2, lambda: self.assertTrue(scope['store_is_current'](native)))

    def test_new_process_identity_rejects_old_store(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / '_keyten.abi3.so'
            binary.write_bytes(b'first engine')
            native = root / 'table.k10dir'
            native.mkdir()
            first = helpers(root)
            self.assertFalse(first['store_is_current'](native))
            (native / '.engine').write_text(first['engine_stamp']())
            self.assertTrue(first['store_is_current'](native))
            binary.write_bytes(b'next engine')
            self.assertFalse(helpers(root)['store_is_current'](native))


if __name__ == '__main__':
    unittest.main()
