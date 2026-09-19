"""Small cross-engine semantic regressions; requires benchmark dependencies."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ClickBenchSemanticsTests(unittest.TestCase):
    def test_duckdb_loader_preserves_epoch_under_non_utc_host(self):
        import duckdb
        connection = duckdb.connect()
        connection.execute("SET TimeZone='Europe/Berlin'")
        # Execute the adapter's actual connection configuration and load SQL.
        tree = ast.parse((ROOT / 'runner/cb_duckdb.py').read_text())
        statements = [n.value for n in tree.body if isinstance(n, ast.Expr)
                      and isinstance(n.value, ast.Call)
                      and isinstance(n.value.func, ast.Attribute)
                      and isinstance(n.value.func.value, ast.Name)
                      and n.value.func.value.id == 'con'
                      and n.value.func.attr == 'execute']
        setup, load = statements[:2]
        connection.execute(ast.literal_eval(setup.args[0]))
        sql = ast.literal_eval(load.args[0]).split(' FROM read_parquet')[0]
        sql += ' FROM (SELECT 1372796926::BIGINT EventTime, 15888::INTEGER EventDate)'
        connection.execute(sql)
        self.assertEqual(connection.execute('SELECT epoch(EventTime) FROM hits').fetchone()[0], 1372796926)

    def test_url_average_uses_utf8_bytes(self):
        import duckdb
        import keyten as kt
        import polars as pl
        import pyarrow as pa
        table = pa.table({'CounterID': [1, 1, 2, 2, 2],
                          'URL': ['https://例子.org/é🙂', 'ascii', '', None, '🙂']})
        con = duckdb.connect()
        con.register('hits', table)
        sql = (ROOT / 'adapters/clickbench-duckdb-queries.sql').read_text().splitlines()[27]
        expected = con.execute(sql.replace('100000', '0')).fetchall()
        for engine, frame in [('keyten', kt.from_arrow(table).lazy()),
                              ('polars', pl.from_arrow(table).lazy())]:
            with self.subTest(engine=engine):
                expression = (ROOT / f'adapters/clickbench-{engine}/queries.sql').read_text().splitlines()[27]
                result = eval(expression.replace('100000', '0'), {'hits': frame, 'kt': kt, 'pl': pl})
                columns = result.to_dict() if engine == 'keyten' else result.to_dict(as_series=False)
                self.assertEqual(list(zip(*columns.values())), expected)

    def test_referer_group_matches_sql_for_www_and_unmatched_values(self):
        import duckdb
        import keyten as kt
        import polars as pl
        import pyarrow as pa
        values = ['http://www.example.com/a', 'https://example.com/b',
                  'http://example.com', 'android-app://example',
                  'https://other.org/x', 'https://例子.org/é🙂', '', None] * 3
        table = pa.table({'Referer': values})
        con = duckdb.connect()
        con.register('hits', table)
        sql = (ROOT / 'adapters/clickbench-duckdb-queries.sql').read_text().splitlines()[28]
        expected = con.execute(sql.replace('100000', '1')).fetchall()
        for engine, module, frame in [('keyten', kt, kt.from_arrow(table).lazy()),
                                      ('polars', pl, pl.from_arrow(table).lazy())]:
            with self.subTest(engine=engine):
                expression = (ROOT / f'adapters/clickbench-{engine}/queries.sql').read_text().splitlines()[28]
                result = eval(expression.replace('100000', '1'), {'hits': frame, 'kt': kt, 'pl': pl})
                columns = result.to_dict() if engine == 'keyten' else result.to_dict(as_series=False)
                actual = list(zip(*columns.values()))
                self.assertEqual(sorted(actual), sorted(expected))


if __name__ == '__main__':
    unittest.main()
