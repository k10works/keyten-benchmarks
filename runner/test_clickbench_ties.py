"""Adversarial checks for the full-relation ClickBench correctness oracle."""
import unittest
import duckdb

from check_clickbench_results import _prepare_tie_oracle, _validate_tied_result


class TieValidationTests(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect()
        self.con.execute("CREATE TABLE hits (SearchPhrase VARCHAR, URL VARCHAR)")
        self.con.execute("INSERT INTO hits SELECT 'g' || i, 'google/' || i "
                         "FROM range(12) t(i), range(2)")
        self.sql = ("SELECT SearchPhrase, MIN(URL), COUNT(*) AS c FROM hits "
                    "WHERE URL LIKE '%google%' AND SearchPhrase <> '' "
                    "GROUP BY SearchPhrase ORDER BY c DESC LIMIT 10;")
        self.oracle = _prepare_tie_oracle(self.con, self.sql, 21)
        self.rows = [[f'g{i}', f'google/{i}', 2] for i in range(10)]

    def tearDown(self):
        self.con.close()

    def check(self, rows, oracle=None):
        _validate_tied_result(self.con, oracle or self.oracle, (['key', 'min', 'count'], rows))

    def test_different_legitimate_tied_groups_pass(self):
        self.check([[f'g{i}', f'google/{i}', 2] for i in range(2, 12)])

    def test_wrong_count_fails_despite_same_shape(self):
        self.rows[0][2] = 3
        with self.assertRaisesRegex(AssertionError, 'value mismatch'):
            self.check(self.rows)

    def test_wrong_non_sort_aggregate_fails(self):
        self.rows[0][1] = 'invented URL'
        with self.assertRaisesRegex(AssertionError, 'value mismatch'):
            self.check(self.rows)

    def test_nonexistent_group_fails(self):
        self.rows[0][0] = 'invented group'
        with self.assertRaisesRegex(AssertionError, 'group not present'):
            self.check(self.rows)

    def test_duplicate_group_fails(self):
        self.rows[1] = self.rows[0][:]
        with self.assertRaisesRegex(AssertionError, 'duplicate group'):
            self.check(self.rows)

    def test_wrong_rank_and_missing_winner_fail(self):
        self.con.execute("INSERT INTO hits VALUES ('g11', 'google/11')")
        oracle = _prepare_tie_oracle(self.con, self.sql, 21)
        with self.assertRaisesRegex(AssertionError, 'rank-window'):
            self.check(self.rows, oracle)
        self.check([['g11', 'google/11', 3]] + self.rows[:9], oracle)
        with self.assertRaisesRegex(AssertionError, 'rank-window'):
            self.check(self.rows[:9] + [['g11', 'google/11', 3]], oracle)

    def test_offset_window_and_boundary_ties(self):
        self.con.execute('CREATE TABLE ranks AS SELECT i::VARCHAR URL, '
                         'CASE WHEN i < 1000 THEN 3 ELSE 2 END PageViews FROM range(1012) t(i)')
        oracle = _prepare_tie_oracle(self.con,
            'SELECT URL, PageViews FROM ranks ORDER BY PageViews DESC LIMIT 10 OFFSET 1000;', 38)
        good = [[str(i), 2] for i in range(1002, 1012)]
        _validate_tied_result(self.con, oracle, (['URL', 'PageViews'], good))
        with self.assertRaisesRegex(AssertionError, 'rank-window'):
            _validate_tied_result(self.con, oracle, (['URL', 'PageViews'], [['0', 3]] + good[:9]))

    def test_unordered_still_checks_aggregates(self):
        self.con.execute('CREATE TABLE unordered AS SELECT i UserID, i::VARCHAR SearchPhrase, '
                         '2::BIGINT n FROM range(12) t(i)')
        oracle = _prepare_tie_oracle(self.con,
            'SELECT UserID, SearchPhrase, n FROM unordered LIMIT 10;', 17)
        rows = [[i, str(i), 2] for i in range(2, 12)]
        self.check(rows, oracle)
        rows[0][2] = 99
        with self.assertRaisesRegex(AssertionError, 'value mismatch'):
            self.check(rows, oracle)

    def test_null_key_and_float_tolerance(self):
        self.con.execute("CREATE TABLE avgs (CounterID BIGINT, l DOUBLE, c BIGINT)")
        self.con.execute("INSERT INTO avgs VALUES (NULL, 2.5, 100001), (1, 1.5, 100002)")
        oracle = _prepare_tie_oracle(self.con,
            'SELECT CounterID, l, c FROM avgs ORDER BY l DESC LIMIT 25;', 27)
        self.check([[None, 2.5 + 1e-9, 100001], [1, 1.5, 100002]], oracle)
        with self.assertRaisesRegex(AssertionError, 'value mismatch'):
            self.check([[None, 2.6, 100001], [1, 1.5, 100002]], oracle)

    def test_empty_window_and_wrong_shape(self):
        self.con.execute('DELETE FROM hits')
        oracle = _prepare_tie_oracle(self.con, self.sql, 21)
        self.check([], oracle)
        with self.assertRaisesRegex(AssertionError, 'row-count'):
            self.check(self.rows, oracle)
        with self.assertRaisesRegex(AssertionError, 'column-count'):
            _validate_tied_result(self.con, oracle, (['wrong'], []))

    def test_integer_aggregates_are_exact_even_at_large_magnitudes(self):
        self.con.execute("CREATE TABLE avgs (CounterID BIGINT, l DOUBLE, c BIGINT)")
        self.con.execute("INSERT INTO avgs VALUES (1, 2.5, 1000000000)")
        oracle = _prepare_tie_oracle(self.con,
            'SELECT CounterID, l, c FROM avgs ORDER BY l DESC LIMIT 25;', 27)
        with self.assertRaisesRegex(AssertionError, 'value mismatch'):
            self.check([[1, 2.5, 1000000001.0]], oracle)

    def test_changed_limit_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'unsupported SQL'):
            _prepare_tie_oracle(self.con, self.sql.replace('LIMIT 10', 'LIMIT 9'), 21)


if __name__ == '__main__':
    unittest.main()
