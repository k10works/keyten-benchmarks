"""Official Python SDK adapters. Loading and Arrow conversion are untimed."""
from __future__ import annotations

import hashlib
import math
import socket
import subprocess
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

EPOCH_NS = 946684800000000000
EPOCH_DAYS = 10957


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_port(process, port, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('server exited during startup; inspect server.log')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.2):
                return
        except OSError:
            time.sleep(.1)
    raise TimeoutError('server startup timed out; inspect server.log')


class Server:
    def close(self):
        try:
            if getattr(self, 'db', None) is not None:
                self.db.close()
        finally:
            if getattr(self, 'process', None) is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
                if self.process.stdin is not None:
                    self.process.stdin.close()
            if getattr(self, 'log', None) is not None:
                self.log.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class L(Server):
    name = 'l'
    mode = 'resident-memory'

    def __init__(self, binary, work, threads):
        import l
        self.sdk = l
        self.binary = Path(binary).resolve()
        self.identity = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        port = free_port()
        self.log = open(work / 'server.log', 'w')
        try:
            # -s counts secondary workers, in addition to the main thread.
            self.process = subprocess.Popen(
                [str(self.binary), '-q', '-p', str(port), '-s', str(threads - 1)],
                stdin=subprocess.PIPE, stdout=self.log, stderr=self.log)
            wait_port(self.process, port)
            self.db = l.connect('127.0.0.1', port)
            self.version = str(self.db.query('.z.K').to_python())
            from importlib.metadata import version
            self.client_version = version('l')
        except BaseException:
            self.close()
            raise

    def execute(self, query):
        return self.db.query(query)

    def _column(self, array):
        k = self.sdk.K
        t = array.type
        if pa.types.is_dictionary(t):
            array = pc.cast(array, t.value_type)
            t = array.type
        if pa.types.is_timestamp(t):
            ints = pc.cast(pc.cast(array, pa.timestamp('ns')), pa.int64()).to_pylist()
            return k.timestamp_vec([v - EPOCH_NS if v is not None else -(2**63) for v in ints])
        if pa.types.is_date(t):
            ints = pc.cast(pc.cast(array, pa.date32()), pa.int32()).to_pylist()
            return k.date_vec([v - EPOCH_DAYS if v is not None else -(2**31) for v in ints])
        if pa.types.is_duration(t) or pa.types.is_time(t):
            ints = pc.cast(array, pa.int64()).to_pylist()
            return k.timespan_vec([v if v is not None else -(2**63) for v in ints])
        values = array.to_pylist()
        if pa.types.is_boolean(t):
            if array.null_count:
                raise ValueError('l boolean vectors cannot preserve nulls')
            return k.bool_vec(values)
        if pa.types.is_integer(t):
            return k.long_vec([v if v is not None else -(2**63) for v in values])
        if pa.types.is_floating(t) or pa.types.is_decimal(t):
            return k.float_vec([float(v) if v is not None else math.nan for v in values])
        if pa.types.is_string(t) or pa.types.is_large_string(t):
            if array.null_count:
                raise ValueError('l symbols cannot distinguish null from an empty string')
            return k.symbol_vec(values)
        raise TypeError(f'unsupported l input type: {t}')

    def load(self, name, table):
        # Bounded IPC batches keep conversion outside the measured region.
        for batch in table.to_batches(max_chunksize=65536):
            value = self.sdk.K.table(self.sdk.K.dict(self.sdk.K.symbol_vec(batch.schema.names), self.sdk.K.list([self._column(c) for c in batch.columns])))
            self.db.query_with_args('{[n;t]n upsert t}', self.sdk.K.symbol(name), value)
        if not table.num_rows:
            value = self.sdk.K.table(self.sdk.K.dict(self.sdk.K.symbol_vec(table.column_names), self.sdk.K.list([self._column(c) for c in table.columns])))
            self.db.query_with_args('{[n;t]n set t}', self.sdk.K.symbol(name), value)
        actual = self.db.query(f'count {name}').to_python()
        if actual != table.num_rows:
            raise ValueError(f'{name}: loaded {actual} rows, expected {table.num_rows}')

    def query(self, query):
        # Return native K; converting to Python/Arrow belongs outside timing.
        return self.db.query('0!(' + query + ')')

    def arrow(self, result, query):
        values = result.to_python()
        types = self.db.query('type each value flip 0!(' + query + ')').to_python()
        columns = []
        for (name, data), tag in zip(values.items(), types):
            if tag in (5, 6, 7, 12, 13, 14, 16, 17, 18, 19):
                null = -(2**(63 if tag in (7, 12, 16) else 16 if tag == 5 else 31))
                if tag == 5:
                    null = -(2**15)
                data = [None if v == null else v for v in data]
            if tag in (8, 9):
                data = [None if math.isnan(v) else v for v in data]
            if tag == 12:
                col = pa.array([v + EPOCH_NS if v is not None else None for v in data], type=pa.timestamp('ns'))
            elif tag == 14:
                col = pa.array([v + EPOCH_DAYS if v is not None else None for v in data], type=pa.date32())
            elif tag == 16:
                col = pa.array(data, type=pa.duration('ns'))
            elif tag in (17, 18, 19):
                scale = {17: 60_000_000_000, 18: 1_000_000_000, 19: 1_000_000}[tag]
                col = pa.array([v * scale if v is not None else None for v in data], type=pa.time64('ns'))
            else:
                col = pa.array(data)
            columns.append(col)
        return pa.Table.from_arrays(columns, names=list(values))


class QuestDB(Server):
    name = 'questdb'
    mode = 'native-storage-warm'

    def __init__(self, binary, work, threads):
        import questdb
        self.sdk = questdb
        home = Path(binary).resolve()
        port = free_port()
        (work / 'conf').mkdir()
        (work / 'import').mkdir()
        self.work = work
        (work / 'conf/server.conf').write_text(
            f'http.bind.to=127.0.0.1:{port}\nhttp.min.enabled=false\n'
            f'pg.enabled=false\nline.tcp.enabled=false\nshared.query.worker.count={threads}\n'
            'shared.network.worker.count=1\nshared.write.worker.count=1\n'
            f'cairo.sql.copy.root={work / "import"}\n')
        self.log = open(work / 'server.log', 'w')
        try:
            self.process = subprocess.Popen(
                [str(home / 'bin/java'), '--enable-native-access=io.questdb',
                 '--add-opens=java.base/java.lang=io.questdb',
                 '--add-opens=java.base/java.nio=io.questdb',
                 '--add-exports=java.base/jdk.internal.vm=io.questdb', '-m', 'io.questdb/io.questdb.ServerMain', '-d', str(work)],
                stdout=self.log, stderr=self.log)
            wait_port(self.process, port)
            self.db = questdb.connect(f'ws::addr=127.0.0.1:{port};')
            self.version = str(self.query('select build()').column(0)[0].as_py())
            self.client_version = questdb.__version__
            self.identity = hashlib.sha256((home / 'lib/modules').read_bytes()).hexdigest()
        except BaseException:
            self.close()
            raise

    def execute(self, query):
        self.db.execute(query)

    def load(self, name, table):
        import pyarrow.parquet as pq
        # CTAS also loads dimension tables without inventing an ingestion timestamp.
        pq.write_table(table, self.work / 'import' / f'{name}.parquet')
        timestamp = next((c for c in ('ts', 'time', 'bound') if c in table.column_names and pa.types.is_timestamp(table[c].type)), None)
        suffix = f" ORDER BY {timestamp}) TIMESTAMP({timestamp})" if timestamp else ")"
        self.execute(f"CREATE TABLE {name} AS (SELECT * FROM read_parquet('{name}.parquet')" + suffix)
        columns = self.query(f'SELECT * FROM {name} LIMIT 0').column_names
        if columns != table.column_names:
            raise ValueError(f'{name}: Parquet import changed columns: {columns} != {table.column_names}')
        actual = self.query(f'SELECT count() FROM {name}').column(0)[0].as_py()
        if actual != table.num_rows:
            raise ValueError(f'{name}: loaded {actual} rows, expected {table.num_rows}')

    def query(self, query):
        with self.db.query(query) as result:
            return result.to_arrow()

    def arrow(self, result, query):
        return result
