#!/usr/bin/env python3
"""Run QuestDB and l through their official SDKs, with a fresh DuckDB reference.

Results are written to a separate run directory; historical board results are
never silently mixed with a new machine, dataset, or client timing boundary.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from collections import Counter
from datetime import date as Date
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import time

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from external.clients import L, QuestDB
from external.suites import ROOT, load_tables, prepare_table, queries, setup_l, setup_questdb


def canonical(table, suite, date):
    arrays = []
    for name, col in zip(table.column_names, table.columns):
        typ = col.type
        if pa.types.is_dictionary(typ):
            col = pc.cast(col, typ.value_type)
            typ = col.type
        if pa.types.is_timestamp(typ):
            col = pc.cast(pc.cast(col, pa.timestamp('ns')), pa.int64())
            if suite == 'taq':
                epoch = pa.scalar(date).cast(pa.timestamp('ns')).cast(pa.int64()).as_py()
                col = pc.subtract(col, epoch)
        elif pa.types.is_duration(typ):
            col = pc.cast(col, pa.int64())
        elif pa.types.is_date(typ):
            col = pc.cast(pc.cast(col, pa.date32()), pa.int32())
        elif pa.types.is_time(typ):
            col = pc.cast(pc.cast(col, pa.time64('ns')), pa.int64())
        arrays.append(col)
    table = pa.Table.from_arrays(arrays, names=[f'c{i}' for i in range(len(arrays))])
    return table


def compare(reference, candidate, suite, date):
    if reference.shape != candidate.shape:
        raise AssertionError(f'shape {candidate.shape} != reference {reference.shape}')
    for i, field in enumerate(reference.schema):
        col = candidate.column(i)
        if pa.types.is_date(field.type) and pa.types.is_timestamp(col.type):
            midnight = pc.cast(pc.cast(col, pa.date32()), col.type)
            if not col.equals(midnight):
                raise AssertionError(f'{field.name}: date result contains a time of day')
            candidate = candidate.set_column(i, candidate.column_names[i], pc.cast(col, pa.date32()))
    left, right = [canonical(t, suite, date) for t in (reference, candidate)]
    if not left.num_rows:
        return
    # Compare the full multiset of rows, retaining duplicates. Column aliases
    # differ between SQL and qSQL; projection order remains part of the contract.
    keys = [(n, 'ascending') for n in left.column_names]
    left, right = left.sort_by(keys), right.sort_by(keys)
    for n, a, b in zip(reference.column_names, left.columns, right.columns):
        if not pc.is_null(a).equals(pc.is_null(b)):
            raise AssertionError(f'{n}: null positions differ')
        if pa.types.is_integer(a.type) and pa.types.is_floating(b.type):
            if not a.equals(b.cast(a.type)):
                raise AssertionError(f'{n}: integer values differ')
            continue
        if pa.types.is_floating(a.type) or pa.types.is_floating(b.type):
            a, b = pc.cast(a, pa.float64()), pc.cast(b, pa.float64())
            delta = pc.abs(pc.subtract(a, b))
            tol = pc.add(1e-6, pc.multiply(1e-4 if suite == 'tickops' else 1e-7, pc.max_element_wise(pc.abs(a), pc.abs(b))))
            ok = pc.or_(pc.less_equal(delta, tol), pc.equal(a, b))
            ok = pc.or_(ok, pc.and_(pc.is_nan(a), pc.is_nan(b)))
            if not pc.all(pc.fill_null(ok, True)).as_py():
                raise AssertionError(f'{n}: floating values differ')
        else:
            # Integers must remain exact, including 64-bit identifiers.
            if not a.equals(b.cast(a.type)):
                raise AssertionError(f'{n}: values differ')


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return result



@contextmanager
def start_engine(name, work, args, tables):
    cls, binary = (L, args.l_bin) if name == 'l' else (QuestDB, args.questdb_home)
    with cls(binary, work, args.threads) as engine:
        for table_name, table in tables.items():
            prepared = prepare_table(args.suite, table_name, table, name, args.date)
            if args.suite == 'taq' and name == 'questdb' and table_name in ('trade', 'quote'):
                import numpy as np
                prepared = prepared.append_column('rowid', pa.array(np.arange(prepared.num_rows, dtype=np.int64)))
            engine.load(table_name, prepared)
        if name == 'l':
            engine.execute('.bench.j:{[a;b;ka;kb]b:flip (flip b),(enlist ka)!enlist b kb;ej[enlist ka;a;b]}')
            setup_l(engine, args.suite, args.param_dir)
        else:
            setup_questdb(engine, args.suite, args.date, args.param_dir)
        yield engine


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def measure(run, samples, warmups):
    for _ in range(warmups):
        run()
    times = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        result = run()
        times.append((time.perf_counter_ns() - start) / 1e6)
        del result
    return times

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', choices=['taq', 'tickops', 'pdsh', 'clickbench'])
    parser.add_argument('data', type=Path)
    parser.add_argument('--engines', default='questdb,l')
    parser.add_argument('--threads', type=positive, default=4)
    parser.add_argument('--samples', type=positive, default=3)
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--scale-factor', type=float, default=10)
    parser.add_argument('--date', default='2026-04-01')
    parser.add_argument('--param-dir', type=Path, default=ROOT / 'harnesses/taq/artifacts/parameters/small')
    parser.add_argument('--l-bin', type=Path, default=Path(os.environ.get('L_BIN', ROOT.parent / 'l/l')))
    parser.add_argument('--questdb-home', type=Path, default=Path(os.environ.get('QUESTDB_HOME', ROOT.parent / 'questdb-10.0.1-rt-linux-x86-64')))
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--correctness-only', action='store_true')
    args = parser.parse_args()
    if not math.isfinite(args.scale_factor) or args.scale_factor <= 0:
        parser.error('--scale-factor must be positive and finite')
    if args.warmups < 0:
        parser.error('--warmups must be non-negative')
    engines = args.engines.split(',')
    if len(set(engines)) != len(engines) or any(e not in ('questdb', 'l') for e in engines):
        parser.error('--engines must contain questdb and/or l without duplicates')
    Date.fromisoformat(args.date)
    out = args.out_dir or ROOT / '.work/external' / (args.suite + '-' + time.strftime('%Y%m%d-%H%M%S'))
    out = out.resolve()
    # Refuse reuse: stale successful files must never survive a failed rerun.
    out.mkdir(parents=True, exist_ok=False)
    print(f'Run directory: {out}', flush=True)
    tables = load_tables(args.suite, args.data.resolve(), args.date)
    reference = duckdb.connect()
    reference.execute(f'SET threads={args.threads}')
    for name, table in tables.items():
        reference.register('input_table', prepare_table(args.suite, name, table, 'duckdb', args.date))
        reference.execute(f'CREATE TABLE {name} AS SELECT * FROM input_table')
    if args.suite == 'taq':
        reference.execute('CREATE TABLE timeBuckets (bucket VARCHAR, bound TIME)')
        for line in (args.param_dir / 'timeBuckets.txt').read_text().splitlines():
            label, value = line.split('=')
            reference.execute('INSERT INTO timeBuckets VALUES (?, ?::TIME)', [label, value.split('D')[1]])
    refs = queries(args.suite, 'duckdb', args.date, args.param_dir, args.scale_factor)
    report = {'suite': args.suite, 'engines': engines, 'queries': {}, 'status': 'pass'}
    machine = {'cpu': platform.processor(), 'cores': os.cpu_count(),
               'ram_gb': os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') // 2**30,
               'os': f'{platform.system()} {platform.machine()}', 'date': Date.today().isoformat()}
    source_files = [args.data] if args.data.is_file() else sorted(args.data.rglob('*.parquet'))
    metadata = {'input_sha256': {str(p.resolve()): sha256(p) for p in source_files},
                'adapter_sha256': {str(p.relative_to(ROOT)): sha256(p) for folder in (ROOT / 'runner/external', ROOT / 'adapters/external') for p in sorted(folder.glob('*')) if p.is_file()},
                'workers': args.threads, 'samples': args.samples, 'warmups': args.warmups,
                'data': str(args.data.resolve()), 'tables': {n: {'rows': t.num_rows, 'schema': str(t.schema)} for n,t in tables.items()},
                'timing': 'client wall time through complete native SDK materialization; conversion for correctness excluded',
                'reference': {'engine': 'duckdb', 'version': duckdb.__version__}}
    cpuinfo = Path('/proc/cpuinfo')
    if cpuinfo.exists():
        machine['cpu'] = next((line.split(':', 1)[1].strip() for line in cpuinfo.read_text().splitlines() if line.startswith('model name')), machine['cpu'])
    metadata['adapter_sha256']['runner/run_external.py'] = sha256(Path(__file__))
    metadata['query_contract'] = 'deterministic grouped LIMIT ties' if args.suite == 'clickbench' else 'vendored suite semantics'
    (out / 'metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
    refdir = out / 'duckdb'
    refdir.mkdir()
    for idx, sql in refs.items():
        if sql is None:
            continue
        result = reference.execute(sql).to_arrow_table()
        pq.write_table(result, refdir / f'q{idx:02}.parquet')
    timed, verified = {}, {}
    for name in engines:
        work = out / name
        work.mkdir()
        catalog = queries(args.suite, name, args.date, args.param_dir, args.scale_factor)
        with start_engine(name, work, args, tables) as engine:
            entries, passed = {}, []
            for idx, query in catalog.items():
                if name == 'questdb' and args.suite == 'taq' and query:
                    schema = pq.read_schema(refdir / f'q{idx:02}.parquet')
                    projection = ','.join('"' + n.replace('"', '""') + '"' for n in schema.names)
                    query = f'SELECT {projection} FROM ({query})'
                    catalog[idx] = query
                entry = {'query': query}
                entries[idx] = entry
                if query is None:
                    reasons = {('l', 'tickops', 4): 'l 0.9752 lacks wj1 for duration-window volatility',
                               ('l', 'clickbench', 29): 'Native regex extraction query not implemented'}
                    reason = reasons.get((name, args.suite, idx), 'No reference implementation for grouped EWM standard deviation')
                    entry.update(status='gap', reason=reason)
                    continue
                if refs.get(idx) is None:
                    entry.update(status='gap', reason='No reference implementation available; excluded from timing')
                    continue
                try:
                    result = engine.query(query)
                    table = engine.arrow(result, query)
                except Exception as error:
                    entry.update(status='execution_error', error=f'{type(error).__name__}: {error}')
                    print(f'{name} q{idx}: EXECUTION ERROR {error}', flush=True)
                    continue
                pq.write_table(table, work / f'q{idx:02}.parquet')
                try:
                    compare(pq.read_table(refdir / f'q{idx:02}.parquet'), table, args.suite, args.date)
                except (AssertionError, pa.ArrowException) as error:
                    entry.update(status='mismatch', error=str(error))
                    report['status'] = 'fail'
                else:
                    entry.update(status='pass', rows=table.num_rows)
                    passed.append(idx)
                print(f'{name} q{idx}: {entry["status"]}', flush=True)
            report['queries'][name] = entries
            (out / 'correctness.json').write_text(json.dumps(report, indent=2)+'\n')
            verified[name] = {'passed': passed, 'catalog': catalog,
                              'version': engine.version, 'identity': engine.identity,
                              'client_version': engine.client_version, 'mode': engine.mode}
            if not passed:
                report['status'] = 'fail'
    report['engine_identity'] = {name: {k: v for k, v in info.items() if k in ('version', 'identity', 'client_version', 'mode')} for name, info in verified.items()}
    report['coverage'] = {name: dict(Counter(e['status'] for e in entries.values()))
                          for name, entries in report['queries'].items()}
    if report['status'] == 'pass' and any(
            e['status'] != 'pass' for entries in report['queries'].values() for e in entries.values()):
        report['status'] = 'partial'
    (out / 'correctness.json').write_text(json.dumps(report, indent=2)+'\n')
    # Every requested engine must finish correctness before fresh timing servers
    # are started. A mismatch suppresses all timing and board result files.
    if report['status'] != 'fail' and not args.correctness_only:
        for name in engines:
            work = out / name / 'timing-server'
            work.mkdir()
            with start_engine(name, work, args, tables) as engine:
                details = verified[name]
                if engine.identity != details['identity'] or engine.version != details['version']:
                    raise RuntimeError(f'{name}: binary changed between correctness and timing')
                rows = []
                for idx in details['passed']:
                    query = details['catalog'][idx]
                    samples = measure(lambda: engine.query(query), args.samples, args.warmups)
                    rows.append({'idx': idx, 'query': query, 'ms': min(samples), 'samples_ms': samples})
                timed[name] = {'engine': name, 'version': engine.version, 'machine': machine,
                               'metadata': {**metadata, 'benchmark_mode': engine.mode,
                                            'client_version': engine.client_version, 'binary_sha256': engine.identity},
                               'queries': rows, 'total_ms': sum(r['ms'] for r in rows)}
        rows = []
        for idx, sql in refs.items():
            if sql is None:
                continue
            samples = measure(lambda: reference.execute(sql).to_arrow_table(), args.samples, args.warmups)
            rows.append({'idx': idx, 'query': sql, 'ms': min(samples), 'samples_ms': samples})
        timed['duckdb'] = {'engine': 'duckdb', 'version': duckdb.__version__, 'machine': machine,
                           'metadata': {**metadata, 'benchmark_mode': 'resident-memory'},
                           'queries': rows, 'total_ms': sum(r['ms'] for r in rows)}
    reference.close()
    # Emit only after every requested engine has completed its gate.
    if report['status'] != 'fail':
        for name, result in timed.items():
            (out / f'{name}.json').write_text(json.dumps(result, indent=2)+'\n')
    if timed:
        index = {'suites': [{'id': '.', 'title': args.suite + ' · external engines',
                            'dataset': args.data.name,
                            'note': f'New SDK run, {args.threads} query workers, best of {args.samples}. '
                                    'QuestDB uses warm native storage; l and DuckDB use resident memory. '
                                    'Only verified queries receive timings; see correctness.json for coverage.',
                            'engines': ['duckdb'] + engines}]}
        (out / 'index.json').write_text(json.dumps(index, indent=2)+'\n')
        print(f'Board: /board/?results={os.path.relpath(out, ROOT / "board")}')
    print('Coverage: ' + json.dumps(report['coverage']))
    print(f'Correctness: {report["status"]}; details: {out / "correctness.json"}')
    return 0 if report['status'] != 'fail' else 1


if __name__ == '__main__':
    raise SystemExit(main())
