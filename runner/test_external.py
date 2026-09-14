"""Regression checks for data fidelity and the external correctness gate."""
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import contextmanager, redirect_stdout
import io
import run_external

import pyarrow as pa

from external.clients import L, QuestDB
from external.suites import ROOT, queries, pdsh_sql
from run_external import compare


class CorrectnessTests(unittest.TestCase):
    def test_row_order_is_not_part_of_unordered_relation(self):
        compare(pa.table({'a': [1, 2, 1]}), pa.table({'other': [1, 1, 2]}), 'pdsh', '2026-04-01')

    def test_duplicate_multiplicity_is_checked(self):
        with self.assertRaises(AssertionError):
            compare(pa.table({'a': [1, 2, 1]}), pa.table({'a': [1, 2, 2]}), 'pdsh', '2026-04-01')

    def test_large_identifiers_are_exact(self):
        with self.assertRaises(AssertionError):
            compare(pa.table({'id': [2**60 + 1]}), pa.table({'id': [2**60 + 2]}), 'clickbench', '2026-04-01')

    def test_large_identifier_cannot_match_a_rounded_float(self):
        with self.assertRaises(AssertionError):
            compare(pa.table({'id': [2**60 + 1]}), pa.table({'id': [float(2**60 + 1)]}), 'clickbench', '2026-04-01')

    def test_null_is_not_zero(self):
        with self.assertRaises(AssertionError):
            compare(pa.table({'v': pa.array([None], type=pa.int64())}), pa.table({'v': [0]}), 'pdsh', '2026-04-01')

    def test_small_float_accumulation_difference(self):
        compare(pa.table({'v': [1.00000001]}), pa.table({'v': [1.00000002]}), 'tickops', '2026-04-01')

    def test_shape_mismatch_is_fatal(self):
        with self.assertRaises(AssertionError):
            compare(pa.table({'v': [1]}), pa.table({'v': [1, 1]}), 'pdsh', '2026-04-01')

    def test_timestamp_nanoseconds_are_not_rounded(self):
        a = pa.table({'ts': pa.array([1780000000000000001], type=pa.timestamp('ns'))})
        b = pa.table({'ts': pa.array([1780000000000000002], type=pa.timestamp('ns'))})
        with self.assertRaises(AssertionError):
            compare(a, b, 'tickops', '2026-04-01')

    def test_quest_date_representation(self):
        a = pa.table({'d': pa.array([date(1970, 1, 2)], type=pa.date32())})
        b = pa.table({'d': pa.array([86400000], type=pa.timestamp('ms'))})
        compare(a, b, 'clickbench', '2026-04-01')
        with self.assertRaises(AssertionError):
            compare(a, pa.table({'d': pa.array([86400001], type=pa.timestamp('ms'))}), 'clickbench', '2026-04-01')


class CatalogTests(unittest.TestCase):
    def test_every_suite_has_every_query_slot(self):
        for suite, count in [('tickops', 8), ('taq', 53), ('clickbench', 43), ('pdsh', 22)]:
            for engine in ('l', 'questdb', 'duckdb'):
                with self.subTest(suite=suite, engine=engine):
                    catalog = queries(suite, engine, param_dir=ROOT / 'harnesses/taq/artifacts/parameters/small')
                    self.assertEqual(set(catalog), set(range(1, count + 1)))

    def test_pdsh_fraction_tracks_scale(self):
        self.assertIn('1e-05', pdsh_sql(10)[11])
        self.assertIn('0.0001', pdsh_sql(1)[11])

    def test_l_limit_does_not_pad_short_results(self):
        for suite in ('pdsh', 'clickbench'):
            for query in queries(suite, 'l').values():
                if query:
                    self.assertNotRegex(query, r'\b(?:10|20|25|100)#')


class RunnerGateTests(unittest.TestCase):
    def run_gate(self, candidate):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        source = root / 'input.parquet'
        source.write_bytes(b'fixture')
        output = root / 'results'
        starts = []

        class FakeEngine:
            version = 'test'
            identity = 'test-hash'
            client_version = 'test-sdk'
            mode = 'resident-memory'

            def query(self, query):
                return pa.table({'x': [candidate]})

            def arrow(self, result, query):
                return result

        @contextmanager
        def start(*args):
            starts.append(args[0])
            yield FakeEngine()

        argv = ['run_external', 'tickops', str(source), '--engines', 'l',
                '--out-dir', str(output), '--samples', '1', '--warmups', '0']
        with patch('sys.argv', argv), patch.object(run_external, 'start_engine', start), \
             patch.object(run_external, 'load_tables', return_value={'t': pa.table({'x': [1]})}), \
             patch.object(run_external, 'queries', return_value={1: 'SELECT x FROM t'}), redirect_stdout(io.StringIO()):
            status = run_external.main()
        return status, starts, output

    def test_mismatch_never_starts_timing_or_writes_board_results(self):
        status, starts, output = self.run_gate(2)
        self.assertEqual(status, 1)
        self.assertEqual(starts, ['l'])
        self.assertFalse((output / 'l.json').exists())
        self.assertFalse((output / 'duckdb.json').exists())
        self.assertFalse((output / 'index.json').exists())
        self.assertEqual(json.loads((output / 'correctness.json').read_text())['status'], 'fail')

    def test_success_uses_fresh_server_and_emits_reference(self):
        status, starts, output = self.run_gate(1)
        self.assertEqual(status, 0)
        self.assertEqual(starts, ['l', 'l'])
        for engine in ('l', 'duckdb'):
            result = json.loads((output / f'{engine}.json').read_text())
            self.assertEqual(len(result['queries']), 1)
            self.assertEqual(len(result['queries'][0]['samples_ms']), 1)
        self.assertTrue((output / 'index.json').exists())


@unittest.skipUnless(os.environ.get('BENCH_EXTERNAL_INTEGRATION') == '1', 'enable local server integration explicitly')
class SDKIntegrationTests(unittest.TestCase):
    def test_official_sdk_roundtrip(self):
        table = pa.table({'id': [2**60+1, 2**60+2], 'text': ['abc', 'xyz'],
                          'ts': pa.array([1780000000000000001, 1780000000000000002], type=pa.timestamp('ns')),
                          'v': pa.array([1.25, None], type=pa.float64())})
        for cls, binary in [(L, os.environ.get('L_BIN', ROOT.parent / 'l/l')),
                            (QuestDB, os.environ.get('QUESTDB_HOME', ROOT.parent / 'questdb-10.0.1-rt-linux-x86-64'))]:
            with self.subTest(engine=cls.name), tempfile.TemporaryDirectory() as folder:
                with cls(binary, Path(folder), 2) as engine:
                    engine.load('roundtrip', table)
                    query = 'select from roundtrip' if cls is L else 'select * from roundtrip'
                    actual = engine.arrow(engine.query(query), query)
                    compare(table, actual, 'tickops', '2026-04-01')
                    pid = engine.process
                self.assertIsNotNone(pid.poll())


if __name__ == '__main__':
    unittest.main()
