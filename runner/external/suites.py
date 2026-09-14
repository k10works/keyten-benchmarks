"""Shared data preparation and query catalogs for the external engines."""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
TABLES = ('lineitem', 'orders', 'customer', 'supplier', 'part', 'partsupp', 'nation', 'region')


def pdsh_sql(scale_factor=10):
    """Extract the vendored SQL f-strings without importing/running its harness."""
    result = {}
    names = {'line_item': 'lineitem', 'part_supp': 'partsupp'}
    for i in range(1, 23):
        tree = ast.parse((ROOT / f'harnesses/pdsh/queries/duckdb/q{i}.py').read_text())
        bindings = {"fraction": str(0.0001 / scale_factor)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
                continue
            target = node.targets[0].id
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
                method = node.value.func.attr
                if method.startswith('get_') and method.endswith('_ds'):
                    table = method[4:-3]
                    bindings[target] = names.get(table, table)
            if target == 'query_str':
                parts = []
                for part in node.value.values:
                    parts.append(part.value if isinstance(part, ast.Constant) else bindings[part.value.id])
                result[i] = ''.join(parts).strip()
    return result


def psv(name):
    with (ROOT / f'harnesses/taq/artifacts/queries/inmemory/{name}.psv').open() as f:
        return list(csv.DictReader(f, delimiter='|'))


def parameters(path):
    result = {}
    for name in ('aFreqInstr', 'mostFreqInstr', 'anInfreqInstr', 'twentyInstrs', 'hundredInstrs', 'fivehundredInfreqInstrs'):
        values = (path / f'{name}.txt').read_text().splitlines()
        result[name] = values if name.endswith('Instrs') else values[0]
    return result


def literal(value):
    if isinstance(value, list):
        return '(' + ','.join(map(literal, value)) + ')'
    return "'" + str(value).replace("'", "''") + "'"


def queries(suite, engine, date='2026-04-01', param_dir=None, scale_factor=10):
    if engine == 'l' and suite != 'taq':
        catalog = json.loads((ROOT / f'adapters/external/{suite}-l.json').read_text())
        return {int(k): v.replace('.bench.fraction', str(.0001 / scale_factor)) if v else None for k, v in catalog.items()}
    if suite == 'clickbench':
        sql = {i: q for i, q in enumerate((ROOT / 'adapters/clickbench-duckdb-queries.sql').read_text().splitlines(), 1)}
    elif suite == 'pdsh':
        sql = pdsh_sql(scale_factor)
    elif suite == 'tickops':
        spec = importlib.util.spec_from_file_location('tick_sql', ROOT / 'harnesses/tickops/queries_duckdb.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sql = {i: mod.SQL.get(i) for i in range(1, 9)}
    else:
        if engine == 'l':
            result = {int(r['idx']): r['query'].replace('devSpread: dev ', 'devSpread: sdev ') for r in psv('kdb')}
            result[28] = 'select time,price:?[20<=1+til count i;20 mavg price;0n] from trade where sym=mostFreqInstr'
            return result
        params = parameters(param_dir)
        params['datadate'] = f"TIMESTAMP '{date}'"
        sql = {}
        for row in psv('duckdb'):
            bindings = {str(i): params[k.strip()] if k.strip() == 'datadate' else literal(params[k.strip()])
                        for i, k in enumerate(row['parameter'].split(','), 1) if k.strip()}
            sql[int(row['idx'])] = re.sub(r'\$(\d+)', lambda m: bindings[m[1]], row['query'])
    if suite == 'clickbench':
        sql = deterministic_clickbench(sql)
    if engine == 'questdb':
        overrides = ROOT / f'adapters/external/{suite}-questdb.json'
        sql = {i: quest_sql(q) if q else None for i, q in sql.items()}
        if suite == 'taq':
            sql = {i: questdb_taq_query(i, quest_taq(q, date), parameters(param_dir), date) for i, q in sql.items()}
        if overrides.exists():
            sql.update({int(k): v.replace('{fraction}', str(.0001 / scale_factor)) if v else None for k, v in json.loads(overrides.read_text()).items()})
    return sql


def quest_sql(query):
    import duckdb
    query = re.sub(r"('[^']+')::date", r"date \1", query, flags=re.I)
    query = re.sub(r"(?:date|timestamp) '[^']+'\s*\+\s*interval '[^']+'\s*(day|month|year)",
                   lambda m: "'" + str(duckdb.sql('select ' + m[0]).fetchone()[0]) + "'", query, flags=re.I)
    query = re.sub(r"\bdate ('[^']+')", r"\1", query, flags=re.I)

    query = re.sub(r'\bMIN\((URL|Title|Referer)\)', r'MIN(CAST(\1 AS STRING))', query, flags=re.I)
    having = re.search(r' HAVING COUNT\(\*\) > (\d+)( ORDER BY .+)', query, re.I)
    if having:
        query = 'SELECT * FROM (' + query[:having.start()] + ') WHERE c > ' + having[1] + having[2]
    query = re.sub(r'\bSTRLEN\(', 'length(', query, flags=re.I)
    query = re.sub(r'\bSTDDEV\(', 'stddev_samp(', query, flags=re.I)
    query = re.sub(r'\bLIMIT\s+(\d+)\s+OFFSET\s+(\d+)',
                   lambda m: f'LIMIT {m[2]},{int(m[2]) + int(m[1])}', query, flags=re.I)
    return query.rstrip(';')


def load_tables(suite, data, date):
    if suite == 'clickbench':
        table = pq.read_table(data)
        for name, typ in [('EventTime', pa.timestamp('s')), ('EventDate', pa.date32())]:
            col = table[name]
            if pa.types.is_integer(col.type):
                if name == 'EventDate':
                    col = pc.cast(col, pa.int32())
                table = table.set_column(table.schema.get_field_index(name), name, pc.cast(col, typ))
        return {'hits': table}
    if suite == 'tickops':
        return {t: pq.read_table(data / f'{t}.parquet') for t in ('trades', 'quotes')}
    if suite == 'pdsh':
        return {t: pq.read_table(data / f'{t}.parquet') for t in TABLES}
    result = {'exnames': pq.read_table(data / 'exnames.parquet')}
    for name in ('master', 'trade', 'quote'):
        folder = data / name / f'date={date}'
        files = sorted(folder.glob('*.parquet'))
        if not files:
            raise FileNotFoundError(folder)
        table = pa.concat_tables([pq.ParquetFile(f).read() for f in files])
        if 'date' in table.column_names:
            table = table.drop(['date'])
        if name != 'master':
            table = table.sort_by([('sym', 'ascending'), ('time', 'ascending')])
        result[name] = table
    return result


def prepare_table(suite, name, table, engine, date):
    if suite != 'taq' or name not in ('trade', 'quote'):
        return table
    # TAQ Parquet stores nanoseconds since midnight. SQL uses timestamps;
    # the vendored q queries use timespans, exactly as the native kdb harness.
    epoch = pa.scalar(date, type=pa.string()).cast(pa.timestamp('ns')).cast(pa.int64()).as_py()
    for col in ('time', 'participantTimestamp', 'tradeReportingFacilityTRFTimestamp', 'FINRAADFTimestamp'):
        if col not in table.column_names:
            continue
        values = pc.cast(table[col], pa.int64())
        values = pc.cast(values, pa.duration('ns')) if engine == 'l' else pc.cast(pc.add(values, epoch), pa.timestamp('ns'))
        table = table.set_column(table.schema.get_field_index(col), col, values)
    return table


def setup_l(client, suite, param_dir):
    if suite != 'taq':
        return
    for name, value in parameters(param_dir).items():
        k = client.sdk.K.symbol_vec(value) if isinstance(value, list) else client.sdk.K.symbol(value)
        client.db.query_with_args('{[n;v]n set v}', client.sdk.K.symbol(name), k)
    # q exchange columns are chars, while the portable input stores strings.
    client.execute('exnames:exec (first each string ex)!name from exnames')
    for name in ('trade', 'quote'):
        for col in ('ex', 'source', 'stop', 'shortSaleRestrictionIndicator'):
            if col in client.db.query(f'cols {name}').to_python():
                client.execute(f'{name}:update {col}:first each string {col} from {name}')
    text = (param_dir / 'timeBuckets.txt').read_text().splitlines()
    labels, spans = zip(*(line.split('=') for line in text))
    client.execute('timeBuckets:(`' + '`'.join(labels) + ')!(' + ';'.join(spans) + ')')
    client.execute('timeBucketsStep:`s#value[timeBuckets]!key timeBuckets')
    client.execute('system "l ' + str(ROOT / 'harnesses/taq/src/pivot.q') + '"')


def quest_taq(query, date):
    import duckdb
    query = re.sub(r"TIMESTAMP '[^']+' \+ INTERVAL '[^']+'",
                   lambda m: "CAST('" + str(duckdb.sql('select ' + m[0]).fetchone()[0]) + "' AS TIMESTAMP_NS)", query)
    query = query.replace("time BETWEEN '-infinity'::TIMESTAMP AND 'infinity'::TIMESTAMP", 'time IS NOT NULL')
    query = re.sub(r"time::TIME BETWEEN '([^']+)' AND '([^']+)'",
                   lambda m: f"time BETWEEN '{date}T{m[1]}' AND '{date}T{m[2]}'", query)
    query = query.replace('sym::VARCHAR', 'sym')
    query = re.sub(r'\* EXCLUDE \([^)]*\)', '*', query)
    query = query.replace('* RENAME bucket AS minute', '*, bucket AS minute')
    query = re.sub(r"time_bucket\(INTERVAL '(\d+) minutes?', time\)::TIME",
                   lambda m: "timestamp_floor('" + m[1] + "m', time)", query)
    query = re.sub(r'FIRST\((\w+) ORDER BY rowid\)', r'arg_min(\1, rowid)', query)
    query = re.sub(r'LAST\((\w+) ORDER BY rowid\)', r'arg_max(\1, rowid)', query)
    return query


def deterministic_clickbench(sql):
    """Resolve otherwise arbitrary LIMIT cutoffs identically in SQL and qSQL."""
    result = {}
    for idx, query in sql.items():
        match = re.search(r'GROUP BY (.+?)(?: HAVING .+?)? ORDER BY (.+?) LIMIT', query, re.I)
        if match:
            query = query.replace(' ORDER BY ' + match[2] + ' LIMIT',
                                  ' ORDER BY ' + match[2] + ', ' + match[1] + ' LIMIT')
        elif idx == 18:
            query = query.replace(' LIMIT', ' ORDER BY UserID, SearchPhrase LIMIT')
        result[idx] = query
    return result


def setup_questdb(client, suite, date, param_dir):
    if suite != 'taq':
        return
    labels, bounds = [], []
    for line in (param_dir / 'timeBuckets.txt').read_text().splitlines():
        label, span = line.split('=')
        labels.append(label)
        bounds.append(date + 'T' + span.split('D')[1])
    client.load('timeBuckets', pa.table({'bucket': labels,
                'bound': pc.cast(pa.array(bounds), pa.timestamp('ns')),
                'rowid': list(range(len(labels)))}))


def questdb_taq_query(idx, query, params, date):
    if idx in (39, 40):
        key = 'ex' if idx == 39 else 'sym'
        where = '' if idx == 39 else ' WHERE corr <> 0'
        return f'SELECT t.{key}, tb.bucket AS timeBucket, count(*) AS cnt, sum(size) AS size FROM trade t ASOF JOIN timeBuckets tb{where} GROUP BY t.{key}, tb.bucket'
    if idx in (50, 51, 52, 53):
        left = 't.time,t.price,t.size,t.stop,t.cond,t.ex'
        if idx != 51:
            left = 't.sym,' + left
        right = 'q.bid,q.ask,q.bsize,q.asize,q.cond AS quotecond'
        if idx in (50, 51):
            right += ',q.ex AS quoteex'
        where = {
            50: 't.sym IN ' + literal(params['twentyInstrs']),
            51: 't.sym = ' + literal(params['aFreqInstr']),
            52: 't.size > 500000',
            53: f"t.time BETWEEN '{date}T16:20:00' AND '{date}T16:30:00'",
        }[idx]
        keys = 'sym,ex' if idx == 52 else 'sym'
        return f'SELECT {left},{right} FROM trade t ASOF JOIN quote q ON ({keys}) WHERE {where}'
    if idx in (45, 46, 47):
        pattern = r'\(price IS DISTINCT FROM (LAG\(price\) OVER \([^)]+\))\)::INT'
        def distinct(m):
            prev = m[1]
            return f'CASE WHEN price IS NULL AND {prev} IS NULL THEN 0 WHEN price IS NULL OR {prev} IS NULL THEN 1 WHEN price <> {prev} THEN 1 ELSE 0 END'
        query = re.sub(pattern, distinct, query)
    return query
